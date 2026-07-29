from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.migrations.schema_inspection import table_columns, table_exists


async def migrate_project_schema(engine: AsyncEngine) -> dict[str, list[str]]:
    changed_columns: list[str] = []
    async with engine.begin() as conn:
        if await table_exists(conn, "documents"):
            columns = await table_columns(conn, "documents")
            if "content_revision" not in columns:
                await conn.execute(
                    text(
                        "ALTER TABLE documents ADD COLUMN "
                        "content_revision INTEGER NOT NULL DEFAULT 0"
                    )
                )
                changed_columns.append("documents.content_revision")
    return {"changed_columns": changed_columns}


async def assert_project_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        if "content_revision" not in await table_columns(conn, "documents"):
            raise RuntimeError(
                "Runtime schema mismatch; missing columns: "
                "documents.content_revision; run the runtime schema migration"
            )
