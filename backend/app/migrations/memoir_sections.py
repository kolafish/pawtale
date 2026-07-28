from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncEngine

from app.database import Base, register_models
from app.migrations.schema_inspection import table_exists


MEMOIR_SECTION_TABLES = (
    "memoir_sections",
    "memoir_section_materials",
    "memoir_section_digests",
)

_REQUIRED_COLUMNS = {
    "memoir_sections": {
        "id",
        "project_id",
        "chapter_id",
        "title",
        "section_type",
        "sort_order",
        "status",
        "include_in_book",
        "version",
        "metadata_json",
        "created_at",
        "updated_at",
    },
    "memoir_section_materials": {
        "id",
        "project_id",
        "section_id",
        "utterance_id",
        "transcript_version_id",
        "sort_order",
        "assigned_by",
        "assignment_confidence",
        "assignment_reason",
        "include_in_book",
        "created_at",
        "updated_at",
    },
    "memoir_section_digests": {
        "id",
        "project_id",
        "section_id",
        "input_signature",
        "engine",
        "status",
        "digest_json",
        "error_message",
        "created_at",
        "updated_at",
    },
}


async def _create_metadata_table(conn, table_name: str) -> None:
    register_models()
    table = Base.metadata.tables[table_name]
    await conn.run_sync(lambda sync_conn: table.create(sync_conn, checkfirst=True))


async def migrate_memoir_section_schema(
    engine: AsyncEngine,
) -> dict[str, list[str]]:
    changed_tables: list[str] = []
    async with engine.begin() as conn:
        for table_name in MEMOIR_SECTION_TABLES:
            if not await table_exists(conn, table_name):
                await _create_metadata_table(conn, table_name)
                changed_tables.append(table_name)
    return {"changed_tables": changed_tables, "changed_columns": []}


async def assert_memoir_section_schema(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        for table_name, expected_columns in _REQUIRED_COLUMNS.items():
            columns = await conn.run_sync(
                lambda sync_conn, name=table_name: {
                    column["name"] for column in inspect(sync_conn).get_columns(name)
                }
            )
            missing = expected_columns - columns
            if missing:
                missing_names = ", ".join(sorted(missing))
                raise RuntimeError(
                    f"{table_name} is missing required columns: {missing_names}"
                )
