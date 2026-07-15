from __future__ import annotations

from sqlalchemy import text

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
