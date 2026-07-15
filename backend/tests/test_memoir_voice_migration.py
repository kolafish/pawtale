import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.database import Base, register_models
from app.migrations.memoir_voice import (
    TRANSCRIPT_FTS_TABLE,
    TRANSCRIPT_FTS_TRIGGERS,
    assert_memoir_voice_schema,
    describe_memoir_voice_schema,
    expected_memoir_voice_schema,
    migrate_memoir_voice_schema,
    rebuild_memoir_voice_schema,
)


async def test_memoir_voice_migration_only_installs_runtime_managed_fts():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        first = await migrate_memoir_voice_schema(engine)
        second = await migrate_memoir_voice_schema(engine)

        assert first["changed_tables"] == [TRANSCRIPT_FTS_TABLE]
        assert first["changed_triggers"] == list(TRANSCRIPT_FTS_TRIGGERS)
        assert first["changed_columns"] == []
        assert first["changed_rows"] == []
        assert second["changed_tables"] == []
        assert second["changed_triggers"] == []
        await assert_memoir_voice_schema(engine)
    finally:
        await engine.dispose()


async def test_memoir_voice_migration_rejects_partial_legacy_schema():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE TABLE memoir_voice_utterances "
                    "(id VARCHAR(36) PRIMARY KEY)"
                )
            )

        with pytest.raises(RuntimeError, match="Missing required tables"):
            await migrate_memoir_voice_schema(engine)
    finally:
        await engine.dispose()


async def test_memoir_voice_schema_rejects_unexpected_legacy_column():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await migrate_memoir_voice_schema(engine)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "ALTER TABLE memoir_voice_sessions "
                    "ADD COLUMN legacy_question_id VARCHAR(36)"
                )
            )

        with pytest.raises(RuntimeError, match="unexpected columns") as exc_info:
            await assert_memoir_voice_schema(engine)

        assert "memoir_voice_sessions.legacy_question_id" in str(exc_info.value)
    finally:
        await engine.dispose()


async def test_rebuild_memoir_voice_schema_restores_exact_current_model():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        await rebuild_memoir_voice_schema(engine)
        await assert_memoir_voice_schema(engine)

        assert (
            await describe_memoir_voice_schema(engine)
            == expected_memoir_voice_schema()
        )
    finally:
        await engine.dispose()
