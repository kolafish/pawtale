from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.database import Base, register_models
from app.migrations.schema_inspection import table_columns


RUNTIME_MANAGED_TABLES = {
    "memoir_voice_utterances_fts",
    "memoir_voice_utterances_fts_config",
    "memoir_voice_utterances_fts_data",
    "memoir_voice_utterances_fts_docsize",
    "memoir_voice_utterances_fts_idx",
}


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
            + "; the database must match the current schema before API startup"
        )
