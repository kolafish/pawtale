from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.schema import CreateIndex, CreateTable

from app.database import Base, register_models
from app.migrations.schema_inspection import table_columns, table_exists


OBSOLETE_RUNTIME_TABLES = (
    "characters",
    "scenes",
    "memoir_answers",
    "memoir_book_auto_revision_jobs",
    "memoir_chapter_event_cluster_snapshots",
    "memoir_coverage_snapshots",
    "memoir_coverage_topics",
    "memoir_drafts",
    "memoir_event_memories",
    "memoir_memory_evidence",
    "memoir_memory_links",
    "memoir_open_questions",
    "memoir_output_versions",
    "memoir_outputs",
    "memoir_person_memories",
    "memoir_place_memories",
    "memoir_question_candidates",
    "memoir_questions",
    "memoir_session_summaries",
    "memoir_sessions",
    "memoir_source_artifacts",
    "memoir_theme_memories",
)

RUNTIME_MANAGED_TABLES = {
    "memoir_voice_utterances_fts",
    "memoir_voice_utterances_fts_config",
    "memoir_voice_utterances_fts_data",
    "memoir_voice_utterances_fts_docsize",
    "memoir_voice_utterances_fts_idx",
}

EXACT_REBUILD_TABLES = (
    "memoir_voice_sessions",
    "memoir_transcript_versions",
)

LEGACY_DRAFT_BLOCK_TABLES = (
    "memoir_draft_block_claims",
    "memoir_draft_block_stories",
    "memoir_draft_block_versions",
    "memoir_draft_blocks",
)


async def _runtime_table_names(conn) -> set[str]:
    rows = (
        await conn.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        )
    ).scalars()
    return {str(name) for name in rows}


async def _drop_non_auto_indexes(conn, table_name: str) -> None:
    indexes = (
        await conn.execute(text(f'PRAGMA index_list("{table_name}")'))
    ).fetchall()
    for index in indexes:
        index_name = str(index[1])
        if index_name and not index_name.startswith("sqlite_autoindex"):
            await conn.execute(text(f'DROP INDEX IF EXISTS "{index_name}"'))


async def _rebuild_exact_table(conn, table_name: str) -> None:
    table = Base.metadata.tables[table_name]
    expected_columns = [column.name for column in table.columns]
    actual_columns = await table_columns(conn, table_name)
    missing_columns = set(expected_columns) - actual_columns
    if missing_columns:
        raise RuntimeError(
            f"Cannot rebuild {table_name}; missing current columns: "
            + ", ".join(sorted(missing_columns))
        )

    temporary_name = f"{table_name}__exact_schema"
    if await table_exists(conn, temporary_name):
        raise RuntimeError(
            f"Cannot rebuild {table_name}; stale table exists: {temporary_name}"
        )
    create_sql = str(CreateTable(table).compile(dialect=conn.dialect))
    create_sql = create_sql.replace(
        f"CREATE TABLE {table_name}",
        f'CREATE TABLE "{temporary_name}"',
        1,
    )
    columns_sql = ", ".join(f'"{column}"' for column in expected_columns)

    await conn.execute(text(create_sql))
    try:
        await conn.execute(
            text(
                f'INSERT INTO "{temporary_name}" ({columns_sql}) '
                f'SELECT {columns_sql} FROM "{table_name}"'
            )
        )
        await _drop_non_auto_indexes(conn, table_name)
        await conn.execute(text(f'DROP TABLE "{table_name}"'))
        await conn.execute(
            text(f'ALTER TABLE "{temporary_name}" RENAME TO "{table_name}"')
        )
        for index in sorted(table.indexes, key=lambda item: item.name or ""):
            await conn.execute(CreateIndex(index))
    except Exception:
        if await table_exists(conn, temporary_name):
            await conn.execute(text(f'DROP TABLE "{temporary_name}"'))
        raise


async def migrate_base_runtime_schema(engine: AsyncEngine) -> dict[str, list[str]]:
    """Remove structures that are not part of the current runtime model."""
    if engine.dialect.name != "sqlite":
        return {"dropped_tables": [], "rebuilt_tables": [], "reset_tables": []}

    register_models()
    dropped_tables: list[str] = []
    rebuilt_tables: list[str] = []
    reset_tables: list[str] = []
    async with engine.begin() as conn:
        if int((await conn.execute(text("PRAGMA foreign_keys"))).scalar_one()):
            raise RuntimeError(
                "Exact SQLite cleanup requires the migration connection; "
                "run scripts/migrate_runtime_schema.py"
            )
        existing_tables = await _runtime_table_names(conn)
        for table_name in OBSOLETE_RUNTIME_TABLES:
            if table_name not in existing_tables:
                continue
            await conn.execute(text(f'DROP TABLE "{table_name}"'))
            dropped_tables.append(table_name)

        if await table_exists(conn, "memoir_draft_blocks") and "legacy_draft_id" in (
            await table_columns(conn, "memoir_draft_blocks")
        ):
            for table_name in LEGACY_DRAFT_BLOCK_TABLES:
                if not await table_exists(conn, table_name):
                    continue
                await conn.execute(text(f'DROP TABLE "{table_name}"'))
                reset_tables.append(table_name)

        for table_name in EXACT_REBUILD_TABLES:
            if not await table_exists(conn, table_name):
                continue
            expected_columns = set(Base.metadata.tables[table_name].columns.keys())
            if await table_columns(conn, table_name) == expected_columns:
                continue
            await _rebuild_exact_table(conn, table_name)
            rebuilt_tables.append(table_name)

    return {
        "dropped_tables": dropped_tables,
        "rebuilt_tables": rebuilt_tables,
        "reset_tables": reset_tables,
    }


async def base_schema_mismatches(engine: AsyncEngine) -> dict[str, list[str]]:
    register_models()
    missing_tables: list[str] = []
    unexpected_tables: list[str] = []
    missing_columns: list[str] = []
    unexpected_columns: list[str] = []

    async with engine.begin() as conn:
        actual_tables = await _runtime_table_names(conn)
        expected_tables = set(Base.metadata.tables)
        unexpected_tables.extend(
            sorted(actual_tables - expected_tables - RUNTIME_MANAGED_TABLES)
        )
        for table_name, table in sorted(Base.metadata.tables.items()):
            if table_name not in actual_tables:
                missing_tables.append(table_name)
                continue
            columns = await table_columns(conn, table_name)
            expected_columns = set(table.columns.keys())
            missing_columns.extend(
                f"{table_name}.{column}"
                for column in sorted(expected_columns - columns)
            )
            unexpected_columns.extend(
                f"{table_name}.{column}"
                for column in sorted(columns - expected_columns)
            )

    return {
        "missing_tables": missing_tables,
        "unexpected_tables": unexpected_tables,
        "missing_columns": missing_columns,
        "unexpected_columns": unexpected_columns,
    }


async def assert_base_runtime_schema(engine: AsyncEngine) -> None:
    mismatches = await base_schema_mismatches(engine)
    details: list[str] = []
    for key, label in (
        ("missing_tables", "missing tables"),
        ("unexpected_tables", "unexpected tables"),
        ("missing_columns", "missing columns"),
        ("unexpected_columns", "unexpected columns"),
    ):
        values = mismatches[key]
        if values:
            details.append(f"{label}: {', '.join(values)}")
    if details:
        raise RuntimeError(
            "Runtime schema mismatch; "
            + "; ".join(details)
            + "; run `PYTHONPATH=backend python backend/scripts/migrate_runtime_schema.py` before starting the API"
        )
