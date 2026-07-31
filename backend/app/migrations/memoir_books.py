from __future__ import annotations

from sqlalchemy import inspect, text
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
        "phase",
        "revision_plan_json",
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
    changed_columns: list[str] = []
    async with engine.begin() as conn:
        register_models()
        tables = await conn.run_sync(lambda sync_conn: set(inspect(sync_conn).get_table_names()))
        for table_name in REQUIRED_TABLE_COLUMNS:
            if table_name not in tables:
                await conn.run_sync(
                    lambda sync_conn, name=table_name: Base.metadata.tables[name].create(
                        sync_conn,
                        checkfirst=True,
                    )
                )
                changed_tables.append(table_name)
        proposal_table = "memoir_book_revision_proposals"
        if proposal_table in tables and proposal_table not in changed_tables:
            columns = await conn.run_sync(
                lambda sync_conn: {column["name"] for column in inspect(sync_conn).get_columns(proposal_table)}
            )
            additions = {
                "phase": "VARCHAR(20) NOT NULL DEFAULT 'draft_ready'",
                "revision_plan_json": "JSON NOT NULL DEFAULT '{}'",
            }
            for column_name, definition in additions.items():
                if column_name in columns:
                    continue
                await conn.execute(text(f'ALTER TABLE "{proposal_table}" ADD COLUMN "{column_name}" {definition}'))
                changed_columns.append(f"{proposal_table}.{column_name}")
    return {
        "changed_tables": changed_tables,
        "changed_columns": changed_columns,
    }


async def assert_memoir_book_schema(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        tables = await conn.run_sync(lambda sync_conn: set(inspect(sync_conn).get_table_names()))
        for table_name, required_columns in REQUIRED_TABLE_COLUMNS.items():
            if table_name not in tables:
                raise RuntimeError(f"{table_name} is missing")
            columns = await conn.run_sync(
                lambda sync_conn, name=table_name: {column["name"] for column in inspect(sync_conn).get_columns(name)}
            )
            missing = required_columns - columns
            if missing:
                raise RuntimeError(f"{table_name} is missing required columns: " + ", ".join(sorted(missing)))
