from __future__ import annotations

from sqlalchemy import text

from app.database import Base, register_models


REBUILD_SUFFIX = "__foreign_key_rebuild"


async def _table_exists(conn, table_name: str) -> bool:
    return (
        await conn.execute(
            text(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = :table_name"
            ),
            {"table_name": table_name},
        )
    ).scalar_one_or_none() is not None


async def legacy_foreign_key_tables(conn) -> list[str]:
    if conn.dialect.name != "sqlite":
        return []
    rows = (
        await conn.execute(
            text(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                  AND sql IS NOT NULL
                  AND instr(sql, 'REFERENCES') > 0
                  AND instr(sql, '_old') > 0
                ORDER BY name
                """
            )
        )
    ).scalars().all()
    return [str(row) for row in rows]


async def _drop_non_auto_indexes(conn, table_name: str) -> None:
    indexes = (await conn.execute(text(f'PRAGMA index_list("{table_name}")'))).fetchall()
    for index in indexes:
        index_name = str(index[1])
        if index_name and not index_name.startswith("sqlite_autoindex"):
            await conn.execute(text(f'DROP INDEX IF EXISTS "{index_name}"'))


async def _rebuild_from_metadata(conn, table_name: str) -> int:
    if int((await conn.execute(text("PRAGMA foreign_keys"))).scalar_one()):
        raise RuntimeError(
            "SQLite foreign-key repair requires the migration connection; "
            "run scripts/migrate_runtime_schema.py"
        )
    register_models()
    table = Base.metadata.tables.get(table_name)
    if table is None:
        raise RuntimeError(f"Cannot repair unknown table: {table_name}")
    actual_columns = [
        str(row[1])
        for row in (
            await conn.execute(text(f'PRAGMA table_info("{table_name}")'))
        ).fetchall()
    ]
    expected_columns = [column.name for column in table.columns]
    if set(actual_columns) != set(expected_columns):
        missing = sorted(set(expected_columns) - set(actual_columns))
        unexpected = sorted(set(actual_columns) - set(expected_columns))
        raise RuntimeError(
            f"Cannot repair {table_name}: runtime columns do not match model metadata; "
            f"missing={missing}; unexpected={unexpected}"
        )
    old_table_name = f"{table_name}{REBUILD_SUFFIX}"
    if await _table_exists(conn, old_table_name):
        raise RuntimeError(
            f"Cannot repair {table_name}: stale rebuild table {old_table_name} exists"
        )
    before_count = int(
        (await conn.execute(text(f'SELECT COUNT(*) FROM "{table_name}"'))).scalar_one()
    )
    columns_sql = ", ".join(f'"{column}"' for column in expected_columns)
    await conn.execute(text("PRAGMA legacy_alter_table=ON"))
    try:
        await conn.execute(
            text(f'ALTER TABLE "{table_name}" RENAME TO "{old_table_name}"')
        )
        await _drop_non_auto_indexes(conn, old_table_name)
        await conn.run_sync(lambda sync_conn: table.create(sync_conn, checkfirst=True))
        await conn.execute(
            text(
                f'INSERT INTO "{table_name}" ({columns_sql}) '
                f'SELECT {columns_sql} FROM "{old_table_name}"'
            )
        )
        after_count = int(
            (await conn.execute(text(f'SELECT COUNT(*) FROM "{table_name}"'))).scalar_one()
        )
        if after_count != before_count:
            raise RuntimeError(
                f"Cannot repair {table_name}: row count changed "
                f"from {before_count} to {after_count}"
            )
        await conn.execute(text(f'DROP TABLE "{old_table_name}"'))
    finally:
        await conn.execute(text("PRAGMA legacy_alter_table=OFF"))
    return before_count


async def _delete_orphan_source_cards(conn) -> int:
    if not await _table_exists(conn, "memoir_source_cards"):
        return 0
    result = await conn.execute(
        text(
            """
            DELETE FROM memoir_source_cards
            WHERE NOT EXISTS (
                SELECT 1
                FROM memoir_voice_utterances AS utterance
                WHERE utterance.id = memoir_source_cards.utterance_id
            )
               OR (
                    transcript_version_id IS NOT NULL
                    AND NOT EXISTS (
                        SELECT 1
                        FROM memoir_transcript_versions AS transcript
                        WHERE transcript.id = memoir_source_cards.transcript_version_id
                    )
               )
            """
        )
    )
    return int(result.rowcount or 0)


async def repair_sqlite_foreign_key_integrity(conn) -> dict[str, object]:
    if conn.dialect.name != "sqlite":
        return {"rebuilt_tables": [], "preserved_rows": {}, "deleted_derived_rows": {}}
    tables = await legacy_foreign_key_tables(conn)
    preserved_rows = {
        table_name: await _rebuild_from_metadata(conn, table_name)
        for table_name in tables
    }
    deleted_source_cards = await _delete_orphan_source_cards(conn)
    return {
        "rebuilt_tables": tables,
        "preserved_rows": preserved_rows,
        "deleted_derived_rows": {
            "memoir_source_cards": deleted_source_cards,
        },
    }


async def assert_sqlite_foreign_key_integrity(
    engine,
    *,
    require_enforcement: bool = True,
) -> None:
    if engine.dialect.name != "sqlite":
        return
    async with engine.connect() as conn:
        legacy_tables = await legacy_foreign_key_tables(conn)
        violations = (await conn.execute(text("PRAGMA foreign_key_check"))).fetchmany(20)
        enabled = int((await conn.execute(text("PRAGMA foreign_keys"))).scalar_one())
    details: list[str] = []
    if legacy_tables:
        details.append("legacy foreign-key targets: " + ", ".join(legacy_tables))
    if violations:
        details.append(
            "foreign-key violations: "
            + ", ".join(f"{row[0]}[{row[1]}]" for row in violations)
        )
    if require_enforcement and not enabled:
        details.append("SQLite foreign-key enforcement is disabled")
    if details:
        raise RuntimeError(
            "SQLite referential integrity mismatch; "
            + "; ".join(details)
            + "; run scripts/migrate_runtime_schema.py before starting the API"
        )
