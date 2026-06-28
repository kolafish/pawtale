from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

import app.main as app_main
from app.database import Base, register_models


async def test_auto_migrate_drops_legacy_memoir_book_chapter_summary(monkeypatch):
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(
                text("ALTER TABLE memoir_book_plan_chapters ADD COLUMN summary TEXT NOT NULL DEFAULT ''")
            )
            columns = {
                row[1]
                for row in (
                    await conn.execute(text("PRAGMA table_info(memoir_book_plan_chapters)"))
                ).fetchall()
            }
            assert "summary" in columns

        monkeypatch.setattr(app_main, "engine", engine)

        await app_main._auto_migrate()
        await app_main._auto_migrate()

        async with engine.begin() as conn:
            columns = {
                row[1]
                for row in (
                    await conn.execute(text("PRAGMA table_info(memoir_book_plan_chapters)"))
                ).fetchall()
            }

        assert "summary" not in columns
    finally:
        await engine.dispose()


async def test_auto_migrate_adds_memoir_book_output_version_manuscript_json(monkeypatch):
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(text("ALTER TABLE memoir_book_output_versions DROP COLUMN manuscript_json"))
            columns = {
                row[1]
                for row in (
                    await conn.execute(text("PRAGMA table_info(memoir_book_output_versions)"))
                ).fetchall()
            }
            assert "manuscript_json" not in columns

        monkeypatch.setattr(app_main, "engine", engine)

        await app_main._auto_migrate()
        await app_main._auto_migrate()

        async with engine.begin() as conn:
            columns = {
                row[1]
                for row in (
                    await conn.execute(text("PRAGMA table_info(memoir_book_output_versions)"))
                ).fetchall()
            }

        assert "manuscript_json" in columns
    finally:
        await engine.dispose()
