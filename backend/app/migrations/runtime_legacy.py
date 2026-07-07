from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.migrations.schema_inspection import table_columns


async def migrate_legacy_runtime_schema(engine: AsyncEngine) -> dict[str, list[str]]:
    changed_columns: list[str] = []
    removed_columns: list[str] = []

    async with engine.begin() as conn:
        document_columns = await table_columns(conn, "documents")
        if "word_count" not in document_columns:
            await conn.execute(text("ALTER TABLE documents ADD COLUMN word_count INTEGER DEFAULT 0"))
            changed_columns.append("documents.word_count")

        project_columns = await table_columns(conn, "projects")
        if "type" not in project_columns:
            await conn.execute(text("ALTER TABLE projects ADD COLUMN type VARCHAR(20) NOT NULL DEFAULT 'novel'"))
            changed_columns.append("projects.type")
        if "metadata_json" not in project_columns:
            await conn.execute(text("ALTER TABLE projects ADD COLUMN metadata_json JSON NOT NULL DEFAULT '{}'"))
            changed_columns.append("projects.metadata_json")

        book_chapter_columns = await table_columns(conn, "memoir_book_plan_chapters")
        if "summary" in book_chapter_columns:
            await conn.execute(text("ALTER TABLE memoir_book_plan_chapters DROP COLUMN summary"))
            removed_columns.append("memoir_book_plan_chapters.summary")

        book_output_version_columns = await table_columns(conn, "memoir_book_output_versions")
        if "manuscript_json" not in book_output_version_columns:
            await conn.execute(
                text("ALTER TABLE memoir_book_output_versions ADD COLUMN manuscript_json JSON NOT NULL DEFAULT '{}'")
            )
            changed_columns.append("memoir_book_output_versions.manuscript_json")

    return {"changed_columns": changed_columns, "removed_columns": removed_columns}
