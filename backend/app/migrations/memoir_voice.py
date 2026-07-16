from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.schema import Column, Table

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
VOICE_SESSION_ANCHOR_TRIGGERS = (
    "memoir_voice_sessions_anchor_insert",
    "memoir_voice_sessions_anchor_update",
)
AUTOMATION_EVENT_LINK_TRIGGERS = (
    "memoir_automation_events_links_insert",
    "memoir_automation_events_links_update",
)
AUXILIARY_QUERY_INDEXES = (
    "ix_memoir_automation_events_project_timeline",
    "ix_memoir_automation_events_review_utterance",
    "ix_memoir_automation_events_review_task",
    "ix_memoir_automation_events_editing_job",
    "ix_memoir_editing_jobs_chapter_lookup",
    "ix_memoir_editing_jobs_pending_due",
    "ix_memoir_editing_jobs_running_lease",
)


def _memoir_voice_tables_from_metadata() -> tuple[Table, ...]:
    register_models()
    table_names = (*REALTIME_VOICE_TABLES, *MEMOIR_AUXILIARY_TABLES)
    return tuple(Base.metadata.tables[table_name] for table_name in table_names)


def _column_schema(column: Column) -> dict[str, object]:
    return {
        "name": column.name,
        "nullable": bool(column.nullable),
        "primary_key": bool(column.primary_key),
    }


def expected_memoir_voice_schema() -> dict[str, list[dict[str, object]]]:
    return {
        table.name: [_column_schema(column) for column in table.columns]
        for table in _memoir_voice_tables_from_metadata()
    }


async def _table_exists(conn, table_name: str) -> bool:
    result = await conn.execute(
        text(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = :table_name"
        ),
        {"table_name": table_name},
    )
    return result.scalar_one_or_none() is not None


async def _trigger_exists(conn, trigger_name: str) -> bool:
    result = await conn.execute(
        text(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'trigger' AND name = :trigger_name"
        ),
        {"trigger_name": trigger_name},
    )
    return result.scalar_one_or_none() is not None


async def _index_exists(conn, index_name: str) -> bool:
    result = await conn.execute(
        text(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'index' AND name = :index_name"
        ),
        {"index_name": index_name},
    )
    return result.scalar_one_or_none() is not None


async def _table_column_schema(
    conn,
    table_name: str,
) -> list[dict[str, object]]:
    result = await conn.execute(text(f'PRAGMA table_info("{table_name}")'))
    return [
        {
            "name": row[1],
            "nullable": not bool(row[3]),
            "primary_key": bool(row[5]),
        }
        for row in result.fetchall()
    ]


async def describe_memoir_voice_schema(
    engine: AsyncEngine,
) -> dict[str, list[dict[str, object]]]:
    actual: dict[str, list[dict[str, object]]] = {}
    async with engine.begin() as conn:
        for table_name in (*REALTIME_VOICE_TABLES, *MEMOIR_AUXILIARY_TABLES):
            actual[table_name] = (
                await _table_column_schema(conn, table_name)
                if await _table_exists(conn, table_name)
                else []
            )
    return actual


async def _drop_transcript_fts(conn) -> None:
    for trigger_name in TRANSCRIPT_FTS_TRIGGERS:
        await conn.execute(text(f'DROP TRIGGER IF EXISTS "{trigger_name}"'))
    await conn.execute(text(f'DROP TABLE IF EXISTS "{TRANSCRIPT_FTS_TABLE}"'))


async def _ensure_transcript_fts(conn) -> dict[str, list[str]]:
    if not await _table_exists(conn, "memoir_voice_utterances"):
        raise RuntimeError("Missing required table: memoir_voice_utterances")

    changed_tables: list[str] = []
    changed_triggers: list[str] = []
    if not await _table_exists(conn, TRANSCRIPT_FTS_TABLE):
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

    trigger_sql = {
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
    for trigger_name, sql in trigger_sql.items():
        if await _trigger_exists(conn, trigger_name):
            continue
        await conn.execute(text(sql))
        changed_triggers.append(trigger_name)

    return {
        "changed_tables": changed_tables,
        "changed_triggers": changed_triggers,
    }


async def _ensure_voice_session_anchor_schema(
    conn,
) -> dict[str, list[str]]:
    changed_columns: list[str] = []
    changed_indexes: list[str] = []
    changed_triggers: list[str] = []
    columns = {
        str(row[1])
        for row in (
            await conn.execute(
                text('PRAGMA table_info("memoir_voice_sessions")')
            )
        ).fetchall()
    }
    if "anchor_framework_item_id" not in columns:
        await conn.execute(
            text(
                "ALTER TABLE memoir_voice_sessions "
                "ADD COLUMN anchor_framework_item_id VARCHAR(36) "
                "REFERENCES memoir_framework_items(id) ON DELETE SET NULL"
            )
        )
        changed_columns.append(
            "memoir_voice_sessions.anchor_framework_item_id"
        )
    index_name = "ix_memoir_voice_sessions_anchor_framework_item_id"
    if not await _index_exists(conn, index_name):
        await conn.execute(
            text(
                f"CREATE INDEX {index_name} "
                "ON memoir_voice_sessions(anchor_framework_item_id)"
            )
        )
        changed_indexes.append(index_name)
    validation = """
        NEW.anchor_framework_item_id IS NOT NULL
        AND (
            NEW.current_followup_task_id IS NOT NULL
            OR NEW.guidance_mode != 'guided_interview'
            OR NEW.session_goal != 'open_narration'
            OR NOT EXISTS (
                SELECT 1
                FROM memoir_framework_items AS item
                JOIN memoir_project_frameworks AS framework
                  ON framework.id = item.project_framework_id
                WHERE item.id = NEW.anchor_framework_item_id
                  AND framework.project_id = NEW.project_id
            )
        )
    """
    statements = {
        "memoir_voice_sessions_anchor_insert": f"""
            CREATE TRIGGER memoir_voice_sessions_anchor_insert
            BEFORE INSERT ON memoir_voice_sessions
            WHEN {validation}
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'invalid memoir voice session framework anchor'
                );
            END
        """,
        "memoir_voice_sessions_anchor_update": f"""
            CREATE TRIGGER memoir_voice_sessions_anchor_update
            BEFORE UPDATE OF
                anchor_framework_item_id,
                current_followup_task_id,
                guidance_mode,
                session_goal,
                project_id
            ON memoir_voice_sessions
            WHEN {validation}
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'invalid memoir voice session framework anchor'
                );
            END
        """,
    }
    for trigger_name, statement in statements.items():
        if await _trigger_exists(conn, trigger_name):
            continue
        await conn.execute(text(statement))
        changed_triggers.append(trigger_name)
    return {
        "changed_columns": changed_columns,
        "changed_indexes": changed_indexes,
        "changed_triggers": changed_triggers,
    }


async def _ensure_automation_event_link_schema(
    conn,
) -> dict[str, list[str] | int]:
    changed_columns: list[str] = []
    changed_indexes: list[str] = []
    changed_triggers: list[str] = []
    columns = {
        str(row[1])
        for row in (
            await conn.execute(
                text('PRAGMA table_info("memoir_automation_events")')
            )
        ).fetchall()
    }
    column_sql = {
        "review_utterance_id": (
            "VARCHAR(36) REFERENCES memoir_voice_utterances(id) "
            "ON DELETE CASCADE"
        ),
        "followup_task_id": (
            "VARCHAR(36) REFERENCES memoir_followup_tasks(id) "
            "ON DELETE SET NULL"
        ),
        "editing_job_id": (
            "VARCHAR(36) REFERENCES memoir_editing_jobs(id) "
            "ON DELETE SET NULL"
        ),
    }
    for column_name, sql in column_sql.items():
        if column_name in columns:
            continue
        await conn.execute(
            text(
                "ALTER TABLE memoir_automation_events "
                f"ADD COLUMN {column_name} {sql}"
            )
        )
        changed_columns.append(
            f"memoir_automation_events.{column_name}"
        )

    invalid_review_count = int(
        (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM memoir_automation_events "
                    "WHERE event_type = 'source_held' "
                    "AND status = 'needs_review' "
                    "AND ("
                    "(review_utterance_id IS NULL AND ("
                    "json_valid(utterance_ids_json) = 0 "
                    "OR json_array_length(utterance_ids_json) != 1 "
                    "OR NOT EXISTS ("
                    "SELECT 1 FROM memoir_voice_utterances AS utterance "
                    "JOIN memoir_voice_sessions AS session "
                    "ON session.id = utterance.voice_session_id "
                    "WHERE utterance.id = json_extract("
                    "memoir_automation_events.utterance_ids_json, '$[0]') "
                    "AND session.project_id = "
                    "memoir_automation_events.project_id"
                    "))) "
                    "OR (review_utterance_id IS NOT NULL AND ("
                    "NOT EXISTS ("
                    "SELECT 1 FROM memoir_voice_utterances AS utterance "
                    "JOIN memoir_voice_sessions AS session "
                    "ON session.id = utterance.voice_session_id "
                    "WHERE utterance.id = "
                    "memoir_automation_events.review_utterance_id "
                    "AND session.project_id = "
                    "memoir_automation_events.project_id"
                    ") "
                    "OR json_valid(utterance_ids_json) = 0 "
                    "OR (json_array_length(utterance_ids_json) NOT IN (0, 1)) "
                    "OR (json_array_length(utterance_ids_json) = 1 "
                    "AND json_extract(utterance_ids_json, '$[0]') "
                    "!= review_utterance_id)"
                    "))"
                    ")"
                )
            )
        ).scalar_one()
    )
    if invalid_review_count:
        raise RuntimeError(
            "Cannot migrate source review events without exactly one "
            "project-owned utterance"
        )

    await conn.execute(
        text(
            "UPDATE memoir_automation_events "
            "SET review_utterance_id = "
            "json_extract(utterance_ids_json, '$[0]') "
            "WHERE event_type = 'source_held' "
            "AND status = 'needs_review' "
            "AND review_utterance_id IS NULL"
        )
    )
    await conn.execute(
        text(
            "UPDATE memoir_automation_events AS event "
            "SET followup_task_id = COALESCE("
            "json_extract(event.metadata_json, "
            "'$.route.followup_answer.task_id'), "
            "json_extract(event.metadata_json, "
            "'$.followup_answer.task_id')"
            ") "
            "WHERE event.event_type = 'source_held' "
            "AND event.status = 'needs_review' "
            "AND event.followup_task_id IS NULL "
            "AND EXISTS ("
            "SELECT 1 FROM memoir_followup_tasks AS task "
            "WHERE task.id = COALESCE("
            "json_extract(event.metadata_json, "
            "'$.route.followup_answer.task_id'), "
            "json_extract(event.metadata_json, "
            "'$.followup_answer.task_id')"
            ") "
            "AND task.project_id = event.project_id"
            ")"
        )
    )
    await conn.execute(
        text(
            "UPDATE memoir_automation_events AS event "
            "SET editing_job_id = COALESCE("
            "json_extract(event.metadata_json, '$.editing_job_id'), "
            "json_extract(event.metadata_json, '$.job_id'), "
            "json_extract(event.metadata_json, '$.route.editing_job_id'), "
            "json_extract(event.metadata_json, "
            "'$.routes[0].editing_job_id')"
            ") "
            "WHERE event.editing_job_id IS NULL "
            "AND EXISTS ("
            "SELECT 1 FROM memoir_editing_jobs AS job "
            "WHERE job.id = COALESCE("
            "json_extract(event.metadata_json, '$.editing_job_id'), "
            "json_extract(event.metadata_json, '$.job_id'), "
            "json_extract(event.metadata_json, '$.route.editing_job_id'), "
            "json_extract(event.metadata_json, "
            "'$.routes[0].editing_job_id')"
            ") "
            "AND job.project_id = event.project_id"
            ")"
        )
    )
    invalid_direct_link_count = int(
        (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM memoir_automation_events AS event "
                    "WHERE ("
                    "(event.event_type = 'source_held' "
                    "AND event.status = 'needs_review' "
                    "AND event.review_utterance_id IS NULL) "
                    "OR ((event.review_utterance_id IS NOT NULL "
                    "OR event.followup_task_id IS NOT NULL) "
                    "AND event.event_type != 'source_held') "
                    "OR (event.followup_task_id IS NOT NULL "
                    "AND event.review_utterance_id IS NULL) "
                    "OR (event.review_utterance_id IS NOT NULL "
                    "AND NOT EXISTS ("
                    "SELECT 1 FROM memoir_voice_utterances AS utterance "
                    "JOIN memoir_voice_sessions AS session "
                    "ON session.id = utterance.voice_session_id "
                    "WHERE utterance.id = event.review_utterance_id "
                    "AND session.project_id = event.project_id"
                    ")) "
                    "OR (event.followup_task_id IS NOT NULL "
                    "AND NOT EXISTS ("
                    "SELECT 1 FROM memoir_followup_tasks AS task "
                    "WHERE task.id = event.followup_task_id "
                    "AND task.project_id = event.project_id"
                    ")) "
                    "OR (event.editing_job_id IS NOT NULL "
                    "AND NOT EXISTS ("
                    "SELECT 1 FROM memoir_editing_jobs AS job "
                    "WHERE job.id = event.editing_job_id "
                    "AND job.project_id = event.project_id"
                    "))"
                    ")"
                )
            )
        ).scalar_one()
    )
    if invalid_direct_link_count:
        raise RuntimeError(
            "Cannot migrate invalid automation event direct links"
        )
    cleared_review_payloads = (
        await conn.execute(
            text(
                "UPDATE memoir_automation_events "
                "SET utterance_ids_json = '[]' "
                "WHERE event_type = 'source_held' "
                "AND status = 'needs_review' "
                "AND json_array_length(utterance_ids_json) > 0"
            )
        )
    ).rowcount or 0

    for table_name in ("memoir_automation_events", "memoir_editing_jobs"):
        table = Base.metadata.tables[table_name]
        existing_indexes = {
            str(row[1])
            for row in (
                await conn.execute(
                    text(f'PRAGMA index_list("{table_name}")')
                )
            ).fetchall()
        }
        for index in table.indexes:
            if (
                not index.name
                or index.name in existing_indexes
                or index.name not in AUXILIARY_QUERY_INDEXES
            ):
                continue
            await conn.run_sync(
                lambda sync_conn, index=index: index.create(
                    sync_conn,
                    checkfirst=True,
                )
            )
            changed_indexes.append(index.name)

    validation = """
        (
            NEW.event_type = 'source_held'
            AND NEW.status = 'needs_review'
            AND (
                NEW.review_utterance_id IS NULL
                OR json_array_length(NEW.utterance_ids_json) != 0
            )
        )
        OR (
            (NEW.review_utterance_id IS NOT NULL
             OR NEW.followup_task_id IS NOT NULL)
            AND NEW.event_type != 'source_held'
        )
        OR (
            NEW.followup_task_id IS NOT NULL
            AND NEW.review_utterance_id IS NULL
        )
        OR (
            NEW.review_utterance_id IS NOT NULL
            AND NOT EXISTS (
                SELECT 1
                FROM memoir_voice_utterances AS utterance
                JOIN memoir_voice_sessions AS session
                  ON session.id = utterance.voice_session_id
                WHERE utterance.id = NEW.review_utterance_id
                  AND session.project_id = NEW.project_id
            )
        )
        OR (
            NEW.followup_task_id IS NOT NULL
            AND NOT EXISTS (
                SELECT 1
                FROM memoir_followup_tasks AS task
                WHERE task.id = NEW.followup_task_id
                  AND task.project_id = NEW.project_id
            )
        )
        OR (
            NEW.editing_job_id IS NOT NULL
            AND NOT EXISTS (
                SELECT 1
                FROM memoir_editing_jobs AS job
                WHERE job.id = NEW.editing_job_id
                  AND job.project_id = NEW.project_id
            )
        )
    """
    statements = {
        "memoir_automation_events_links_insert": f"""
            CREATE TRIGGER memoir_automation_events_links_insert
            BEFORE INSERT ON memoir_automation_events
            WHEN {validation}
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'invalid memoir automation event direct links'
                );
            END
        """,
        "memoir_automation_events_links_update": f"""
            CREATE TRIGGER memoir_automation_events_links_update
            BEFORE UPDATE OF
                project_id,
                event_type,
                status,
                utterance_ids_json,
                review_utterance_id,
                followup_task_id,
                editing_job_id
            ON memoir_automation_events
            WHEN {validation}
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'invalid memoir automation event direct links'
                );
            END
        """,
    }
    for trigger_name, statement in statements.items():
        if await _trigger_exists(conn, trigger_name):
            continue
        await conn.execute(text(statement))
        changed_triggers.append(trigger_name)
    return {
        "changed_columns": changed_columns,
        "changed_indexes": changed_indexes,
        "changed_triggers": changed_triggers,
        "changed_rows": int(cleared_review_payloads),
    }


async def rebuild_memoir_voice_schema(engine: AsyncEngine) -> None:
    """Drop and recreate the current realtime voice schema for development."""
    tables = _memoir_voice_tables_from_metadata()
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA foreign_keys=OFF"))
        await _drop_transcript_fts(conn)
        for table in reversed(tables):
            await conn.run_sync(
                lambda sync_conn, current=table: current.drop(
                    sync_conn,
                    checkfirst=True,
                )
            )
        for table in tables:
            await conn.run_sync(
                lambda sync_conn, current=table: current.create(
                    sync_conn,
                    checkfirst=True,
                )
            )
        if not await _table_exists(conn, "memoir_followup_tasks"):
            followup_table = Base.metadata.tables["memoir_followup_tasks"]
            await conn.run_sync(
                lambda sync_conn: followup_table.create(
                    sync_conn,
                    checkfirst=True,
                )
            )
        await _ensure_voice_session_anchor_schema(conn)
        await _ensure_automation_event_link_schema(conn)
        await _ensure_transcript_fts(conn)
        await conn.execute(text("PRAGMA foreign_keys=ON"))


async def migrate_memoir_voice_schema(engine: AsyncEngine) -> dict[str, object]:
    """Install runtime-managed FTS objects for the exact current schema."""
    missing_tables: list[str] = []
    async with engine.begin() as conn:
        for table_name in (*REALTIME_VOICE_TABLES, *MEMOIR_AUXILIARY_TABLES):
            if not await _table_exists(conn, table_name):
                missing_tables.append(table_name)
        if missing_tables:
            raise RuntimeError(
                "Missing required tables: " + ", ".join(sorted(missing_tables))
            )
        anchor_result = await _ensure_voice_session_anchor_schema(conn)
        event_link_result = await _ensure_automation_event_link_schema(conn)
        fts_result = await _ensure_transcript_fts(conn)

    await assert_memoir_voice_schema(engine)

    return {
        "changed_tables": fts_result["changed_tables"],
        "changed_columns": [
            *anchor_result["changed_columns"],
            *event_link_result["changed_columns"],
        ],
        "changed_indexes": [
            *anchor_result["changed_indexes"],
            *event_link_result["changed_indexes"],
        ],
        "changed_triggers": [
            *anchor_result["changed_triggers"],
            *event_link_result["changed_triggers"],
            *fts_result["changed_triggers"],
        ],
        "changed_rows": event_link_result["changed_rows"],
    }


async def assert_memoir_voice_schema(engine: AsyncEngine) -> None:
    expected_schema = {
        table: {str(column["name"]): column for column in columns}
        for table, columns in expected_memoir_voice_schema().items()
    }
    missing_tables: list[str] = []
    missing_columns: list[str] = []
    unexpected_columns: list[str] = []
    column_mismatches: list[str] = []

    async with engine.begin() as conn:
        for table_name, expected_columns in expected_schema.items():
            if not await _table_exists(conn, table_name):
                missing_tables.append(table_name)
                continue
            actual_schema = {
                str(column["name"]): column
                for column in await _table_column_schema(conn, table_name)
            }
            expected_names = set(expected_columns)
            actual_names = set(actual_schema)
            missing_columns.extend(
                f"{table_name}.{column}"
                for column in sorted(expected_names - actual_names)
            )
            unexpected_columns.extend(
                f"{table_name}.{column}"
                for column in sorted(actual_names - expected_names)
            )
            for column_name in sorted(expected_names & actual_names):
                expected = expected_columns[column_name]
                actual = actual_schema[column_name]
                for attribute in ("nullable", "primary_key"):
                    if expected[attribute] != actual[attribute]:
                        column_mismatches.append(
                            f"{table_name}.{column_name}.{attribute} "
                            f"expected={expected[attribute]} "
                            f"actual={actual[attribute]}"
                        )

        for table_name in (TRANSCRIPT_FTS_TABLE,):
            if not await _table_exists(conn, table_name):
                missing_tables.append(table_name)
        for trigger_name in TRANSCRIPT_FTS_TRIGGERS:
            if not await _trigger_exists(conn, trigger_name):
                missing_tables.append(f"trigger:{trigger_name}")
        for trigger_name in VOICE_SESSION_ANCHOR_TRIGGERS:
            if not await _trigger_exists(conn, trigger_name):
                missing_tables.append(f"trigger:{trigger_name}")
        if not await _index_exists(
            conn,
            "ix_memoir_voice_sessions_anchor_framework_item_id",
        ):
            missing_tables.append(
                "index:ix_memoir_voice_sessions_anchor_framework_item_id"
            )
        for trigger_name in AUTOMATION_EVENT_LINK_TRIGGERS:
            if not await _trigger_exists(conn, trigger_name):
                missing_tables.append(f"trigger:{trigger_name}")
        for index_name in AUXILIARY_QUERY_INDEXES:
            if not await _index_exists(conn, index_name):
                missing_tables.append(f"index:{index_name}")

    details: list[str] = []
    for values, label in (
        (missing_tables, "missing tables"),
        (missing_columns, "missing columns"),
        (unexpected_columns, "unexpected columns"),
        (column_mismatches, "column mismatches"),
    ):
        if values:
            details.append(f"{label}: {', '.join(sorted(values))}")
    if details:
        raise RuntimeError(
            "Memoir realtime voice schema mismatch; "
            + "; ".join(details)
            + "; run `PYTHONPATH=backend python "
            "backend/scripts/migrate_runtime_schema.py`"
        )
