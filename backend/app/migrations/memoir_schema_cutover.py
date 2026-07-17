from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.database import Base, register_models
from app.domain.memoir.narrative_types import (
    MemoirNarrativeDecisionAction,
    MemoirNarrativeDecisionTargetType,
)
from app.domain.memoir.types import (
    MemoirAutomationEventStatus,
    MemoirAutomationEventType,
    MemoirEditingJobStatus,
    MemoirEditingJobType,
)


# These tables belonged to the superseded Story Slot, Gap/Follow-up Task and
# structured chapter draft implementations. Their data is intentionally not
# migrated into the current CompletionItem and whole-chapter draft models.
OBSOLETE_MEMOIR_TABLES = (
    "memoir_draft_block_claims",
    "memoir_draft_block_stories",
    "memoir_draft_block_versions",
    "memoir_draft_blocks",
    "memoir_chapter_node_stories",
    "memoir_chapter_nodes",
    "memoir_chapter_contracts",
    "memoir_followup_task_claims",
    "memoir_followup_task_answers",
    "memoir_utterance_interview_assignments",
    "memoir_followup_tasks",
    "memoir_narrative_gap_claims",
    "memoir_narrative_gap_stories",
    "memoir_narrative_gap_chapters",
    "memoir_narrative_gaps",
    "memoir_story_slot_claims",
    "memoir_story_slots",
    "memoir_story_revisions",
)

OBSOLETE_MEMOIR_TRIGGERS = (
    "memoir_voice_sessions_anchor_insert",
    "memoir_voice_sessions_anchor_update",
    "memoir_utterance_interview_assignments_immutable_update",
    "memoir_chapter_contracts_immutable_update",
    "memoir_chapter_contracts_immutable_delete",
    "memoir_chapter_nodes_immutable_insert",
    "memoir_chapter_nodes_immutable_update",
    "memoir_chapter_nodes_immutable_delete",
    "memoir_chapter_node_stories_immutable_insert",
    "memoir_chapter_node_stories_immutable_update",
    "memoir_chapter_node_stories_immutable_delete",
    "memoir_draft_blocks_identity_immutable",
    "memoir_draft_blocks_current_version_insert",
    "memoir_draft_blocks_current_version_update",
    "memoir_draft_blocks_factual_sources_insert",
    "memoir_draft_blocks_factual_sources_update",
    "memoir_draft_blocks_locked_version_update",
    "memoir_draft_block_versions_no_update",
    "memoir_draft_block_versions_no_delete",
    "memoir_draft_block_claims_no_update",
    "memoir_draft_block_claims_no_delete",
    "memoir_draft_block_stories_no_update",
    "memoir_draft_block_stories_no_delete",
)

# SQLite cannot remove columns or replace CHECK/FOREIGN KEY constraints in
# place. Only tables whose retained shape changed are rebuilt.
REBUILD_ORDER = (
    "memoir_book_plan_chapters",
    "memoir_stories",
    "memoir_voice_sessions",
    "memoir_voice_utterances",
    "memoir_editing_jobs",
    "memoir_automation_events",
    "memoir_narrative_decisions",
    "memoir_book_chapter_draft_versions",
)

OBSOLETE_COLUMNS = {
    "memoir_stories": {
        "completion_status",
        "slot_schema_version",
        "merged_into_story_id",
    },
    "memoir_voice_sessions": {"current_followup_task_id"},
    "memoir_automation_events": {"followup_task_id"},
    "memoir_narrative_decisions": {"source_followup_task_answer_id"},
    "memoir_book_chapter_draft_versions": {"block_snapshot_json"},
}

LEGACY_SQL_MARKERS = {
    "memoir_stories": (
        "completion_status",
        "slot_schema_version",
        "merged_into_story_id",
    ),
    "memoir_voice_sessions": ("current_followup_task_id",),
    "memoir_editing_jobs": (
        "generate_followup_tasks",
        "generate_draft_blocks",
    ),
    "memoir_automation_events": (
        "followup_task_id",
        "followup_tasks_generated",
    ),
    "memoir_narrative_decisions": (
        "source_followup_task_answer_id",
        "'story_slot'",
        "'followup_task'",
        "'chapter_contract'",
        "'draft_block'",
        "'accept_incomplete'",
        "'lock'",
        "'unlock'",
    ),
    "memoir_book_chapter_draft_versions": ("block_snapshot_json",),
}

REQUIRED_SQL_MARKERS = {
    "memoir_book_plan_chapters": (
        "uq_memoir_book_plan_chapters_project_id",
    ),
}


def _sql_values(values: Iterable[str]) -> str:
    return ", ".join("'" + value.replace("'", "''") + "'" for value in values)


CURRENT_JOB_TYPES = _sql_values(item.value for item in MemoirEditingJobType)
CURRENT_JOB_STATUSES = _sql_values(item.value for item in MemoirEditingJobStatus)
CURRENT_AUTOMATION_EVENT_TYPES = _sql_values(
    item.value for item in MemoirAutomationEventType
)
CURRENT_AUTOMATION_EVENT_STATUSES = _sql_values(
    item.value for item in MemoirAutomationEventStatus
)
CURRENT_DECISION_TARGETS = _sql_values(
    item.value for item in MemoirNarrativeDecisionTargetType
)
CURRENT_DECISION_ACTIONS = _sql_values(
    item.value for item in MemoirNarrativeDecisionAction
)


async def _object_names(conn: AsyncConnection, object_type: str) -> set[str]:
    rows = (
        await conn.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type = :object_type"
            ),
            {"object_type": object_type},
        )
    ).scalars()
    return {str(name) for name in rows}


async def _table_columns(conn: AsyncConnection, table_name: str) -> set[str]:
    rows = (
        await conn.execute(text(f'PRAGMA table_info("{table_name}")'))
    ).all()
    return {str(row[1]) for row in rows}


async def _table_sql(conn: AsyncConnection, table_name: str) -> str:
    value = await conn.scalar(
        text(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = :name"
        ),
        {"name": table_name},
    )
    return str(value or "")


async def _needs_rebuild(
    conn: AsyncConnection,
    table_name: str,
) -> bool:
    actual_columns = await _table_columns(conn, table_name)
    expected_columns = set(Base.metadata.tables[table_name].columns.keys())
    if expected_columns - actual_columns:
        return True
    if actual_columns & OBSOLETE_COLUMNS.get(table_name, set()):
        return True

    table_sql = (await _table_sql(conn, table_name)).lower()
    if any(
        marker.lower() in table_sql
        for marker in LEGACY_SQL_MARKERS.get(table_name, ())
    ):
        return True
    return any(
        marker.lower() not in table_sql
        for marker in REQUIRED_SQL_MARKERS.get(table_name, ())
    )


def _copy_filter(table_name: str) -> str:
    if table_name == "memoir_editing_jobs":
        return (
            f'legacy."job_type" IN ({CURRENT_JOB_TYPES}) '
            f'AND legacy."status" IN ({CURRENT_JOB_STATUSES})'
        )
    if table_name == "memoir_narrative_decisions":
        return (
            f'legacy."target_type" IN ({CURRENT_DECISION_TARGETS}) '
            f'AND legacy."action" IN ({CURRENT_DECISION_ACTIONS})'
        )
    if table_name == "memoir_automation_events":
        return (
            f'legacy."event_type" IN ({CURRENT_AUTOMATION_EVENT_TYPES}) '
            f'AND legacy."status" IN ({CURRENT_AUTOMATION_EVENT_STATUSES})'
        )
    return "1 = 1"


def _copy_expression(table_name: str, column_name: str) -> str:
    if table_name == "memoir_stories" and column_name == "lifecycle_status":
        return (
            "CASE WHEN legacy.\"lifecycle_status\" = 'merged' "
            "THEN 'archived' ELSE legacy.\"lifecycle_status\" END"
        )
    if table_name == "memoir_automation_events" and column_name == "editing_job_id":
        return (
            'CASE WHEN legacy."editing_job_id" IS NULL OR EXISTS ('
            'SELECT 1 FROM "memoir_editing_jobs" AS job '
            'WHERE job.id = legacy."editing_job_id"'
            ') THEN legacy."editing_job_id" ELSE NULL END'
        )
    return f'legacy."{column_name}"'


async def _drop_named_indexes(
    conn: AsyncConnection,
    table_name: str,
) -> None:
    rows = (
        await conn.execute(text(f'PRAGMA index_list("{table_name}")'))
    ).all()
    for row in rows:
        index_name = str(row[1])
        if index_name.startswith("sqlite_autoindex_"):
            continue
        await conn.execute(text(f'DROP INDEX "{index_name}"'))


async def _rebuild_table(
    conn: AsyncConnection,
    table_name: str,
) -> int:
    legacy_table = f"_{table_name}_before_cutover"
    await conn.execute(text(f'DROP TABLE IF EXISTS "{legacy_table}"'))
    source_columns = await _table_columns(conn, table_name)
    target_columns = list(Base.metadata.tables[table_name].columns.keys())
    copied_columns = [
        column_name
        for column_name in target_columns
        if column_name in source_columns
    ]
    before_count = int(
        await conn.scalar(text(f'SELECT COUNT(*) FROM "{table_name}"')) or 0
    )

    await conn.execute(
        text(f'ALTER TABLE "{table_name}" RENAME TO "{legacy_table}"')
    )
    await _drop_named_indexes(conn, legacy_table)
    await conn.run_sync(
        lambda sync_conn: Base.metadata.tables[table_name].create(
            sync_conn,
            checkfirst=False,
        )
    )

    if copied_columns:
        target_sql = ", ".join(
            ['"rowid"', *(f'"{name}"' for name in copied_columns)]
        )
        source_sql = ", ".join(
            [
                'legacy."rowid"',
                *(
                    _copy_expression(table_name, name)
                    for name in copied_columns
                ),
            ]
        )
        await conn.execute(
            text(
                f'INSERT INTO "{table_name}" ({target_sql}) '
                f"SELECT {source_sql} FROM \"{legacy_table}\" AS legacy "
                f"WHERE {_copy_filter(table_name)}"
            )
        )

    after_count = int(
        await conn.scalar(text(f'SELECT COUNT(*) FROM "{table_name}"')) or 0
    )
    await conn.execute(text(f'DROP TABLE "{legacy_table}"'))
    return before_count - after_count


async def _clear_discarded_job_references(
    conn: AsyncConnection,
) -> dict[str, int]:
    table_names = await _object_names(conn, "table")
    if "memoir_book_outputs" not in table_names:
        return {}
    result = await conn.execute(
        text(
            "UPDATE memoir_book_outputs "
            "SET formal_audit_job_id = NULL "
            "WHERE formal_audit_job_id IS NOT NULL "
            "AND NOT EXISTS ("
            "SELECT 1 FROM memoir_editing_jobs AS job "
            "WHERE job.id = memoir_book_outputs.formal_audit_job_id"
            ")"
        )
    )
    changed = max(0, int(result.rowcount or 0))
    return (
        {"memoir_book_outputs.formal_audit_job_id": changed}
        if changed
        else {}
    )


async def migrate_memoir_schema_cutover(
    engine: AsyncEngine,
) -> dict[str, object]:
    """Physically remove the superseded memoir schema without compatibility."""
    if engine.dialect.name != "sqlite":
        raise RuntimeError("Memoir schema cutover currently supports SQLite only")

    register_models()
    dropped_tables: list[str] = []
    dropped_triggers: list[str] = []
    rebuilt_tables: list[str] = []
    discarded_rows: dict[str, int] = {}
    cleared_references: dict[str, int] = {}

    async with engine.connect() as conn:
        foreign_keys_enabled = int(
            (await conn.execute(text("PRAGMA foreign_keys"))).scalar_one()
        )
        await conn.execute(text("PRAGMA foreign_keys = OFF"))
        await conn.execute(text("PRAGMA legacy_alter_table = ON"))
        await conn.commit()
        try:
            async with conn.begin():
                trigger_names = await _object_names(conn, "trigger")
                for trigger_name in OBSOLETE_MEMOIR_TRIGGERS:
                    if trigger_name not in trigger_names:
                        continue
                    await conn.execute(
                        text(f'DROP TRIGGER "{trigger_name}"')
                    )
                    dropped_triggers.append(trigger_name)

                table_names = await _object_names(conn, "table")
                for table_name in OBSOLETE_MEMOIR_TABLES:
                    if table_name not in table_names:
                        continue
                    await conn.execute(text(f'DROP TABLE "{table_name}"'))
                    dropped_tables.append(table_name)

                table_names = await _object_names(conn, "table")
                for table_name in REBUILD_ORDER:
                    if table_name not in table_names:
                        continue
                    if not await _needs_rebuild(conn, table_name):
                        continue
                    discarded_count = await _rebuild_table(conn, table_name)
                    rebuilt_tables.append(table_name)
                    if discarded_count:
                        discarded_rows[table_name] = discarded_count
                cleared_references = await _clear_discarded_job_references(
                    conn
                )
        finally:
            await conn.execute(text("PRAGMA legacy_alter_table = OFF"))
            await conn.execute(
                text(
                    "PRAGMA foreign_keys = "
                    f"{'ON' if foreign_keys_enabled else 'OFF'}"
                )
            )
            await conn.commit()

    return {
        "dropped_tables": dropped_tables,
        "dropped_triggers": dropped_triggers,
        "rebuilt_tables": rebuilt_tables,
        "discarded_rows": discarded_rows,
        "cleared_references": cleared_references,
    }
