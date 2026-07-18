from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.database import Base, register_models


REALTIME_VOICE_TABLES = (
    "audio_files",
    "memoir_voice_sessions",
    "memoir_chat_messages",
    "memoir_voice_utterances",
    "memoir_transcript_alignments",
    "memoir_material_share_videos",
    "memoir_share_video_outputs",
    "memoir_transcript_versions",
    "memoir_voice_authorization_consents",
)
MEMOIR_AUXILIARY_TABLES = (
    "memoir_automation_events",
    "memoir_editing_jobs",
)
TRANSCRIPT_FTS_TABLE = "memoir_voice_utterances_fts"
TRANSCRIPT_FTS_TRIGGERS = (
    "memoir_voice_utterances_fts_ai",
    "memoir_voice_utterances_fts_ad",
    "memoir_voice_utterances_fts_au",
)
VOICE_INTEGRITY_TRIGGERS = (
    "memoir_voice_sessions_targets_insert",
    "memoir_voice_sessions_targets_update",
    "memoir_voice_utterances_targets_insert",
    "memoir_voice_utterances_targets_update",
    "memoir_automation_events_job_insert",
    "memoir_automation_events_job_update",
)
VOICE_TARGET_TRIGGERS = VOICE_INTEGRITY_TRIGGERS[:4]
VOICE_COMPLETION_COLUMNS = {
    "memoir_voice_sessions": "current_completion_issue_id",
    "memoir_voice_utterances": "completion_issue_id",
}


async def _object_exists(conn, *, object_type: str, name: str) -> bool:
    return (
        await conn.scalar(
            text(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = :object_type AND name = :name"
            ),
            {"object_type": object_type, "name": name},
        )
        is not None
    )


async def _table_columns(conn, table_name: str) -> set[str]:
    rows = (await conn.execute(text(f"PRAGMA table_info({table_name})"))).fetchall()
    return {str(row[1]) for row in rows}


async def _ensure_completion_issue_columns(conn) -> list[str]:
    changed: list[str] = []
    statements = {
        "memoir_voice_sessions": (
            "ALTER TABLE memoir_voice_sessions "
            "ADD COLUMN current_completion_issue_id VARCHAR(36) "
            "REFERENCES memoir_completion_issues(id) ON DELETE SET NULL"
        ),
        "memoir_voice_utterances": (
            "ALTER TABLE memoir_voice_utterances "
            "ADD COLUMN completion_issue_id VARCHAR(36) "
            "REFERENCES memoir_completion_issues(id) ON DELETE SET NULL"
        ),
    }
    indexes = {
        "memoir_voice_sessions": (
            "CREATE INDEX IF NOT EXISTS "
            "ix_memoir_voice_sessions_current_completion_issue_id "
            "ON memoir_voice_sessions(current_completion_issue_id)"
        ),
        "memoir_voice_utterances": (
            "CREATE INDEX IF NOT EXISTS "
            "ix_memoir_voice_utterances_completion_issue_id "
            "ON memoir_voice_utterances(completion_issue_id)"
        ),
    }
    for table_name, column_name in VOICE_COMPLETION_COLUMNS.items():
        if column_name not in await _table_columns(conn, table_name):
            await conn.execute(text(statements[table_name]))
            changed.append(f"{table_name}.{column_name}")
        await conn.execute(text(indexes[table_name]))
    return changed


async def _drop_stale_voice_target_triggers(conn) -> None:
    for name in VOICE_TARGET_TRIGGERS:
        sql = await conn.scalar(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'trigger' AND name = :name"
            ),
            {"name": name},
        )
        if sql and "completion_issue_id" not in str(sql):
            await conn.execute(text(f"DROP TRIGGER {name}"))


async def _ensure_transcript_fts(conn) -> dict[str, list[str]]:
    changed_tables: list[str] = []
    changed_triggers: list[str] = []
    if not await _object_exists(
        conn,
        object_type="table",
        name=TRANSCRIPT_FTS_TABLE,
    ):
        await conn.execute(
            text(
                f"""
                CREATE VIRTUAL TABLE {TRANSCRIPT_FTS_TABLE}
                USING fts5(
                    transcript_raw,
                    transcript_edited,
                    content='memoir_voice_utterances',
                    content_rowid='rowid',
                    tokenize='trigram'
                )
                """
            )
        )
        await conn.execute(
            text(
                f"INSERT INTO {TRANSCRIPT_FTS_TABLE}({TRANSCRIPT_FTS_TABLE}) "
                "VALUES('rebuild')"
            )
        )
        changed_tables.append(TRANSCRIPT_FTS_TABLE)

    statements = {
        "memoir_voice_utterances_fts_ai": f"""
            CREATE TRIGGER memoir_voice_utterances_fts_ai
            AFTER INSERT ON memoir_voice_utterances
            BEGIN
                INSERT INTO {TRANSCRIPT_FTS_TABLE}(
                    rowid, transcript_raw, transcript_edited
                ) VALUES (new.rowid, new.transcript_raw, new.transcript_edited);
            END
        """,
        "memoir_voice_utterances_fts_ad": f"""
            CREATE TRIGGER memoir_voice_utterances_fts_ad
            AFTER DELETE ON memoir_voice_utterances
            BEGIN
                INSERT INTO {TRANSCRIPT_FTS_TABLE}(
                    {TRANSCRIPT_FTS_TABLE}, rowid,
                    transcript_raw, transcript_edited
                ) VALUES (
                    'delete', old.rowid,
                    old.transcript_raw, old.transcript_edited
                );
            END
        """,
        "memoir_voice_utterances_fts_au": f"""
            CREATE TRIGGER memoir_voice_utterances_fts_au
            AFTER UPDATE ON memoir_voice_utterances
            BEGIN
                INSERT INTO {TRANSCRIPT_FTS_TABLE}(
                    {TRANSCRIPT_FTS_TABLE}, rowid,
                    transcript_raw, transcript_edited
                ) VALUES (
                    'delete', old.rowid,
                    old.transcript_raw, old.transcript_edited
                );
                INSERT INTO {TRANSCRIPT_FTS_TABLE}(
                    rowid, transcript_raw, transcript_edited
                ) VALUES (new.rowid, new.transcript_raw, new.transcript_edited);
            END
        """,
    }
    for name, statement in statements.items():
        if await _object_exists(conn, object_type="trigger", name=name):
            continue
        await conn.execute(text(statement))
        changed_triggers.append(name)
    return {
        "changed_tables": changed_tables,
        "changed_triggers": changed_triggers,
    }


async def _ensure_voice_integrity_triggers(conn) -> list[str]:
    session_validation = """
        (
            NEW.current_completion_issue_id IS NOT NULL
            AND (
                NEW.session_goal != 'book_completion'
                OR NOT EXISTS (
                    SELECT 1 FROM memoir_completion_issues AS issue
                    WHERE issue.id = NEW.current_completion_issue_id
                      AND issue.project_id = NEW.project_id
                )
            )
        )
        OR (
            NEW.current_completion_issue_id IS NOT NULL
            AND NEW.anchor_framework_item_id IS NOT NULL
        )
        OR (
            NEW.anchor_framework_item_id IS NOT NULL
            AND NOT EXISTS (
                SELECT 1
                FROM memoir_framework_items AS item
                JOIN memoir_project_frameworks AS framework
                  ON framework.id = item.project_framework_id
                WHERE item.id = NEW.anchor_framework_item_id
                  AND framework.project_id = NEW.project_id
            )
        )
    """
    utterance_validation = """
        (
            NEW.completion_issue_id IS NOT NULL
            AND NOT EXISTS (
                SELECT 1
                FROM memoir_completion_issues AS issue
                JOIN memoir_voice_sessions AS session
                  ON session.id = NEW.voice_session_id
                WHERE issue.id = NEW.completion_issue_id
                  AND issue.project_id = session.project_id
            )
        )
        OR (
            NEW.completion_issue_id IS NOT NULL
            AND NEW.framework_item_id IS NOT NULL
        )
        OR (
            NEW.framework_item_id IS NOT NULL
            AND NOT EXISTS (
                SELECT 1
                FROM memoir_framework_items AS item
                JOIN memoir_project_frameworks AS framework
                  ON framework.id = item.project_framework_id
                JOIN memoir_voice_sessions AS session
                  ON session.id = NEW.voice_session_id
                WHERE item.id = NEW.framework_item_id
                  AND framework.project_id = session.project_id
            )
        )
        OR (
            NEW.policy_decision_id IS NOT NULL
            AND NOT EXISTS (
                SELECT 1 FROM memoir_interview_policy_decisions AS decision
                WHERE decision.id = NEW.policy_decision_id
                  AND decision.session_id = NEW.voice_session_id
            )
        )
    """
    event_validation = """
        (
            NEW.editing_job_id IS NOT NULL
            AND NOT EXISTS (
                SELECT 1 FROM memoir_editing_jobs AS job
                WHERE job.id = NEW.editing_job_id
                  AND job.project_id = NEW.project_id
            )
        )
    """
    statements = {
        "memoir_voice_sessions_targets_insert": f"""
            CREATE TRIGGER memoir_voice_sessions_targets_insert
            BEFORE INSERT ON memoir_voice_sessions
            WHEN {session_validation}
            BEGIN
                SELECT RAISE(ABORT, 'invalid memoir voice session target');
            END
        """,
        "memoir_voice_sessions_targets_update": f"""
            CREATE TRIGGER memoir_voice_sessions_targets_update
            BEFORE UPDATE OF
                project_id,
                session_goal,
                current_completion_issue_id,
                anchor_framework_item_id
            ON memoir_voice_sessions
            WHEN {session_validation}
            BEGIN
                SELECT RAISE(ABORT, 'invalid memoir voice session target');
            END
        """,
        "memoir_voice_utterances_targets_insert": f"""
            CREATE TRIGGER memoir_voice_utterances_targets_insert
            BEFORE INSERT ON memoir_voice_utterances
            WHEN {utterance_validation}
            BEGIN
                SELECT RAISE(ABORT, 'invalid memoir voice utterance target');
            END
        """,
        "memoir_voice_utterances_targets_update": f"""
            CREATE TRIGGER memoir_voice_utterances_targets_update
            BEFORE UPDATE OF
                voice_session_id,
                completion_issue_id,
                framework_item_id,
                policy_decision_id
            ON memoir_voice_utterances
            WHEN {utterance_validation}
            BEGIN
                SELECT RAISE(ABORT, 'invalid memoir voice utterance target');
            END
        """,
        "memoir_automation_events_job_insert": f"""
            CREATE TRIGGER memoir_automation_events_job_insert
            BEFORE INSERT ON memoir_automation_events
            WHEN {event_validation}
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'invalid memoir automation event job'
                );
            END
        """,
        "memoir_automation_events_job_update": f"""
            CREATE TRIGGER memoir_automation_events_job_update
            BEFORE UPDATE OF
                project_id,
                editing_job_id
            ON memoir_automation_events
            WHEN {event_validation}
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'invalid memoir automation event job'
                );
            END
        """,
    }
    changed: list[str] = []
    for name, statement in statements.items():
        if await _object_exists(conn, object_type="trigger", name=name):
            continue
        await conn.execute(text(statement))
        changed.append(name)
    return changed


async def migrate_memoir_voice_schema(engine: AsyncEngine) -> dict[str, object]:
    """Create the current voice schema; historical shape upgrades are unsupported."""
    register_models()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        changed_columns = await _ensure_completion_issue_columns(conn)
        await _drop_stale_voice_target_triggers(conn)
        fts = await _ensure_transcript_fts(conn)
        fts["changed_triggers"].extend(
            await _ensure_voice_integrity_triggers(conn)
        )
        fts["changed_columns"] = changed_columns
    return fts


async def assert_memoir_voice_schema(engine: AsyncEngine) -> None:
    register_models()
    expected_tables = (*REALTIME_VOICE_TABLES, *MEMOIR_AUXILIARY_TABLES)
    async with engine.begin() as conn:
        missing = [
            name
            for name in expected_tables
            if not await _object_exists(conn, object_type="table", name=name)
        ]
        if not await _object_exists(
            conn,
            object_type="table",
            name=TRANSCRIPT_FTS_TABLE,
        ):
            missing.append(TRANSCRIPT_FTS_TABLE)
        for name in (*TRANSCRIPT_FTS_TRIGGERS, *VOICE_INTEGRITY_TRIGGERS):
            if not await _object_exists(
                conn,
                object_type="trigger",
                name=name,
            ):
                missing.append(name)
        for table_name, column_name in VOICE_COMPLETION_COLUMNS.items():
            if column_name not in await _table_columns(conn, table_name):
                missing.append(f"{table_name}.{column_name}")
    if missing:
        raise RuntimeError(f"Missing memoir voice schema objects: {sorted(missing)}")
