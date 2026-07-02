from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.database import Base, register_models
from app.migrations.audit import migrate_audit_schema
from app.migrations.auth import migrate_auth_schema
from app.migrations.git_sync import migrate_git_sync_schema
from app.migrations.memoir_semantic import migrate_memoir_semantic_schema
from app.migrations.memoir_voice import assert_memoir_voice_schema, migrate_memoir_voice_schema


async def _table_columns(conn, table: str) -> set[str]:
    result = await conn.execute(text(f"PRAGMA table_info({table})"))
    return {row[1] for row in result.fetchall()}


async def _table_exists(conn, table: str) -> bool:
    result = await conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = :table"),
        {"table": table},
    )
    return result.scalar_one_or_none() is not None


async def _migrate_legacy_runtime_schema(engine: AsyncEngine) -> dict[str, list[str]]:
    changed_columns: list[str] = []
    removed_columns: list[str] = []

    async with engine.begin() as conn:
        document_columns = await _table_columns(conn, "documents")
        if "word_count" not in document_columns:
            await conn.execute(text("ALTER TABLE documents ADD COLUMN word_count INTEGER DEFAULT 0"))
            changed_columns.append("documents.word_count")

        project_columns = await _table_columns(conn, "projects")
        if "type" not in project_columns:
            await conn.execute(text("ALTER TABLE projects ADD COLUMN type VARCHAR(20) NOT NULL DEFAULT 'novel'"))
            changed_columns.append("projects.type")
        if "metadata_json" not in project_columns:
            await conn.execute(text("ALTER TABLE projects ADD COLUMN metadata_json JSON NOT NULL DEFAULT '{}'"))
            changed_columns.append("projects.metadata_json")

        book_chapter_columns = await _table_columns(conn, "memoir_book_plan_chapters")
        if "summary" in book_chapter_columns:
            await conn.execute(text("ALTER TABLE memoir_book_plan_chapters DROP COLUMN summary"))
            removed_columns.append("memoir_book_plan_chapters.summary")

        book_output_version_columns = await _table_columns(conn, "memoir_book_output_versions")
        if "manuscript_json" not in book_output_version_columns:
            await conn.execute(
                text("ALTER TABLE memoir_book_output_versions ADD COLUMN manuscript_json JSON NOT NULL DEFAULT '{}'")
            )
            changed_columns.append("memoir_book_output_versions.manuscript_json")

    return {"changed_columns": changed_columns, "removed_columns": removed_columns}


async def _base_schema_mismatches(engine: AsyncEngine) -> dict[str, list[str]]:
    register_models()
    missing_tables: list[str] = []
    missing_columns: list[str] = []

    async with engine.begin() as conn:
        for table_name, table in sorted(Base.metadata.tables.items()):
            if not await _table_exists(conn, table_name):
                missing_tables.append(table_name)
                continue
            columns = await _table_columns(conn, table_name)
            for column in table.columns:
                if column.name not in columns:
                    missing_columns.append(f"{table_name}.{column.name}")

    return {"missing_tables": missing_tables, "missing_columns": missing_columns}


async def migrate_runtime_schema(engine: AsyncEngine) -> dict[str, object]:
    """Apply explicit schema migrations for the current application version."""
    register_models()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    legacy = await _migrate_legacy_runtime_schema(engine)
    auth = await migrate_auth_schema(engine)
    audit = await migrate_audit_schema(engine)
    git_sync = await migrate_git_sync_schema(engine)
    memoir_voice = await migrate_memoir_voice_schema(engine)
    memoir_semantic = await migrate_memoir_semantic_schema(engine)
    await assert_runtime_schema(engine)
    return {
        "legacy": legacy,
        "auth": auth,
        "audit": audit,
        "git_sync": git_sync,
        "memoir_voice": memoir_voice,
        "memoir_semantic": memoir_semantic,
    }


async def assert_runtime_schema(engine: AsyncEngine) -> None:
    """Validate runtime schema without creating tables or altering columns."""
    mismatches = await _base_schema_mismatches(engine)
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

    await assert_memoir_voice_schema(engine)
