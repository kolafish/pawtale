import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.database import Base, register_models
from app.migrations.memoir_voice import (
    AUTOMATION_EVENT_LINK_TRIGGERS,
    AUXILIARY_QUERY_INDEXES,
    TRANSCRIPT_FTS_TABLE,
    TRANSCRIPT_FTS_TRIGGERS,
    VOICE_SESSION_ANCHOR_TRIGGERS,
    _ensure_automation_event_link_schema,
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
            *AUTOMATION_EVENT_LINK_TRIGGERS,
            *TRANSCRIPT_FTS_TRIGGERS,
        ]
        assert first["changed_columns"] == []
        assert first["changed_rows"] == 0
        assert first["changed_indexes"] == []
        assert second["changed_tables"] == []
        assert second["changed_triggers"] == []
        assert second["changed_columns"] == []
        assert second["changed_indexes"] == []
        assert second["changed_rows"] == 0
        await assert_memoir_voice_schema(engine)
    finally:
        await engine.dispose()


async def test_automation_event_link_migration_backfills_direct_truth_once():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE TABLE memoir_voice_sessions ("
                    "id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36) NOT NULL)"
                )
            )
            await conn.execute(
                text(
                    "CREATE TABLE memoir_voice_utterances ("
                    "id VARCHAR(36) PRIMARY KEY, "
                    "voice_session_id VARCHAR(36) NOT NULL)"
                )
            )
            await conn.execute(
                text(
                    "CREATE TABLE memoir_followup_tasks ("
                    "id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36) NOT NULL)"
                )
            )
            await conn.execute(
                text(
                    "CREATE TABLE memoir_editing_jobs ("
                    "id VARCHAR(36) PRIMARY KEY, "
                    "project_id VARCHAR(36) NOT NULL, "
                    "chapter_id VARCHAR(36), "
                    "job_type VARCHAR(80) NOT NULL, "
                    "status VARCHAR(20) NOT NULL, "
                    "run_after DATETIME, lease_expires_at DATETIME, "
                    "updated_at DATETIME NOT NULL)"
                )
            )
            await conn.execute(
                text(
                    "CREATE TABLE memoir_automation_events ("
                    "id VARCHAR(36) PRIMARY KEY, "
                    "project_id VARCHAR(36) NOT NULL, "
                    "chapter_id VARCHAR(36), "
                    "event_type VARCHAR(40) NOT NULL, "
                    "status VARCHAR(24) NOT NULL, "
                    "utterance_ids_json JSON NOT NULL, "
                    "summary_text TEXT NOT NULL, "
                    "confidence FLOAT, "
                    "metadata_json JSON NOT NULL, "
                    "created_at DATETIME NOT NULL, "
                    "updated_at DATETIME NOT NULL)"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO memoir_voice_sessions VALUES "
                    "('session-1', 'project-1'), "
                    "('session-2', 'project-2')"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO memoir_voice_utterances VALUES "
                    "('utterance-1', 'session-1'), "
                    "('utterance-2', 'session-2')"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO memoir_followup_tasks VALUES "
                    "('task-1', 'project-1')"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO memoir_editing_jobs VALUES "
                    "('job-1', 'project-1', 'chapter-1', "
                    "'quality_audit', 'pending', NULL, NULL, CURRENT_TIMESTAMP)"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO memoir_automation_events VALUES ("
                    "'event-1', 'project-1', 'chapter-1', "
                        "'source_held', 'needs_review', '[\"utterance-1\"]', "
                        "'需要确认', NULL, "
                        "'{\"route\":{\"followup_answer\":{\"task_id\":\"task-1\"},"
                        "\"editing_job_id\":\"job-1\"}}', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )

            first = await _ensure_automation_event_link_schema(conn)
            second = await _ensure_automation_event_link_schema(conn)

            row = (
                await conn.execute(
                    text(
                        "SELECT review_utterance_id, followup_task_id, "
                        "editing_job_id, utterance_ids_json "
                        "FROM memoir_automation_events WHERE id = 'event-1'"
                    )
                )
            ).one()
            indexes = {
                str(item[1])
                for table_name in (
                    "memoir_automation_events",
                    "memoir_editing_jobs",
                )
                for item in (
                    await conn.execute(
                        text(f'PRAGMA index_list("{table_name}")')
                    )
                ).fetchall()
            }

        assert first["changed_columns"] == [
            "memoir_automation_events.review_utterance_id",
            "memoir_automation_events.followup_task_id",
            "memoir_automation_events.editing_job_id",
        ]
        assert set(first["changed_indexes"]) == set(AUXILIARY_QUERY_INDEXES)
        assert first["changed_triggers"] == list(
            AUTOMATION_EVENT_LINK_TRIGGERS
        )
        assert first["changed_rows"] == 1
        assert second == {
            "changed_columns": [],
            "changed_indexes": [],
            "changed_triggers": [],
            "changed_rows": 0,
        }
        assert row == ("utterance-1", "task-1", "job-1", "[]")
        assert set(AUXILIARY_QUERY_INDEXES).issubset(indexes)

        async with engine.begin() as conn:
            with pytest.raises(
                IntegrityError,
                match="invalid memoir automation event direct links",
            ):
                await conn.execute(
                    text(
                        "INSERT INTO memoir_automation_events ("
                        "id, project_id, event_type, status, "
                        "utterance_ids_json, summary_text, metadata_json, "
                        "created_at, updated_at, review_utterance_id"
                        ") VALUES ("
                        "'event-invalid', 'project-1', 'source_held', "
                        "'needs_review', '[]', '跨项目', '{}', "
                        "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'utterance-2')"
                    )
                )
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
