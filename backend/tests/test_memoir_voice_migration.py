import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.migrations.memoir_voice import (
    assert_memoir_voice_schema,
    describe_memoir_voice_schema,
    expected_memoir_voice_schema,
    migrate_memoir_voice_schema,
    rebuild_memoir_voice_schema,
)


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
        assert "source_artifact_id" in columns
        assert index_name == "ix_memoir_transcript_versions_voice_session_id"
    finally:
        await engine.dispose()


async def test_memoir_voice_migration_creates_automation_events_table():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    CREATE TABLE memoir_transcript_versions (
                        id VARCHAR(36) NOT NULL,
                        answer_id VARCHAR(36),
                        voice_session_id VARCHAR(36),
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
            table_name = await conn.scalar(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'memoir_automation_events'"
                )
            )
            columns = {
                row[1]
                for row in (
                    await conn.execute(text("PRAGMA table_info(memoir_automation_events)"))
                ).fetchall()
            }

        assert "memoir_automation_events" in result["changed_tables"]
        assert "memoir_automation_events" not in second_result["changed_tables"]
        assert table_name == "memoir_automation_events"
        assert {"event_type", "status", "utterance_ids_json", "summary_text"}.issubset(columns)
    finally:
        await engine.dispose()


async def test_memoir_voice_migration_creates_transcript_fts_table_and_triggers():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    CREATE TABLE memoir_transcript_versions (
                        id VARCHAR(36) NOT NULL,
                        answer_id VARCHAR(36),
                        voice_session_id VARCHAR(36),
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
            await conn.execute(
                text(
                    """
                    CREATE TABLE memoir_voice_utterances (
                        id VARCHAR(36) NOT NULL,
                        transcript_raw TEXT NOT NULL,
                        transcript_edited TEXT NOT NULL,
                        PRIMARY KEY (id)
                    )
                    """
                )
            )

        result = await migrate_memoir_voice_schema(engine)

        async with engine.begin() as conn:
            fts_table = await conn.scalar(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'memoir_voice_utterances_fts'"
                )
            )
            triggers = (
                await conn.execute(
                    text(
                        "SELECT name FROM sqlite_master "
                        "WHERE type = 'trigger' AND name LIKE 'memoir_voice_utterances_fts_%'"
                    )
                )
            ).scalars().all()
            await conn.execute(
                text(
                    """
                    INSERT INTO memoir_voice_utterances (id, transcript_raw, transcript_edited)
                    VALUES ('utterance-1', '我小时候常在外婆家的院子里晒太阳。', '')
                    """
                )
            )
            inserted_count = await conn.scalar(
                text(
                    "SELECT count(*) FROM memoir_voice_utterances_fts "
                    "WHERE memoir_voice_utterances_fts MATCH '\"外婆家\"'"
                )
            )
            await conn.execute(
                text(
                    """
                    UPDATE memoir_voice_utterances
                    SET transcript_edited = '后来去县城读书。'
                    WHERE id = 'utterance-1'
                    """
                )
            )
            updated_count = await conn.scalar(
                text(
                    "SELECT count(*) FROM memoir_voice_utterances_fts "
                    "WHERE memoir_voice_utterances_fts MATCH '\"县城读\"'"
                )
            )

        assert "memoir_voice_utterances_fts" in result["changed_tables"]
        assert fts_table == "memoir_voice_utterances_fts"
        assert sorted(triggers) == [
            "memoir_voice_utterances_fts_ad",
            "memoir_voice_utterances_fts_ai",
            "memoir_voice_utterances_fts_au",
        ]
        assert inserted_count == 1
        assert updated_count == 1
    finally:
        await engine.dispose()


async def test_memoir_voice_migration_prunes_old_transcript_versions():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    CREATE TABLE memoir_transcript_versions (
                        id VARCHAR(36) NOT NULL,
                        answer_id VARCHAR(36),
                        voice_session_id VARCHAR(36),
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
            for index in range(5):
                await conn.execute(
                    text(
                        """
                        INSERT INTO memoir_transcript_versions (
                            id,
                            source_artifact_id,
                            version_type,
                            content,
                            created_by,
                            created_at
                        )
                        VALUES (
                            :id,
                            'utterance-1',
                            'user_edited',
                            :content,
                            'user',
                            :created_at
                        )
                        """
                    ),
                    {
                        "id": f"version-{index + 1}",
                        "content": f"版本 {index + 1}",
                        "created_at": f"2026-06-24T00:00:0{index}",
                    },
                )

        await migrate_memoir_voice_schema(engine)

        async with engine.begin() as conn:
            remaining = (
                await conn.execute(
                    text("SELECT id FROM memoir_transcript_versions ORDER BY created_at")
                )
            ).scalars().all()

        assert remaining == ["version-3", "version-4", "version-5"]
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
        assert "memoir_transcript_versions.source_artifact_id" in message
    finally:
        await engine.dispose()


async def test_memoir_voice_schema_assertion_reports_unexpected_columns():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        await rebuild_memoir_voice_schema(engine)
        async with engine.begin() as conn:
            await conn.execute(text("ALTER TABLE memoir_voice_sessions ADD COLUMN legacy_question_id VARCHAR(36)"))

        with pytest.raises(RuntimeError) as exc_info:
            await assert_memoir_voice_schema(engine)

        assert "unexpected columns:" in str(exc_info.value)
        assert "memoir_voice_sessions.legacy_question_id" in str(exc_info.value)
    finally:
        await engine.dispose()


async def test_memoir_voice_schema_assertion_reports_nullable_mismatch():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        await rebuild_memoir_voice_schema(engine)
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE memoir_transcript_versions"))
            await conn.execute(
                text(
                    """
                    CREATE TABLE memoir_transcript_versions (
                        id VARCHAR(36) NOT NULL,
                        answer_id VARCHAR(36) NOT NULL,
                        voice_session_id VARCHAR(36),
                        source_artifact_id VARCHAR(36),
                        version_type VARCHAR(30) NOT NULL,
                        content TEXT NOT NULL,
                        provider VARCHAR(100),
                        confidence FLOAT,
                        created_by VARCHAR(20) NOT NULL,
                        created_at DATETIME NOT NULL,
                        PRIMARY KEY (id)
                    )
                    """
                )
            )

        with pytest.raises(RuntimeError) as exc_info:
            await assert_memoir_voice_schema(engine)

        message = str(exc_info.value)
        assert "column mismatches:" in message
        assert "memoir_transcript_versions.answer_id.nullable expected=True actual=False" in message
    finally:
        await engine.dispose()


async def test_rebuild_memoir_voice_schema_restores_exact_model_columns():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    CREATE TABLE memoir_voice_sessions (
                        id VARCHAR(36) NOT NULL,
                        legacy_question_id VARCHAR(36),
                        PRIMARY KEY (id)
                    )
                    """
                )
            )

        await rebuild_memoir_voice_schema(engine)
        await assert_memoir_voice_schema(engine)

        assert await describe_memoir_voice_schema(engine) == expected_memoir_voice_schema()
    finally:
        await engine.dispose()
