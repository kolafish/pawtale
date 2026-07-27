from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.database import Base, register_models


TABLE_NAME = "memoir_completion_issues"
REQUIRED_COLUMNS = {
    "id",
    "project_id",
    "scope",
    "section_id",
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


async def _table_exists(conn) -> bool:
    result = await conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = :table"),
        {"table": TABLE_NAME},
    )
    return result.scalar_one_or_none() is not None


async def migrate_memoir_completion_schema(
    engine: AsyncEngine,
) -> dict[str, object]:
    changed_tables: list[str] = []
    superseded_section_issues = 0
    async with engine.begin() as conn:
        if not await _table_exists(conn):
            register_models()
            table = Base.metadata.tables[TABLE_NAME]
            await conn.run_sync(
                lambda sync_conn: table.create(sync_conn, checkfirst=True)
            )
            changed_tables.append(TABLE_NAME)
        else:
            result = await conn.execute(
                text(
                    f"UPDATE {TABLE_NAME} SET status = 'closed', "
                    "outcome = 'superseded', "
                    "outcome_note = '小节发现改由章节审阅统一筛选', "
                    "closed_at = CURRENT_TIMESTAMP, "
                    "updated_at = CURRENT_TIMESTAMP "
                    "WHERE scope = 'section' AND status != 'closed'"
                )
            )
            superseded_section_issues = result.rowcount
    return {
        "changed_tables": changed_tables,
        "changed_columns": [],
        "superseded_section_issues": superseded_section_issues,
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
