import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.database import register_models
from app.migrations.runtime import assert_runtime_schema, migrate_runtime_schema


async def _table_columns(conn, table_name: str) -> set[str]:
    rows = (await conn.execute(text(f"PRAGMA table_info({table_name})"))).fetchall()
    return {row[1] for row in rows}


async def test_runtime_schema_assert_does_not_remove_extra_columns():
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
            output_columns = await _table_columns(conn, "memoir_book_outputs")

        assert "summary" in columns
        assert {
            "formal_version_id",
            "formal_audit_job_id",
            "formalized_by_user_id",
            "formalized_at",
        }.issubset(output_columns)
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
