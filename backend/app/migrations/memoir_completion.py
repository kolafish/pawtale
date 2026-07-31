from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncEngine

from app.database import Base, register_models
from app.migrations.schema_inspection import table_exists


TABLE_NAME = "memoir_completion_issues"
REQUIRED_COLUMNS = {
    "id",
    "project_id",
    "scope",
    "chapter_id",
    "issue_type",
    "channel",
    "question",
    "reason",
    "priority",
    "fingerprint",
    "related_material_ids_json",
    "input_signature",
    "status",
    "outcome",
    "outcome_note",
    "answered_material_id",
    "started_at",
    "closed_at",
    "created_at",
    "updated_at",
}


async def migrate_memoir_completion_schema(
    engine: AsyncEngine,
) -> dict[str, object]:
    changed_tables: list[str] = []
    async with engine.begin() as conn:
        if not await table_exists(conn, TABLE_NAME):
            register_models()
            table = Base.metadata.tables[TABLE_NAME]
            await conn.run_sync(
                lambda sync_conn: table.create(sync_conn, checkfirst=True)
            )
            changed_tables.append(TABLE_NAME)
    return {
        "changed_tables": changed_tables,
        "changed_columns": [],
    }


async def assert_memoir_completion_schema(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        columns = await conn.run_sync(
            lambda sync_conn: {
                column["name"]
                for column in inspect(sync_conn).get_columns(TABLE_NAME)
            }
        )
        missing = REQUIRED_COLUMNS - columns
        if missing:
            missing_names = ", ".join(sorted(missing))
            raise RuntimeError(
                f"{TABLE_NAME} is missing required columns: {missing_names}"
            )
