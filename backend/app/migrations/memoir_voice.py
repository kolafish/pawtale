from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.schema import Column, Table

from app.database import Base, register_models
from app.domain.memoir.types import (
    MemoirAutomationEventType,
    MemoirChatMessageRole,
    MemoirEditingJobType,
    MemoirInterviewType,
    MemoirSessionGoal,
)
from app.migrations.sqlite_integrity import (
    legacy_foreign_key_tables,
    repair_sqlite_foreign_key_integrity,
)


REALTIME_VOICE_TABLES = (
    "audio_files",
    "memoir_voice_sessions",
    "memoir_chat_messages",
    "memoir_voice_utterances",
    "memoir_transcript_alignments",
    "memoir_material_share_videos",
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
TRANSCRIPT_VERSION_RETENTION_LIMIT = 3


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


async def _table_columns(conn, table: str) -> set[str]:
    result = await conn.execute(text(f"PRAGMA table_info({table})"))
    return {row[1] for row in result.fetchall()}


async def _table_exists(conn, table: str) -> bool:
    result = await conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = :table"),
        {"table": table},
    )
    return result.scalar_one_or_none() is not None


async def _trigger_exists(conn, trigger: str) -> bool:
    result = await conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type = 'trigger' AND name = :trigger"),
        {"trigger": trigger},
    )
    return result.scalar_one_or_none() is not None


async def _index_exists(conn, index: str) -> bool:
    result = await conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = :index"),
        {"index": index},
    )
    return result.scalar_one_or_none() is not None


async def _table_column_schema(conn, table: str) -> list[dict[str, object]]:
    result = await conn.execute(text(f"PRAGMA table_info({table})"))
    return [
        {
            "name": row[1],
            "nullable": not bool(row[3]),
            "primary_key": bool(row[5]),
        }
        for row in result.fetchall()
    ]


async def describe_memoir_voice_schema(engine: AsyncEngine) -> dict[str, list[dict[str, object]]]:
    actual: dict[str, list[dict[str, object]]] = {}
    async with engine.begin() as conn:
        for table_name in (*REALTIME_VOICE_TABLES, *MEMOIR_AUXILIARY_TABLES):
            if not await _table_exists(conn, table_name):
                actual[table_name] = []
                continue
            actual[table_name] = await _table_column_schema(conn, table_name)
    return actual


async def rebuild_memoir_voice_schema(engine: AsyncEngine) -> None:
    """Drop and recreate only realtime voice tables for development resets."""
    tables = _memoir_voice_tables_from_metadata()
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA foreign_keys=OFF"))
        await _drop_transcript_fts(conn)
        for table in reversed(tables):
            await conn.run_sync(lambda sync_conn, table=table: table.drop(sync_conn, checkfirst=True))
        for table in tables:
            await conn.run_sync(lambda sync_conn, table=table: table.create(sync_conn, checkfirst=True))
        await _ensure_transcript_fts(conn)
        await conn.execute(text("PRAGMA foreign_keys=ON"))


async def _create_metadata_table(conn, table_name: str) -> None:
    register_models()
    table = Base.metadata.tables[table_name]
    await conn.run_sync(lambda sync_conn: table.create(sync_conn, checkfirst=True))


async def _table_sql(conn, table_name: str) -> str:
    result = await conn.execute(
        text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = :table"),
        {"table": table_name},
    )
    return str(result.scalar_one_or_none() or "")


async def _ensure_enum_check_constraint(conn, table_name: str, enum_values: set[str]) -> bool:
    if not await _table_exists(conn, table_name):
        return False
    table_sql = await _table_sql(conn, table_name)
    if enum_values and all(value in table_sql for value in enum_values):
        return False

    register_models()
    table = Base.metadata.tables[table_name]
    columns = [column.name for column in table.columns]
    column_sql = ", ".join(columns)
    old_table_name = f"{table_name}_old"
    await conn.execute(text(f"ALTER TABLE {table_name} RENAME TO {old_table_name}"))
    old_indexes = (await conn.execute(text(f"PRAGMA index_list({old_table_name})"))).fetchall()
    for index in old_indexes:
        index_name = str(index[1])
        if index_name and not index_name.startswith("sqlite_autoindex"):
            await conn.execute(text(f"DROP INDEX IF EXISTS {index_name}"))
    await conn.run_sync(lambda sync_conn: table.create(sync_conn, checkfirst=True))
    select_sql = ", ".join(
        f"COALESCE({column}, CURRENT_TIMESTAMP) AS {column}"
        if column in {"created_at", "updated_at"}
        else column
        for column in columns
    )
    await conn.execute(
        text(
            f"""
            INSERT INTO {table_name} ({column_sql})
            SELECT {select_sql}
            FROM {old_table_name}
            """
        )
    )
    await conn.execute(text(f"DROP TABLE {old_table_name}"))
    return True


async def _ensure_editing_jobs_type_constraint(conn) -> bool:
    return await _ensure_enum_check_constraint(
        conn,
        "memoir_editing_jobs",
        {item.value for item in MemoirEditingJobType},
    )


async def _ensure_automation_events_type_constraint(conn) -> bool:
    return await _ensure_enum_check_constraint(
        conn,
        "memoir_automation_events",
        {item.value for item in MemoirAutomationEventType},
    )


async def _ensure_chat_messages_role_constraint(conn) -> bool:
    return await _ensure_enum_check_constraint(
        conn,
        "memoir_chat_messages",
        {item.value for item in MemoirChatMessageRole},
    )


async def _ensure_voice_sessions_enum_constraints(conn) -> bool:
    return await _ensure_enum_check_constraint(
        conn,
        "memoir_voice_sessions",
        {
            *(item.value for item in MemoirInterviewType),
            *(item.value for item in MemoirSessionGoal),
        },
    )


async def _drop_transcript_fts(conn) -> None:
    for trigger in TRANSCRIPT_FTS_TRIGGERS:
        await conn.execute(text(f"DROP TRIGGER IF EXISTS {trigger}"))
    await conn.execute(text(f"DROP TABLE IF EXISTS {TRANSCRIPT_FTS_TABLE}"))


async def _ensure_transcript_fts(conn) -> dict[str, list[str]]:
    changed_tables: list[str] = []
    changed_triggers: list[str] = []
    if not await _table_exists(conn, "memoir_voice_utterances"):
        return {"changed_tables": changed_tables, "changed_triggers": changed_triggers}

    created_fts = not await _table_exists(conn, TRANSCRIPT_FTS_TABLE)
    await conn.execute(
        text(
            f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS {TRANSCRIPT_FTS_TABLE}
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
    if created_fts:
        changed_tables.append(TRANSCRIPT_FTS_TABLE)
        await conn.execute(text(f"INSERT INTO {TRANSCRIPT_FTS_TABLE}({TRANSCRIPT_FTS_TABLE}) VALUES('rebuild')"))

    trigger_sql = {
        "memoir_voice_utterances_fts_ai": f"""
            CREATE TRIGGER IF NOT EXISTS memoir_voice_utterances_fts_ai
            AFTER INSERT ON memoir_voice_utterances
            BEGIN
                INSERT INTO {TRANSCRIPT_FTS_TABLE}(rowid, transcript_raw, transcript_edited)
                VALUES (new.rowid, new.transcript_raw, new.transcript_edited);
            END
        """,
        "memoir_voice_utterances_fts_ad": f"""
            CREATE TRIGGER IF NOT EXISTS memoir_voice_utterances_fts_ad
            AFTER DELETE ON memoir_voice_utterances
            BEGIN
                INSERT INTO {TRANSCRIPT_FTS_TABLE}({TRANSCRIPT_FTS_TABLE}, rowid, transcript_raw, transcript_edited)
                VALUES ('delete', old.rowid, old.transcript_raw, old.transcript_edited);
            END
        """,
        "memoir_voice_utterances_fts_au": f"""
            CREATE TRIGGER IF NOT EXISTS memoir_voice_utterances_fts_au
            AFTER UPDATE ON memoir_voice_utterances
            BEGIN
                INSERT INTO {TRANSCRIPT_FTS_TABLE}({TRANSCRIPT_FTS_TABLE}, rowid, transcript_raw, transcript_edited)
                VALUES ('delete', old.rowid, old.transcript_raw, old.transcript_edited);
                INSERT INTO {TRANSCRIPT_FTS_TABLE}(rowid, transcript_raw, transcript_edited)
                VALUES (new.rowid, new.transcript_raw, new.transcript_edited);
            END
        """,
    }
    for trigger, sql in trigger_sql.items():
        created_trigger = not await _trigger_exists(conn, trigger)
        await conn.execute(text(sql))
        if created_trigger:
            changed_triggers.append(trigger)

    return {"changed_tables": changed_tables, "changed_triggers": changed_triggers}


def _raw_asr_version_id(metadata_json: object) -> str | None:
    if metadata_json is None:
        return None
    value = metadata_json
    if isinstance(metadata_json, str):
        try:
            value = json.loads(metadata_json)
        except json.JSONDecodeError:
            return None
    if not isinstance(value, dict):
        return None
    version_id = value.get("asr_transcript_version_id")
    return version_id if isinstance(version_id, str) and version_id else None


async def _backfill_transcript_version_source_artifacts(conn) -> int:
    if not (
        await _table_exists(conn, "memoir_voice_utterances")
        and await _table_exists(conn, "memoir_chat_messages")
        and await _table_exists(conn, "memoir_transcript_versions")
    ):
        return 0
    rows = (
        await conn.execute(
            text(
                """
                SELECT
                    memoir_voice_utterances.id,
                    memoir_chat_messages.transcript_version_id,
                    memoir_chat_messages.metadata_json
                FROM memoir_voice_utterances
                JOIN memoir_chat_messages
                    ON memoir_voice_utterances.chat_message_id = memoir_chat_messages.id
                """
            )
        )
    ).fetchall()
    changed_rows = 0
    for utterance_id, corrected_version_id, metadata_json in rows:
        version_ids = [
            version_id
            for version_id in (corrected_version_id, _raw_asr_version_id(metadata_json))
            if version_id
        ]
        for version_id in dict.fromkeys(version_ids):
            result = await conn.execute(
                text(
                    """
                    UPDATE memoir_transcript_versions
                    SET source_artifact_id = :utterance_id
                    WHERE id = :version_id
                        AND source_artifact_id IS NULL
                    """
                ),
                {"utterance_id": utterance_id, "version_id": version_id},
            )
            changed_rows += int(result.rowcount or 0)
    return changed_rows


async def _prune_transcript_versions(conn) -> int:
    if not await _table_exists(conn, "memoir_transcript_versions"):
        return 0
    evidence_filter = ""
    if await _table_exists(conn, "memoir_claim_evidence"):
        evidence_filter = (
            "AND id NOT IN ("
            "SELECT transcript_version_id FROM memoir_claim_evidence "
            "WHERE transcript_version_id IS NOT NULL)"
        )
    delete_result = await conn.execute(
        text(
            f"""
            DELETE FROM memoir_transcript_versions
            WHERE id IN (
                SELECT id
                FROM (
                    SELECT
                        id,
                        row_number() OVER (
                            PARTITION BY source_artifact_id, version_type
                            ORDER BY created_at DESC, id DESC
                        ) AS version_rank
                    FROM memoir_transcript_versions
                    WHERE source_artifact_id IS NOT NULL
                    {evidence_filter}
                )
                WHERE version_rank > :retention_limit
            )
            """
        ),
        {"retention_limit": TRANSCRIPT_VERSION_RETENTION_LIMIT},
    )
    return int(delete_result.rowcount or 0)


async def migrate_memoir_voice_schema(engine: AsyncEngine) -> dict[str, object]:
    """Repair known realtime voice schema drift in existing SQLite databases."""
    changed_tables: list[str] = []
    changed_columns: list[str] = []
    changed_indexes: list[str] = []
    changed_triggers: list[str] = []
    changed_rows: list[str] = []
    async with engine.begin() as conn:
        for table_name in MEMOIR_AUXILIARY_TABLES:
            if not await _table_exists(conn, table_name):
                await _create_metadata_table(conn, table_name)
                changed_tables.append(table_name)

        if await _table_exists(conn, "memoir_editing_jobs"):
            editing_job_columns = await _table_columns(conn, "memoir_editing_jobs")
            editing_job_column_definitions = {
                "lease_token": "VARCHAR(36)",
                "lease_expires_at": "DATETIME",
            }
            for column_name, column_type in editing_job_column_definitions.items():
                if column_name not in editing_job_columns:
                    await conn.execute(
                        text(f"ALTER TABLE memoir_editing_jobs ADD COLUMN {column_name} {column_type}")
                    )
                    changed_columns.append(f"memoir_editing_jobs.{column_name}")

        if await _ensure_editing_jobs_type_constraint(conn):
            changed_tables.append("memoir_editing_jobs")
        if await _ensure_automation_events_type_constraint(conn):
            changed_tables.append("memoir_automation_events")

        if not await _table_exists(conn, "memoir_voice_authorization_consents"):
            await _create_metadata_table(conn, "memoir_voice_authorization_consents")
            changed_tables.append("memoir_voice_authorization_consents")

        if await _table_exists(conn, "memoir_voice_sessions"):
            voice_session_columns = await _table_columns(conn, "memoir_voice_sessions")
            voice_session_column_definitions = {
                "interview_type": "VARCHAR(30) NOT NULL DEFAULT 'ai_interview'",
                "session_goal": "VARCHAR(30) NOT NULL DEFAULT 'open_narration'",
                "current_followup_task_id": "VARCHAR(36)",
            }
            for column_name, column_type in voice_session_column_definitions.items():
                if column_name in voice_session_columns:
                    continue
                await conn.execute(
                    text(
                        f"ALTER TABLE memoir_voice_sessions "
                        f"ADD COLUMN {column_name} {column_type}"
                    )
                )
                changed_columns.append(f"memoir_voice_sessions.{column_name}")
            if await _ensure_voice_sessions_enum_constraints(conn):
                changed_tables.append("memoir_voice_sessions")

        if await _ensure_chat_messages_role_constraint(conn):
            changed_tables.append("memoir_chat_messages")

        if not await _table_exists(conn, "memoir_transcript_versions"):
            raise RuntimeError("Missing required table: memoir_transcript_versions")

        if await _table_exists(conn, "memoir_voice_utterances"):
            utterance_columns = await _table_columns(conn, "memoir_voice_utterances")
            utterance_column_definitions = {
                "source_material_type": "VARCHAR(40) NOT NULL DEFAULT ''",
                "source_route_action": "VARCHAR(40) NOT NULL DEFAULT ''",
                "source_route_confidence": "FLOAT",
                "source_route_reason": "TEXT NOT NULL DEFAULT ''",
                "source_route_updated_at": "DATETIME",
            }
            for column_name, column_type in utterance_column_definitions.items():
                if column_name not in utterance_columns:
                    await conn.execute(
                        text(f"ALTER TABLE memoir_voice_utterances ADD COLUMN {column_name} {column_type}")
                    )
                    changed_columns.append(f"memoir_voice_utterances.{column_name}")

        if not await _table_exists(conn, "memoir_transcript_alignments"):
            await _create_metadata_table(conn, "memoir_transcript_alignments")
            changed_tables.append("memoir_transcript_alignments")

        if not await _table_exists(conn, "memoir_material_share_videos"):
            await _create_metadata_table(conn, "memoir_material_share_videos")
            changed_tables.append("memoir_material_share_videos")
        share_video_columns = await _table_columns(conn, "memoir_material_share_videos")
        share_video_column_definitions = {
            "run_after": "DATETIME",
            "locked_at": "DATETIME",
            "lease_token": "VARCHAR(36)",
            "lease_expires_at": "DATETIME",
            "attempts": "INTEGER NOT NULL DEFAULT 0",
        }
        for column_name, column_type in share_video_column_definitions.items():
            if column_name not in share_video_columns:
                await conn.execute(
                    text(f"ALTER TABLE memoir_material_share_videos ADD COLUMN {column_name} {column_type}")
                )
                changed_columns.append(f"memoir_material_share_videos.{column_name}")
        obsolete_share_video_columns = {"style", "style_json"} & share_video_columns
        for column_name in sorted(obsolete_share_video_columns):
            await conn.execute(
                text(f"ALTER TABLE memoir_material_share_videos DROP COLUMN {column_name}")
            )
        if obsolete_share_video_columns and "memoir_material_share_videos" not in changed_tables:
            changed_tables.append("memoir_material_share_videos")

        lease_indexes = {
            "ix_memoir_editing_jobs_lease_expires_at": (
                "memoir_editing_jobs",
                "lease_expires_at",
            ),
            "ix_memoir_material_share_videos_run_after": (
                "memoir_material_share_videos",
                "run_after",
            ),
            "ix_memoir_material_share_videos_lease_expires_at": (
                "memoir_material_share_videos",
                "lease_expires_at",
            ),
        }
        for index_name, (table_name, column_name) in lease_indexes.items():
            if not await _index_exists(conn, index_name):
                await conn.execute(
                    text(f"CREATE INDEX {index_name} ON {table_name} ({column_name})")
                )
                changed_indexes.append(index_name)

        columns = await _table_columns(conn, "memoir_transcript_versions")
        if "voice_session_id" not in columns:
            await conn.execute(
                text("ALTER TABLE memoir_transcript_versions ADD COLUMN voice_session_id VARCHAR(36)")
            )
            changed_columns.append("memoir_transcript_versions.voice_session_id")
        if "source_artifact_id" not in columns:
            await conn.execute(
                text("ALTER TABLE memoir_transcript_versions ADD COLUMN source_artifact_id VARCHAR(36)")
            )
            changed_columns.append("memoir_transcript_versions.source_artifact_id")

        legacy_fk_tables = await legacy_foreign_key_tables(conn)
        if "memoir_voice_utterances" in legacy_fk_tables:
            await _drop_transcript_fts(conn)
        foreign_key_result = await repair_sqlite_foreign_key_integrity(conn)
        for table_name in foreign_key_result["rebuilt_tables"]:
            if table_name not in changed_tables:
                changed_tables.append(str(table_name))
        deleted_source_cards = int(
            foreign_key_result["deleted_derived_rows"].get("memoir_source_cards", 0)
        )
        if deleted_source_cards:
            changed_rows.append(f"memoir_source_cards.orphans:{deleted_source_cards}")

        transcript_version_indexes = {
            "ix_memoir_transcript_versions_voice_session_id": "voice_session_id",
            "ix_memoir_transcript_versions_source_type_created": (
                "source_artifact_id, version_type, created_at"
            ),
        }
        for index_name, columns_sql in transcript_version_indexes.items():
            if await _index_exists(conn, index_name):
                continue
            await conn.execute(
                text(f"CREATE INDEX {index_name} ON memoir_transcript_versions ({columns_sql})")
            )
            changed_indexes.append(index_name)

        fts_result = await _ensure_transcript_fts(conn)
        changed_tables.extend(fts_result["changed_tables"])
        changed_triggers.extend(fts_result["changed_triggers"])

        backfilled = await _backfill_transcript_version_source_artifacts(conn)
        if backfilled:
            changed_rows.append(f"memoir_transcript_versions.source_artifact_id:{backfilled}")
        pruned = await _prune_transcript_versions(conn)
        if pruned:
            changed_rows.append(f"memoir_transcript_versions.pruned:{pruned}")

    return {
        "changed_tables": changed_tables,
        "changed_columns": changed_columns,
        "changed_indexes": changed_indexes,
        "changed_triggers": changed_triggers,
        "changed_rows": changed_rows,
        "foreign_keys": foreign_key_result,
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
                            f"expected={expected[attribute]} actual={actual[attribute]}"
                        )

    if missing_tables or missing_columns or unexpected_columns or column_mismatches:
        details: list[str] = []
        if missing_tables:
            details.append(f"missing tables: {', '.join(sorted(missing_tables))}")
        if missing_columns:
            details.append(f"missing columns: {', '.join(sorted(missing_columns))}")
        if unexpected_columns:
            details.append(f"unexpected columns: {', '.join(sorted(unexpected_columns))}")
        if column_mismatches:
            details.append(f"column mismatches: {', '.join(sorted(column_mismatches))}")
        raise RuntimeError(
            "Memoir realtime voice schema mismatch; "
            + "; ".join(details)
            + "; for development databases without data to preserve, run "
            "`python scripts/rebuild_memoir_voice_schema.py --yes`"
        )
