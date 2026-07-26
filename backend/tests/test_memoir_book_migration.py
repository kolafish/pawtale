import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.database import register_models
from app.migrations.memoir_schema_cutover import (
    OBSOLETE_MEMOIR_TABLES,
    migrate_memoir_schema_cutover,
)
from app.migrations.runtime import assert_runtime_schema, migrate_runtime_schema


async def _table_columns(conn, table_name: str) -> set[str]:
    rows = (
        await conn.execute(text(f"PRAGMA table_info({table_name})"))
    ).fetchall()
    return {row[1] for row in rows}


async def _table_names(conn) -> set[str]:
    rows = (
        await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type = 'table'")
        )
    ).fetchall()
    return {row[0] for row in rows}


async def test_runtime_schema_assert_rejects_extra_columns_without_mutating():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        await migrate_runtime_schema(engine)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "ALTER TABLE memoir_book_plan_chapters "
                    "ADD COLUMN summary TEXT NOT NULL DEFAULT ''"
                )
            )

        with pytest.raises(RuntimeError, match="unexpected columns"):
            await assert_runtime_schema(
                engine,
                require_sqlite_foreign_keys=False,
            )

        async with engine.begin() as conn:
            assert "summary" in await _table_columns(
                conn,
                "memoir_book_plan_chapters",
            )
            output_columns = await _table_columns(
                conn,
                "memoir_book_outputs",
            )
        assert "formal_version_id" not in output_columns
        assert "formal_audit_job_id" not in output_columns
    finally:
        await engine.dispose()


async def test_runtime_schema_assert_rejects_unexpected_tables():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        await migrate_runtime_schema(engine)
        async with engine.begin() as conn:
            await conn.execute(
                text("CREATE TABLE obsolete_runtime_data (id TEXT)")
            )

        with pytest.raises(RuntimeError, match="unexpected tables"):
            await assert_runtime_schema(
                engine,
                require_sqlite_foreign_keys=False,
            )
        async with engine.begin() as conn:
            assert "obsolete_runtime_data" in await _table_names(conn)
    finally:
        await engine.dispose()


async def test_runtime_schema_assert_rejects_disabled_foreign_keys():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        await migrate_runtime_schema(engine)
        with pytest.raises(
            RuntimeError,
            match="foreign-key enforcement is disabled",
        ):
            await assert_runtime_schema(engine)
    finally:
        await engine.dispose()


async def test_runtime_schema_assert_reports_missing_columns():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        await migrate_runtime_schema(engine)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "ALTER TABLE memoir_book_output_versions "
                    "DROP COLUMN manuscript_json"
                )
            )

        with pytest.raises(
            RuntimeError,
            match="memoir_book_output_versions.manuscript_json",
        ):
            await assert_runtime_schema(engine)
    finally:
        await engine.dispose()


async def test_runtime_schema_migration_creates_book_completion_jobs():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        await migrate_runtime_schema(engine)
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE memoir_book_completion_jobs"))

        await migrate_runtime_schema(engine)

        async with engine.begin() as conn:
            assert "memoir_book_completion_jobs" in await _table_names(conn)
            columns = await _table_columns(conn, "memoir_book_completion_jobs")
        assert {
            "project_id",
            "active_key",
            "target_chapters_json",
            "chapter_job_ids_json",
            "enqueue_errors_json",
        } <= columns
        await assert_runtime_schema(
            engine,
            require_sqlite_foreign_keys=False,
        )
    finally:
        await engine.dispose()


async def test_cutover_drops_all_known_legacy_tables():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        await migrate_runtime_schema(engine)
        sample_tables = {
            "memoir_claims",
            "memoir_stories",
            "memoir_completion_items",
            "memoir_source_cards",
            "memoir_chapter_source_links",
        }
        assert sample_tables <= set(OBSOLETE_MEMOIR_TABLES)
        async with engine.begin() as conn:
            for table_name in sample_tables:
                await conn.execute(
                    text(f"CREATE TABLE {table_name} (id TEXT)")
                )
                await conn.execute(
                    text(f"INSERT INTO {table_name} (id) VALUES ('legacy')")
                )

        result = await migrate_memoir_schema_cutover(engine)

        async with engine.begin() as conn:
            names = await _table_names(conn)
        assert sample_tables.isdisjoint(names)
        assert sample_tables <= set(result["dropped_tables"])
        assert "memoir_voice_sessions" in names
        assert "memoir_section_materials" in names
    finally:
        await engine.dispose()


async def test_cutover_leaves_unknown_table_for_schema_assertion():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        await migrate_runtime_schema(engine)
        async with engine.begin() as conn:
            await conn.execute(
                text("CREATE TABLE unknown_memoir_table (id TEXT)")
            )
        await migrate_memoir_schema_cutover(engine)
        async with engine.begin() as conn:
            assert "unknown_memoir_table" in await _table_names(conn)
    finally:
        await engine.dispose()
