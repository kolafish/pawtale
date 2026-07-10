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


async def test_memoir_voice_migration_creates_editing_jobs_table():
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
                    "WHERE type = 'table' AND name = 'memoir_editing_jobs'"
                )
            )
            columns = {
                row[1]
                for row in (
                    await conn.execute(text("PRAGMA table_info(memoir_editing_jobs)"))
                ).fetchall()
            }

        assert "memoir_editing_jobs" in result["changed_tables"]
        assert "memoir_editing_jobs" not in second_result["changed_tables"]
        assert table_name == "memoir_editing_jobs"
        assert {
            "job_type",
            "status",
            "run_after",
            "dedupe_key",
            "payload_json",
            "lease_token",
            "lease_expires_at",
        }.issubset(columns)
    finally:
        await engine.dispose()


async def test_memoir_voice_migration_adds_share_video_job_lifecycle_columns():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    CREATE TABLE memoir_transcript_versions (
                        id VARCHAR(36) NOT NULL,
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
                    CREATE TABLE memoir_material_share_videos (
                        id VARCHAR(36) NOT NULL PRIMARY KEY,
                        project_id VARCHAR(36) NOT NULL,
                        utterance_id VARCHAR(36) NOT NULL,
                        audio_file_id VARCHAR(36) NOT NULL,
                        user_id VARCHAR(36) NOT NULL,
                        status VARCHAR(20) NOT NULL,
                        text_source VARCHAR(20) NOT NULL,
                        text_hash VARCHAR(64) NOT NULL,
                        selected_text TEXT NOT NULL,
                        start_char INTEGER NOT NULL,
                        end_char INTEGER NOT NULL,
                        start_ms INTEGER NOT NULL,
                        end_ms INTEGER NOT NULL,
                        style VARCHAR(40) NOT NULL,
                        style_json JSON NOT NULL,
                        metadata_json JSON NOT NULL,
                        video_storage_path VARCHAR(500),
                        video_mime_type VARCHAR(100) NOT NULL,
                        video_size_bytes INTEGER,
                        error_message TEXT NOT NULL,
                        completed_at DATETIME,
                        created_at DATETIME,
                        updated_at DATETIME
                    )
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO memoir_material_share_videos (
                        id, project_id, utterance_id, audio_file_id, user_id,
                        status, text_source, text_hash, selected_text,
                        start_char, end_char, start_ms, end_ms, style,
                        style_json, metadata_json, video_mime_type,
                        error_message, created_at, updated_at
                    ) VALUES (
                        'video-1', 'project-1', 'utterance-1', 'audio-1', 'user-1',
                        'pending', 'edited', 'hash', '一段讲述',
                        0, 4, 0, 4000, 'warm_minimal',
                        '{}', '{}', 'video/mp4', '',
                        '2026-07-10 00:00:00', '2026-07-10 00:00:00'
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
                    await conn.execute(text("PRAGMA table_info(memoir_material_share_videos)"))
                ).fetchall()
            }
            row = (
                await conn.execute(
                    text(
                        "SELECT id, run_after, locked_at, lease_token, lease_expires_at, attempts "
                        "FROM memoir_material_share_videos WHERE id = 'video-1'"
                    )
                )
            ).one()

        expected_columns = {
            "run_after",
            "locked_at",
            "lease_token",
            "lease_expires_at",
            "attempts",
        }
        assert expected_columns.issubset(columns)
        assert {"style", "style_json"}.isdisjoint(columns)
        assert "memoir_material_share_videos" in result["changed_tables"]
        assert expected_columns == {
            item.removeprefix("memoir_material_share_videos.")
            for item in result["changed_columns"]
            if item.startswith("memoir_material_share_videos.")
        }
        assert not any(
            item.startswith("memoir_material_share_videos.")
            for item in second_result["changed_columns"]
        )
        assert "memoir_material_share_videos" not in second_result["changed_tables"]
        assert tuple(row) == ("video-1", None, None, None, None, 0)
    finally:
        await engine.dispose()


async def test_memoir_voice_migration_adds_source_route_disposition_columns():
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
                        voice_session_id VARCHAR(36) NOT NULL,
                        chat_message_id VARCHAR(36),
                        audio_file_id VARCHAR(36),
                        transcript_raw TEXT NOT NULL,
                        transcript_edited TEXT NOT NULL,
                        start_ms INTEGER,
                        end_ms INTEGER,
                        provider VARCHAR(100),
                        provider_payload_json JSON NOT NULL,
                        created_at DATETIME,
                        PRIMARY KEY (id)
                    )
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO memoir_voice_utterances (
                        id, voice_session_id, transcript_raw, transcript_edited, provider_payload_json
                    )
                    VALUES ('utterance-1', 'session-1', '你能不能别问了', '', '{}')
                    """
                )
            )

        result = await migrate_memoir_voice_schema(engine)
        second_result = await migrate_memoir_voice_schema(engine)

        async with engine.begin() as conn:
            rows = (
                await conn.execute(
                    text(
                        """
                        SELECT
                            source_material_type,
                            source_route_action,
                            source_route_confidence,
                            source_route_reason,
                            source_route_updated_at
                        FROM memoir_voice_utterances
                        """
                    )
                )
            ).fetchall()

        assert {
            column
            for column in result["changed_columns"]
            if column.startswith("memoir_voice_utterances.source_")
        } == {
            "memoir_voice_utterances.source_material_type",
            "memoir_voice_utterances.source_route_action",
            "memoir_voice_utterances.source_route_confidence",
            "memoir_voice_utterances.source_route_reason",
            "memoir_voice_utterances.source_route_updated_at",
        }
        assert second_result["changed_columns"] == []
        assert rows == [("", "", None, "", None)]
    finally:
        await engine.dispose()


async def test_memoir_voice_migration_adds_interview_type_to_voice_sessions():
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
                    CREATE TABLE memoir_voice_sessions (
                        id VARCHAR(36) NOT NULL,
                        project_id VARCHAR(36) NOT NULL,
                        memoir_session_id VARCHAR(36),
                        status VARCHAR(20) NOT NULL,
                        guidance_mode VARCHAR(30) NOT NULL,
                        current_guidance_module VARCHAR(80),
                        provider VARCHAR(100),
                        input_format VARCHAR(100),
                        started_at DATETIME,
                        stopped_at DATETIME,
                        error_message TEXT NOT NULL,
                        metadata_json JSON NOT NULL,
                        created_at DATETIME,
                        updated_at DATETIME,
                        PRIMARY KEY (id)
                    )
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO memoir_voice_sessions (
                        id,
                        project_id,
                        status,
                        guidance_mode,
                        error_message,
                        metadata_json
                    )
                    VALUES (
                        'voice-session-1',
                        'project-1',
                        'created',
                        'free_narration',
                        '',
                        '{}'
                    )
                    """
                )
            )

        result = await migrate_memoir_voice_schema(engine)
        second_result = await migrate_memoir_voice_schema(engine)

        async with engine.begin() as conn:
            rows = (
                await conn.execute(
                    text("SELECT id, interview_type FROM memoir_voice_sessions")
                )
            ).fetchall()
            columns = {
                row[1]
                for row in (
                    await conn.execute(text("PRAGMA table_info(memoir_voice_sessions)"))
                ).fetchall()
            }

        assert "memoir_voice_sessions.interview_type" in result["changed_columns"]
        assert "memoir_voice_sessions.interview_type" not in second_result["changed_columns"]
        assert "interview_type" in columns
        assert rows == [("voice-session-1", "ai_interview")]
    finally:
        await engine.dispose()


async def test_memoir_voice_migration_updates_editing_job_type_constraint():
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
                    CREATE TABLE memoir_editing_jobs (
                        id VARCHAR(36) NOT NULL,
                        project_id VARCHAR(36) NOT NULL,
                        chapter_id VARCHAR(36),
                        job_type VARCHAR(40) NOT NULL,
                        status VARCHAR(20) NOT NULL,
                        run_after DATETIME NOT NULL,
                        locked_at DATETIME,
                        attempts INTEGER NOT NULL,
                        dedupe_key VARCHAR(200),
                        payload_json JSON NOT NULL,
                        result_json JSON NOT NULL,
                        error_message TEXT NOT NULL,
                        created_at DATETIME,
                        updated_at DATETIME,
                        PRIMARY KEY (id),
                        CONSTRAINT ck_memoir_editing_jobs_type
                            CHECK (job_type IN ('route_sources', 'revise_chapter', 'quality_audit')),
                        CONSTRAINT ck_memoir_editing_jobs_status
                            CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'skipped')),
                        UNIQUE (dedupe_key)
                    )
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO memoir_editing_jobs (
                        id, project_id, job_type, status, run_after, attempts,
                        payload_json, result_json, error_message
                    )
                    VALUES (
                        'job-1', 'project-1', 'route_sources', 'pending',
                        '2026-06-27 16:00:00', 0, '{}', '{}', ''
                    )
                    """
                )
            )

        result = await migrate_memoir_voice_schema(engine)
        second_result = await migrate_memoir_voice_schema(engine)

        async with engine.begin() as conn:
            table_sql = await conn.scalar(
                text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'memoir_editing_jobs'")
            )
            rows = (
                await conn.execute(
                    text("SELECT id, job_type FROM memoir_editing_jobs ORDER BY id")
                )
            ).fetchall()
            await conn.execute(
                text(
                    """
                    INSERT INTO memoir_editing_jobs (
                        id, project_id, job_type, status, run_after, attempts,
                        payload_json, result_json, error_message, created_at, updated_at
                    )
                    VALUES (
                        'job-2', 'project-1', 'correct_transcript', 'pending',
                        '2026-06-27 16:00:00', 0, '{}', '{}', '',
                        '2026-06-27 16:00:00', '2026-06-27 16:00:00'
                    )
                    """
                )
            )

        assert "memoir_editing_jobs" in result["changed_tables"]
        assert "memoir_editing_jobs" not in second_result["changed_tables"]
        assert "correct_transcript" in str(table_sql)
        assert rows == [("job-1", "route_sources")]
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
