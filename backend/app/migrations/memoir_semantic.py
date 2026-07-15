from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.database import Base, register_models


MEMOIR_SEMANTIC_TABLES = (
    "memoir_source_cards",
    "memoir_chapter_cards",
    "memoir_book_quality_audit_snapshots",
    "memoir_chapter_source_links",
)


async def _table_exists(conn, table: str) -> bool:
    result = await conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = :table"),
        {"table": table},
    )
    return result.scalar_one_or_none() is not None


async def _create_metadata_table(conn, table_name: str) -> None:
    register_models()
    table = Base.metadata.tables[table_name]
    await conn.run_sync(lambda sync_conn: table.create(sync_conn, checkfirst=True))


async def migrate_memoir_semantic_schema(engine: AsyncEngine) -> dict[str, list[str]]:
    """Create derived memoir semantic tables for existing SQLite databases."""
    changed_tables: list[str] = []
    changed_columns: list[str] = []
    async with engine.begin() as conn:
        for table_name in MEMOIR_SEMANTIC_TABLES:
            if not await _table_exists(conn, table_name):
                await _create_metadata_table(conn, table_name)
                changed_tables.append(table_name)

    return {"changed_tables": changed_tables, "changed_columns": changed_columns}
