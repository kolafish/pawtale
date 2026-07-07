from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine

from app.database import Base, register_models
from app.migrations.schema_inspection import table_columns, table_exists


async def base_schema_mismatches(engine: AsyncEngine) -> dict[str, list[str]]:
    register_models()
    missing_tables: list[str] = []
    missing_columns: list[str] = []

    async with engine.begin() as conn:
        for table_name, table in sorted(Base.metadata.tables.items()):
            if not await table_exists(conn, table_name):
                missing_tables.append(table_name)
                continue
            columns = await table_columns(conn, table_name)
            for column in table.columns:
                if column.name not in columns:
                    missing_columns.append(f"{table_name}.{column.name}")

    return {"missing_tables": missing_tables, "missing_columns": missing_columns}


async def assert_base_runtime_schema(engine: AsyncEngine) -> None:
    mismatches = await base_schema_mismatches(engine)
    missing_tables = mismatches["missing_tables"]
    missing_columns = mismatches["missing_columns"]
    if missing_tables or missing_columns:
        details: list[str] = []
        if missing_tables:
            details.append(f"missing tables: {', '.join(missing_tables)}")
        if missing_columns:
            details.append(f"missing columns: {', '.join(missing_columns)}")
        raise RuntimeError(
            "Runtime schema mismatch; "
            + "; ".join(details)
            + "; run `PYTHONPATH=backend python backend/scripts/migrate_runtime_schema.py` before starting the API"
        )
