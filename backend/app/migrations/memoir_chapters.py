from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.database import Base, register_models


CHAPTER_DIGEST_TABLE = "memoir_chapter_digests"
DRAFT_VERSION_TABLE = "memoir_book_chapter_draft_versions"
_DIGEST_COLUMNS = {
    "id",
    "project_id",
    "chapter_id",
    "input_signature",
    "engine",
    "status",
    "digest_json",
    "error_message",
    "created_at",
    "updated_at",
}
_DRAFT_COLUMNS = {
    "input_signature",
    "used_material_ids_json",
    "material_snapshot_json",
    "section_snapshot_json",
}


async def _columns(conn, table_name: str) -> set[str]:
    return await conn.run_sync(
        lambda sync_conn: {
            column["name"]
            for column in inspect(sync_conn).get_columns(table_name)
        }
    )


async def migrate_memoir_chapter_schema(
    engine: AsyncEngine,
) -> dict[str, list[str]]:
    changed_tables: list[str] = []
    changed_columns: list[str] = []
    async with engine.begin() as conn:
        register_models()
        tables = await conn.run_sync(
            lambda sync_conn: set(inspect(sync_conn).get_table_names())
        )
        if CHAPTER_DIGEST_TABLE not in tables:
            await conn.run_sync(
                lambda sync_conn: Base.metadata.tables[
                    CHAPTER_DIGEST_TABLE
                ].create(sync_conn, checkfirst=True)
            )
            changed_tables.append(CHAPTER_DIGEST_TABLE)

        draft_columns = await _columns(conn, DRAFT_VERSION_TABLE)
        additions = {
            "input_signature": (
                "VARCHAR(128) NOT NULL DEFAULT ''"
            ),
            "used_material_ids_json": (
                "JSON NOT NULL DEFAULT '[]'"
            ),
            "material_snapshot_json": (
                "JSON NOT NULL DEFAULT '[]'"
            ),
            "section_snapshot_json": (
                "JSON NOT NULL DEFAULT '[]'"
            ),
        }
        for column_name, definition in additions.items():
            if column_name in draft_columns:
                continue
            await conn.execute(
                text(
                    f'ALTER TABLE "{DRAFT_VERSION_TABLE}" '
                    f'ADD COLUMN "{column_name}" {definition}'
                )
            )
            changed_columns.append(f"{DRAFT_VERSION_TABLE}.{column_name}")
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS "
                "ix_memoir_book_chapter_draft_versions_input_signature "
                "ON memoir_book_chapter_draft_versions (input_signature)"
            )
        )
    return {
        "changed_tables": changed_tables,
        "changed_columns": changed_columns,
    }


async def assert_memoir_chapter_schema(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        digest_columns = await _columns(conn, CHAPTER_DIGEST_TABLE)
        missing_digest = _DIGEST_COLUMNS - digest_columns
        if missing_digest:
            raise RuntimeError(
                "memoir_chapter_digests is missing required columns: "
                + ", ".join(sorted(missing_digest))
            )
        draft_columns = await _columns(conn, DRAFT_VERSION_TABLE)
        missing_draft = _DRAFT_COLUMNS - draft_columns
        if missing_draft:
            raise RuntimeError(
                f"{DRAFT_VERSION_TABLE} is missing required columns: "
                + ", ".join(sorted(missing_draft))
            )
