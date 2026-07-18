from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncEngine

from app.database import Base, register_models


TABLE_NAME = "memoir_book_reviews"
REQUIRED_COLUMNS = {
    "id",
    "project_id",
    "input_signature",
    "engine",
    "status",
    "review_json",
    "error_message",
    "created_at",
    "updated_at",
}


async def migrate_memoir_book_schema(
    engine: AsyncEngine,
) -> dict[str, list[str]]:
    changed_tables: list[str] = []
    async with engine.begin() as conn:
        register_models()
        tables = await conn.run_sync(
            lambda sync_conn: set(inspect(sync_conn).get_table_names())
        )
        if TABLE_NAME not in tables:
            await conn.run_sync(
                lambda sync_conn: Base.metadata.tables[TABLE_NAME].create(
                    sync_conn,
                    checkfirst=True,
                )
            )
            changed_tables.append(TABLE_NAME)
    return {"changed_tables": changed_tables, "changed_columns": []}


async def assert_memoir_book_schema(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        columns = await conn.run_sync(
            lambda sync_conn: {
                column["name"]
                for column in inspect(sync_conn).get_columns(TABLE_NAME)
            }
        )
    missing = REQUIRED_COLUMNS - columns
    if missing:
        raise RuntimeError(
            f"{TABLE_NAME} is missing required columns: "
            + ", ".join(sorted(missing))
        )
