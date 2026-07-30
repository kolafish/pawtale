from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncEngine

from app.database import Base, register_models


REQUIRED_TABLE_COLUMNS = {
    "memoir_book_reviews": {
        "id",
        "project_id",
        "input_signature",
        "engine",
        "status",
        "review_json",
        "error_message",
        "created_at",
        "updated_at",
    },
    "memoir_book_revision_proposals": {
        "id",
        "project_id",
        "review_id",
        "review_input_signature",
        "base_output_version_id",
        "status",
        "selected_actions_json",
        "candidate_chapter_snapshot_json",
        "comparison_json",
        "generation_json",
        "summary",
        "result_output_version_id",
        "created_at",
        "updated_at",
    },
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
        for table_name in REQUIRED_TABLE_COLUMNS:
            if table_name not in tables:
                await conn.run_sync(
                    lambda sync_conn, name=table_name: Base.metadata.tables[name].create(
                        sync_conn,
                        checkfirst=True,
                    )
                )
                changed_tables.append(table_name)
    return {"changed_tables": changed_tables, "changed_columns": []}


async def assert_memoir_book_schema(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        tables = await conn.run_sync(
            lambda sync_conn: set(inspect(sync_conn).get_table_names())
        )
        for table_name, required_columns in REQUIRED_TABLE_COLUMNS.items():
            if table_name not in tables:
                raise RuntimeError(f"{table_name} is missing")
            columns = await conn.run_sync(
                lambda sync_conn, name=table_name: {
                    column["name"]
                    for column in inspect(sync_conn).get_columns(name)
                }
            )
            missing = required_columns - columns
            if missing:
                raise RuntimeError(
                    f"{table_name} is missing required columns: "
                    + ", ".join(sorted(missing))
                )
