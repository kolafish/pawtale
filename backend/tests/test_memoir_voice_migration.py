import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.migrations.memoir_voice import assert_memoir_voice_schema, migrate_memoir_voice_schema


async def test_memoir_voice_migration_adds_transcript_voice_session_column():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    CREATE TABLE memoir_transcript_versions (
                        id VARCHAR(36) NOT NULL,
                        answer_id VARCHAR(36),
                        source_artifact_id VARCHAR(36),
                        version_type VARCHAR(30) NOT NULL,
                        content TEXT NOT NULL,
                        provider VARCHAR(100),
                        confidence FLOAT,
                        created_by VARCHAR(20) NOT NULL,
                        created_at DATETIME,
                        PRIMARY KEY (id)
                    )
                    """
                )
            )

        result = await migrate_memoir_voice_schema(engine)
        second_result = await migrate_memoir_voice_schema(engine)

        async with engine.begin() as conn:
            columns = {
                row[1]
                for row in (
                    await conn.execute(text("PRAGMA table_info(memoir_transcript_versions)"))
                ).fetchall()
            }
            index_name = await conn.scalar(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'index' AND name = 'ix_memoir_transcript_versions_voice_session_id'"
                )
            )

        assert result["changed_columns"] == ["memoir_transcript_versions.voice_session_id"]
        assert second_result["changed_columns"] == []
        assert "voice_session_id" in columns
        assert index_name == "ix_memoir_transcript_versions_voice_session_id"
    finally:
        await engine.dispose()


async def test_memoir_voice_migration_requires_transcript_table():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        with pytest.raises(RuntimeError, match="Missing required table: memoir_transcript_versions"):
            await migrate_memoir_voice_schema(engine)
    finally:
        await engine.dispose()


async def test_memoir_voice_schema_assertion_reports_missing_tables_and_columns():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    CREATE TABLE memoir_transcript_versions (
                        id VARCHAR(36) NOT NULL,
                        answer_id VARCHAR(36),
                        source_artifact_id VARCHAR(36),
                        version_type VARCHAR(30) NOT NULL,
                        content TEXT NOT NULL,
                        provider VARCHAR(100),
                        confidence FLOAT,
                        created_by VARCHAR(20) NOT NULL,
                        created_at DATETIME,
                        PRIMARY KEY (id)
                    )
                    """
                )
            )

        with pytest.raises(RuntimeError) as exc_info:
            await assert_memoir_voice_schema(engine)

        message = str(exc_info.value)
        assert "missing tables:" in message
        assert "audio_files" in message
        assert "memoir_voice_sessions" in message
        assert "missing columns:" in message
        assert "memoir_transcript_versions.voice_session_id" in message
    finally:
        await engine.dispose()
