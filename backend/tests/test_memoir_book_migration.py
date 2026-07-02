import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.database import Base, register_models
from app.migrations.runtime import assert_runtime_schema, migrate_runtime_schema


async def _table_columns(conn, table_name: str) -> set[str]:
    rows = (await conn.execute(text(f"PRAGMA table_info({table_name})"))).fetchall()
    return {row[1] for row in rows}


async def test_runtime_migration_drops_legacy_memoir_book_chapter_summary():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(
                text("ALTER TABLE memoir_book_plan_chapters ADD COLUMN summary TEXT NOT NULL DEFAULT ''")
            )
            assert "summary" in await _table_columns(conn, "memoir_book_plan_chapters")

        await migrate_runtime_schema(engine)
        await migrate_runtime_schema(engine)

        async with engine.begin() as conn:
            columns = await _table_columns(conn, "memoir_book_plan_chapters")

        assert "summary" not in columns
    finally:
        await engine.dispose()


async def test_runtime_migration_adds_memoir_book_output_version_manuscript_json():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(text("ALTER TABLE memoir_book_output_versions DROP COLUMN manuscript_json"))
            assert "manuscript_json" not in await _table_columns(conn, "memoir_book_output_versions")

        await migrate_runtime_schema(engine)
        await migrate_runtime_schema(engine)

        async with engine.begin() as conn:
            columns = await _table_columns(conn, "memoir_book_output_versions")

        assert "manuscript_json" in columns
    finally:
        await engine.dispose()


async def test_runtime_schema_assert_does_not_remove_legacy_columns():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        await migrate_runtime_schema(engine)
        async with engine.begin() as conn:
            await conn.execute(
                text("ALTER TABLE memoir_book_plan_chapters ADD COLUMN summary TEXT NOT NULL DEFAULT ''")
            )

        await assert_runtime_schema(engine)

        async with engine.begin() as conn:
            columns = await _table_columns(conn, "memoir_book_plan_chapters")

        assert "summary" in columns
    finally:
        await engine.dispose()


async def test_runtime_schema_assert_reports_missing_columns_without_mutating_schema():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        await migrate_runtime_schema(engine)
        async with engine.begin() as conn:
            await conn.execute(text("ALTER TABLE memoir_book_output_versions DROP COLUMN manuscript_json"))

        with pytest.raises(RuntimeError, match="memoir_book_output_versions.manuscript_json"):
            await assert_runtime_schema(engine)

        async with engine.begin() as conn:
            columns = await _table_columns(conn, "memoir_book_output_versions")

        assert "manuscript_json" not in columns
    finally:
        await engine.dispose()
