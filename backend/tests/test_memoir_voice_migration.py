import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.database import Base, register_models
from app.migrations.memoir_voice import (
    TRANSCRIPT_FTS_TABLE,
    TRANSCRIPT_FTS_TRIGGERS,
    VOICE_SESSION_ANCHOR_TRIGGERS,
    assert_memoir_voice_schema,
    describe_memoir_voice_schema,
    expected_memoir_voice_schema,
    migrate_memoir_voice_schema,
    rebuild_memoir_voice_schema,
)


async def test_memoir_voice_migration_installs_runtime_managed_objects():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        first = await migrate_memoir_voice_schema(engine)
        second = await migrate_memoir_voice_schema(engine)

        assert first["changed_tables"] == [TRANSCRIPT_FTS_TABLE]
        assert first["changed_triggers"] == [
            *VOICE_SESSION_ANCHOR_TRIGGERS,
            *TRANSCRIPT_FTS_TRIGGERS,
        ]
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


async def test_memoir_voice_migration_adds_anchor_to_existing_session_table():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(text("PRAGMA foreign_keys=OFF"))
            await conn.execute(text("DROP TABLE memoir_voice_sessions"))
            await conn.execute(
                text(
                    """
                    CREATE TABLE memoir_voice_sessions (
                        id VARCHAR(36) NOT NULL PRIMARY KEY,
                        project_id VARCHAR(36) NOT NULL,
                        status VARCHAR(20) NOT NULL,
                        guidance_mode VARCHAR(30) NOT NULL,
                        interview_type VARCHAR(30) NOT NULL,
                        session_goal VARCHAR(30) NOT NULL,
                        current_followup_task_id VARCHAR(36),
                        current_guidance_module VARCHAR(80),
                        provider VARCHAR(100),
                        input_format VARCHAR(100),
                        started_at DATETIME,
                        stopped_at DATETIME,
                        error_message TEXT NOT NULL,
                        metadata_json JSON NOT NULL,
                        created_at DATETIME NOT NULL,
                        updated_at DATETIME NOT NULL
                    )
                    """
                )
            )
            await conn.execute(text("PRAGMA foreign_keys=ON"))

        result = await migrate_memoir_voice_schema(engine)

        assert result["changed_columns"] == [
            "memoir_voice_sessions.anchor_framework_item_id"
        ]
        async with engine.begin() as conn:
            columns = {
                str(row[1])
                for row in (
                    await conn.execute(
                        text("PRAGMA table_info(memoir_voice_sessions)")
                    )
                ).fetchall()
            }
            assert "anchor_framework_item_id" in columns
            with pytest.raises(IntegrityError):
                await conn.execute(
                    text(
                        """
                        INSERT INTO memoir_voice_sessions (
                            id, project_id, status, guidance_mode,
                            interview_type, session_goal,
                            anchor_framework_item_id, error_message,
                            metadata_json, created_at, updated_at
                        ) VALUES (
                            'session-1', 'project-1', 'created',
                            'free_narration', 'ai_interview',
                            'open_narration', 'topic-1', '', '{}',
                            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                        )
                        """
                    )
                )
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
