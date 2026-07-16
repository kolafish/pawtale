from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.schema import Column, Table

from app.database import Base, register_models
from app.migrations.memoir_draft_blocks import (
    MEMOIR_DRAFT_BLOCK_TRIGGERS,
    ensure_draft_block_triggers,
)


MEMOIR_NARRATIVE_TABLES = (
    "memoir_narrative_entities",
    "memoir_claims",
    "memoir_claim_evidence",
    "memoir_stories",
    "memoir_story_revisions",
    "memoir_story_claims",
    "memoir_story_slots",
    "memoir_story_slot_claims",
    "memoir_chapter_contracts",
    "memoir_chapter_nodes",
    "memoir_chapter_node_stories",
    "memoir_narrative_gaps",
    "memoir_narrative_gap_claims",
    "memoir_narrative_gap_stories",
    "memoir_narrative_gap_chapters",
    "memoir_followup_tasks",
    "memoir_followup_task_claims",
    "memoir_followup_task_answers",
    "memoir_interview_policy_decisions",
    "memoir_interview_policy_evaluations",
    "memoir_utterance_interview_assignments",
    "memoir_narrative_decisions",
    "memoir_draft_blocks",
    "memoir_draft_block_versions",
    "memoir_draft_block_claims",
    "memoir_draft_block_stories",
)
MEMOIR_NARRATIVE_DECISION_TRIGGERS = (
    "memoir_narrative_decisions_no_update",
    "memoir_narrative_decisions_no_delete",
)
MEMOIR_INTERVIEW_POLICY_TRIGGERS = (
    "memoir_utterance_interview_assignments_immutable_update",
    "memoir_interview_policy_decisions_immutable_selection",
    "memoir_interview_policy_decisions_candidate_membership",
    "memoir_interview_policy_decisions_append_only_evaluation",
    "memoir_interview_policy_evaluations_failure_codes",
    "memoir_interview_policy_evaluations_no_update",
    "memoir_interview_policy_evaluations_no_delete",
)
MEMOIR_CHAPTER_STRUCTURE_TRIGGERS = (
    "memoir_chapter_contracts_immutable_update",
    "memoir_chapter_contracts_immutable_delete",
    "memoir_chapter_nodes_immutable_insert",
    "memoir_chapter_nodes_immutable_update",
    "memoir_chapter_nodes_immutable_delete",
    "memoir_chapter_node_stories_immutable_insert",
    "memoir_chapter_node_stories_immutable_update",
    "memoir_chapter_node_stories_immutable_delete",
)
MEMOIR_NARRATIVE_TRIGGERS = (
    *MEMOIR_NARRATIVE_DECISION_TRIGGERS,
    *MEMOIR_INTERVIEW_POLICY_TRIGGERS,
    *MEMOIR_CHAPTER_STRUCTURE_TRIGGERS,
    *MEMOIR_DRAFT_BLOCK_TRIGGERS,
)
MEMOIR_BOOK_OUTPUT_FORMAL_COLUMNS = {
    "formal_version_id": "VARCHAR(36)",
    "formal_audit_job_id": "VARCHAR(36)",
    "formalized_by_user_id": "VARCHAR(36)",
    "formalized_at": "DATETIME",
}
MEMOIR_BOOK_PLAN_CHAPTER_INCLUDE_COLUMN = "include_in_book"
FOLLOWUP_TASK_ANSWER_USE_COLUMN = "answer_use"
NARRATIVE_GAP_ACTION_COLUMNS = {
    "recommended_action": "VARCHAR(24) NOT NULL DEFAULT 'manual_review'",
    "action_reason_code": (
        "VARCHAR(48) NOT NULL DEFAULT 'migration_review_required'"
    ),
    "action_decision_source": "VARCHAR(32) NOT NULL DEFAULT 'migration'",
    "action_policy_version": "VARCHAR(24) NOT NULL DEFAULT 'migration-1'",
}
NARRATIVE_GAP_ACTION_CONSTRAINTS = {
    "ck_memoir_narrative_gaps_recommended_action",
    "ck_memoir_narrative_gaps_action_reason_code",
    "ck_memoir_narrative_gaps_action_decision_source",
    "ck_memoir_narrative_gaps_action_policy_version",
}
FOLLOWUP_TASK_OUTCOME_STATUS_RENAMES = {
    "cannot_recall": "unable_to_answer",
    "excluded": "closed_without_answer",
}
FOLLOWUP_ANSWER_USE_BY_GAP_TYPE = {
    "missing_slot": "direct_narrative",
    "chronology": "clarification",
    "relationship": "clarification",
    "causality": "clarification",
    "unclear_reference": "clarification",
    "contradiction": "fact_resolution",
    "repetition": "fact_resolution",
    "unsupported_fact": "fact_resolution",
}
LEGACY_FOLLOWUP_GENERATION_JOB_TYPE = "generate_followup_tasks"
LEGACY_FOLLOWUP_GENERATION_EVENT_TYPE = "followup_tasks_generated"
INTERVIEW_POLICY_SHADOW_JOB_TYPE = "evaluate_interview_policy_shadow"
INTERVIEW_POLICY_FAILURE_CODES = (
    "action_outside_candidates",
    "interrupted_user",
    "repeated_question",
    "wrong_topic_switch",
    "privacy_violation",
)


def _narrative_tables_from_metadata() -> tuple[Table, ...]:
    register_models()
    return tuple(Base.metadata.tables[table_name] for table_name in MEMOIR_NARRATIVE_TABLES)


def _column_schema(column: Column) -> dict[str, object]:
    return {
        "name": column.name,
        "nullable": bool(column.nullable),
        "primary_key": bool(column.primary_key),
    }


def expected_memoir_narrative_schema() -> dict[str, list[dict[str, object]]]:
    return {
        table.name: [_column_schema(column) for column in table.columns]
        for table in _narrative_tables_from_metadata()
    }


async def _table_exists(conn, table_name: str) -> bool:
    result = await conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = :table_name"),
        {"table_name": table_name},
    )
    return result.scalar_one_or_none() is not None


async def _retire_legacy_completion_rows(conn) -> None:
    if await _table_exists(conn, "memoir_editing_jobs"):
        await conn.execute(
            text(
                "DELETE FROM memoir_editing_jobs "
                "WHERE job_type = :legacy_job_type"
            ),
            {"legacy_job_type": LEGACY_FOLLOWUP_GENERATION_JOB_TYPE},
        )
    if await _table_exists(conn, "memoir_automation_events"):
        await conn.execute(
            text(
                "UPDATE memoir_automation_events "
                "SET event_type = 'audit_completed' "
                "WHERE event_type = :legacy_event_type"
            ),
            {"legacy_event_type": LEGACY_FOLLOWUP_GENERATION_EVENT_TYPE},
        )


async def _table_column_schema(conn, table_name: str) -> list[dict[str, object]]:
    rows = (await conn.execute(text(f"PRAGMA table_info({table_name})"))).fetchall()
    return [
        {
            "name": row[1],
            "nullable": not bool(row[3]),
            "primary_key": bool(row[5]),
        }
        for row in rows
    ]


async def _ensure_structured_draft_snapshot_column(conn) -> list[str]:
    table_name = "memoir_book_chapter_draft_versions"
    if not await _table_exists(conn, table_name):
        return []
    columns = {
        str(column["name"]) for column in await _table_column_schema(conn, table_name)
    }
    if "block_snapshot_json" in columns:
        return []
    await conn.execute(
        text(
            "ALTER TABLE memoir_book_chapter_draft_versions "
            "ADD COLUMN block_snapshot_json JSON NOT NULL DEFAULT '[]'"
        )
    )
    return ["memoir_book_chapter_draft_versions.block_snapshot_json"]


async def _ensure_book_output_formal_columns(conn) -> list[str]:
    table_name = "memoir_book_outputs"
    if not await _table_exists(conn, table_name):
        return []
    columns = {
        str(column["name"])
        for column in await _table_column_schema(conn, table_name)
    }
    changed: list[str] = []
    for column_name, column_type in MEMOIR_BOOK_OUTPUT_FORMAL_COLUMNS.items():
        if column_name in columns:
            continue
        await conn.execute(
            text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
        )
        changed.append(f"{table_name}.{column_name}")
    return changed


async def _ensure_book_plan_chapter_include_column(conn) -> list[str]:
    table_name = "memoir_book_plan_chapters"
    if not await _table_exists(conn, table_name):
        return []
    columns = {
        str(column["name"])
        for column in await _table_column_schema(conn, table_name)
    }
    if MEMOIR_BOOK_PLAN_CHAPTER_INCLUDE_COLUMN in columns:
        return []
    await conn.execute(
        text(
            f"ALTER TABLE {table_name} "
            f"ADD COLUMN {MEMOIR_BOOK_PLAN_CHAPTER_INCLUDE_COLUMN} "
            "BOOLEAN NOT NULL DEFAULT 1"
        )
    )
    return [f"{table_name}.{MEMOIR_BOOK_PLAN_CHAPTER_INCLUDE_COLUMN}"]


async def _ensure_followup_task_answer_use_column(conn) -> list[str]:
    table_name = "memoir_followup_tasks"
    if not await _table_exists(conn, table_name):
        return []
    columns = {
        str(column["name"])
        for column in await _table_column_schema(conn, table_name)
    }
    if FOLLOWUP_TASK_ANSWER_USE_COLUMN in columns:
        return []
    unknown_gap_types = set(
        (
            await conn.execute(
                text(
                    "SELECT DISTINCT gap_type FROM memoir_narrative_gaps "
                    "WHERE gap_type NOT IN "
                    "('missing_slot', 'chronology', 'relationship', 'causality', "
                    "'contradiction', 'repetition', 'unclear_reference', 'unsupported_fact')"
                )
            )
        ).scalars()
    )
    if unknown_gap_types:
        raise RuntimeError(
            "Cannot migrate follow-up answer use for unknown gap types: "
            + ", ".join(sorted(str(item) for item in unknown_gap_types))
        )
    await conn.execute(
        text(
            f"ALTER TABLE {table_name} ADD COLUMN {FOLLOWUP_TASK_ANSWER_USE_COLUMN} "
            "VARCHAR(24) NOT NULL DEFAULT 'direct_narrative'"
        )
    )
    cases = " ".join(
        f"WHEN '{gap_type}' THEN '{answer_use}'"
        for gap_type, answer_use in FOLLOWUP_ANSWER_USE_BY_GAP_TYPE.items()
    )
    await conn.execute(
        text(
            f"UPDATE {table_name} SET {FOLLOWUP_TASK_ANSWER_USE_COLUMN} = CASE "
            "(SELECT gap_type FROM memoir_narrative_gaps "
            f"WHERE memoir_narrative_gaps.id = {table_name}.gap_id) {cases} END"
        )
    )
    return [f"{table_name}.{FOLLOWUP_TASK_ANSWER_USE_COLUMN}"]


async def _ensure_narrative_gap_action_columns(conn) -> list[str]:
    table_name = "memoir_narrative_gaps"
    if not await _table_exists(conn, table_name):
        return []
    columns = {
        str(column["name"])
        for column in await _table_column_schema(conn, table_name)
    }
    changed: list[str] = []
    for column_name, column_type in NARRATIVE_GAP_ACTION_COLUMNS.items():
        if column_name in columns:
            continue
        await conn.execute(
            text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
        )
        changed.append(f"{table_name}.{column_name}")

    await conn.execute(
        text(
            "UPDATE memoir_narrative_gaps AS gap "
            "SET recommended_action = 'interview', "
            "action_reason_code = 'missing_new_evidence', "
            "action_decision_source = 'migration', "
            "action_policy_version = 'migration-1' "
            "WHERE gap.action_decision_source = 'migration' "
            "AND EXISTS ("
            "SELECT 1 FROM memoir_followup_tasks AS task "
            "WHERE task.project_id = gap.project_id "
            "AND task.gap_id = gap.id "
            "AND task.status IN ('pending', 'in_progress', 'deferred')"
            ")"
        )
    )
    await conn.execute(
        text(
            "UPDATE memoir_narrative_gaps AS gap "
            "SET recommended_action = 'editorial', "
            "action_reason_code = 'existing_material_available', "
            "action_decision_source = 'migration', "
            "action_policy_version = 'migration-1' "
            "WHERE gap.gap_type = 'repetition' "
            "AND gap.action_decision_source = 'migration' "
            "AND NOT EXISTS ("
            "SELECT 1 FROM memoir_followup_tasks AS task "
            "WHERE task.project_id = gap.project_id "
            "AND task.gap_id = gap.id "
            "AND task.status IN ('pending', 'in_progress', 'deferred')"
            ")"
        )
    )
    return changed


async def _ensure_narrative_gap_action_constraints(engine: AsyncEngine) -> bool:
    """Rebuild the SQLite table when action columns lack their CHECK constraints."""
    register_models()
    table_name = "memoir_narrative_gaps"
    legacy_table_name = f"{table_name}_before_action_policy"
    async with engine.connect() as conn:
        table_sql = await _table_sql(conn, table_name)
        if not table_sql or all(
            constraint_name in table_sql
            for constraint_name in NARRATIVE_GAP_ACTION_CONSTRAINTS
        ):
            await conn.rollback()
            return False

        columns = {
            str(column["name"])
            for column in await _table_column_schema(conn, table_name)
        }
        missing_columns = sorted(set(NARRATIVE_GAP_ACTION_COLUMNS) - columns)
        if missing_columns:
            raise RuntimeError(
                "Cannot rebuild memoir narrative gap action constraints before "
                f"adding columns: {', '.join(missing_columns)}"
            )

        await conn.rollback()
        await conn.execute(text("PRAGMA foreign_keys=OFF"))
        await conn.commit()
        try:
            async with conn.begin():
                await conn.execute(text("PRAGMA legacy_alter_table=ON"))
                await conn.execute(
                    text(f"ALTER TABLE {table_name} RENAME TO {legacy_table_name}")
                )
                for index_name in await _index_names(conn, legacy_table_name):
                    if not index_name.startswith("sqlite_autoindex"):
                        await conn.execute(text(f'DROP INDEX IF EXISTS "{index_name}"'))

                table = Base.metadata.tables[table_name]
                await conn.run_sync(
                    lambda sync_conn: table.create(sync_conn, checkfirst=False)
                )
                legacy_columns = {
                    str(column["name"])
                    for column in await _table_column_schema(conn, legacy_table_name)
                }
                copied_columns = [
                    column.name for column in table.columns if column.name in legacy_columns
                ]
                column_names = ", ".join(f'"{name}"' for name in copied_columns)
                await conn.execute(
                    text(
                        f"INSERT INTO {table_name} ({column_names}) "
                        f"SELECT {column_names} FROM {legacy_table_name}"
                    )
                )
                await conn.execute(text(f"DROP TABLE {legacy_table_name}"))
                await conn.execute(text("PRAGMA legacy_alter_table=OFF"))
        finally:
            await conn.execute(text("PRAGMA legacy_alter_table=OFF"))
            await conn.execute(text("PRAGMA foreign_keys=ON"))
            await conn.commit()

        async with conn.begin():
            violations = (await conn.execute(text("PRAGMA foreign_key_check"))).fetchall()
            if violations:
                raise RuntimeError(
                    "Narrative gap action migration left foreign key violations: "
                    + ", ".join(str(tuple(row)) for row in violations)
                )
    return True


async def _ensure_editing_job_type_constraint(engine: AsyncEngine) -> bool:
    """Rebuild the SQLite job table when the policy shadow type is unavailable."""
    register_models()
    table_name = "memoir_editing_jobs"
    legacy_table_name = f"{table_name}_before_policy_shadow"
    async with engine.connect() as conn:
        table_sql = await _table_sql(conn, table_name)
        if not table_sql or f"'{INTERVIEW_POLICY_SHADOW_JOB_TYPE}'" in table_sql:
            await conn.rollback()
            return False

        await conn.rollback()
        await conn.execute(text("PRAGMA foreign_keys=OFF"))
        await conn.commit()
        try:
            async with conn.begin():
                await conn.execute(text("PRAGMA legacy_alter_table=ON"))
                await conn.execute(
                    text(f"ALTER TABLE {table_name} RENAME TO {legacy_table_name}")
                )
                for index_name in await _index_names(conn, legacy_table_name):
                    if not index_name.startswith("sqlite_autoindex"):
                        await conn.execute(text(f'DROP INDEX IF EXISTS "{index_name}"'))

                table = Base.metadata.tables[table_name]
                await conn.run_sync(
                    lambda sync_conn: table.create(sync_conn, checkfirst=False)
                )
                legacy_columns = {
                    str(column["name"])
                    for column in await _table_column_schema(conn, legacy_table_name)
                }
                copied_columns = [
                    column.name for column in table.columns if column.name in legacy_columns
                ]
                column_names = ", ".join(f'"{name}"' for name in copied_columns)
                await conn.execute(
                    text(
                        f"INSERT INTO {table_name} ({column_names}) "
                        f"SELECT {column_names} FROM {legacy_table_name}"
                    )
                )
                await conn.execute(text(f"DROP TABLE {legacy_table_name}"))
                await conn.execute(text("PRAGMA legacy_alter_table=OFF"))
        finally:
            await conn.execute(text("PRAGMA legacy_alter_table=OFF"))
            await conn.execute(text("PRAGMA foreign_keys=ON"))
            await conn.commit()

        async with conn.begin():
            violations = (await conn.execute(text("PRAGMA foreign_key_check"))).fetchall()
            if violations:
                raise RuntimeError(
                    "Editing job type migration left foreign key violations: "
                    + ", ".join(str(tuple(row)) for row in violations)
                )
    return True


async def _ensure_followup_task_outcome_schema(engine: AsyncEngine) -> bool:
    """Rebuild the SQLite table so terminal task outcomes have local semantics."""
    table_name = "memoir_followup_tasks"
    legacy_table_name = f"{table_name}_before_outcome_split"
    async with engine.connect() as conn:
        table_sql = await _table_sql(conn, table_name)
        if not table_sql or (
            "'unable_to_answer'" in table_sql
            and "'closed_without_answer'" in table_sql
            and "'cannot_recall'" not in table_sql
        ):
            await conn.rollback()
            return False
        await conn.rollback()
        await conn.execute(text("PRAGMA foreign_keys=OFF"))
        await conn.commit()
        try:
            async with conn.begin():
                await conn.execute(text("PRAGMA legacy_alter_table=ON"))
                await conn.execute(
                    text(f"ALTER TABLE {table_name} RENAME TO {legacy_table_name}")
                )
                for index_name in await _index_names(conn, legacy_table_name):
                    if not index_name.startswith("sqlite_autoindex"):
                        await conn.execute(text(f'DROP INDEX IF EXISTS "{index_name}"'))

                table = Base.metadata.tables[table_name]
                await conn.run_sync(
                    lambda sync_conn: table.create(sync_conn, checkfirst=False)
                )
                legacy_columns = {
                    str(column["name"])
                    for column in await _table_column_schema(conn, legacy_table_name)
                }
                copied_columns = [
                    column.name for column in table.columns if column.name in legacy_columns
                ]
                insert_columns = ", ".join(f'"{name}"' for name in copied_columns)
                select_columns = ", ".join(
                    (
                        "CASE status "
                        + " ".join(
                            f"WHEN '{old}' THEN '{new}'"
                            for old, new in FOLLOWUP_TASK_OUTCOME_STATUS_RENAMES.items()
                        )
                        + " ELSE status END AS status"
                    )
                    if name == "status"
                    else f'"{name}"'
                    for name in copied_columns
                )
                await conn.execute(
                    text(
                        f"INSERT INTO {table_name} ({insert_columns}) "
                        f"SELECT {select_columns} FROM {legacy_table_name}"
                    )
                )
                await conn.execute(text(f"DROP TABLE {legacy_table_name}"))
                await conn.execute(text("PRAGMA legacy_alter_table=OFF"))
        finally:
            await conn.execute(text("PRAGMA legacy_alter_table=OFF"))
            await conn.execute(text("PRAGMA foreign_keys=ON"))
            await conn.commit()
        async with conn.begin():
            violations = (await conn.execute(text("PRAGMA foreign_key_check"))).fetchall()
            if violations:
                raise RuntimeError(
                    "Follow-up task outcome migration left foreign key violations: "
                    + ", ".join(str(tuple(row)) for row in violations)
                )
    return True


async def _migrate_followup_answer_assignment_schema(
    engine: AsyncEngine,
) -> list[str]:
    """Split the legacy task/utterance table without losing answer or decision IDs."""
    register_models()
    legacy_answer_table = "memoir_followup_task_utterances"
    answer_table = "memoir_followup_task_answers"
    assignment_table = "memoir_utterance_interview_assignments"
    policy_table = "memoir_interview_policy_decisions"
    decision_table = "memoir_narrative_decisions"
    legacy_decision_table = f"{decision_table}_before_answer_assignment_split"
    current_tables = (answer_table, policy_table, assignment_table)

    async with engine.connect() as conn:
        if not await _table_exists(conn, legacy_answer_table):
            await conn.rollback()
            return []
        empty_current_tables: set[str] = set()
        nonempty_current_tables: list[str] = []
        for table_name in current_tables:
            if not await _table_exists(conn, table_name):
                continue
            row_count = int(
                (
                    await conn.execute(
                        text(f'SELECT COUNT(*) FROM "{table_name}"')
                    )
                ).scalar_one()
            )
            if row_count:
                nonempty_current_tables.append(table_name)
            else:
                empty_current_tables.add(table_name)
        if nonempty_current_tables:
            raise RuntimeError(
                "Legacy follow-up answer table exists alongside non-empty current "
                "tables; manual migration recovery is required: "
                + ", ".join(sorted(nonempty_current_tables))
            )

        await conn.rollback()
        await conn.execute(text("PRAGMA foreign_keys=OFF"))
        await conn.commit()
        try:
            async with conn.begin():
                await conn.execute(text("PRAGMA legacy_alter_table=ON"))
                for trigger_name in MEMOIR_NARRATIVE_DECISION_TRIGGERS:
                    await conn.execute(text(f"DROP TRIGGER IF EXISTS {trigger_name}"))

                has_decision_table = await _table_exists(conn, decision_table)
                if has_decision_table:
                    await conn.execute(
                        text(
                            f"ALTER TABLE {decision_table} "
                            f"RENAME TO {legacy_decision_table}"
                        )
                    )
                    for index_name in await _index_names(conn, legacy_decision_table):
                        if not index_name.startswith("sqlite_autoindex"):
                            await conn.execute(
                                text(f'DROP INDEX IF EXISTS "{index_name}"')
                            )

                for index_name in await _index_names(conn, legacy_answer_table):
                    if not index_name.startswith("sqlite_autoindex"):
                        await conn.execute(
                            text(f'DROP INDEX IF EXISTS "{index_name}"')
                        )

                for table_name in (assignment_table, policy_table, answer_table):
                    if table_name in empty_current_tables:
                        await conn.execute(text(f'DROP TABLE "{table_name}"'))

                for table_name in current_tables:
                    table = Base.metadata.tables[table_name]
                    await conn.run_sync(
                        lambda sync_conn, table=table: table.create(
                            sync_conn,
                            checkfirst=False,
                        )
                    )

                legacy_columns = {
                    str(column["name"])
                    for column in await _table_column_schema(
                        conn,
                        legacy_answer_table,
                    )
                }
                answer_columns = [
                    column.name
                    for column in Base.metadata.tables[answer_table].columns
                ]
                defaults = {
                    "interpretation_status": "'pending'",
                    "answer_kind": "NULL",
                    "direct_narrative_text": "''",
                    "clarification_text": "''",
                    "fact_resolution_text": "''",
                    "confirmation_question": "''",
                    "interpretation_reason": "''",
                }
                answer_select = [
                    f'"{name}"'
                    if name in legacy_columns
                    else defaults[name]
                    for name in answer_columns
                ]
                insert_columns = ", ".join(f'"{name}"' for name in answer_columns)
                await conn.execute(
                    text(
                        f"INSERT INTO {answer_table} ({insert_columns}) "
                        f"SELECT {', '.join(answer_select)} "
                        f"FROM {legacy_answer_table}"
                    )
                )

                if "is_assignment_snapshot" in legacy_columns:
                    await conn.execute(
                        text(
                            f"INSERT INTO {assignment_table} "
                            "(id, utterance_id, followup_task_id, "
                            "policy_decision_id, created_at) "
                            "SELECT id, utterance_id, task_id, NULL, created_at "
                            "FROM ("
                            "SELECT id, utterance_id, task_id, created_at, "
                            "ROW_NUMBER() OVER ("
                            "PARTITION BY utterance_id "
                            "ORDER BY created_at, id"
                            ") AS assignment_rank "
                            f"FROM {legacy_answer_table} "
                            "WHERE is_assignment_snapshot = 1"
                            ") AS ranked "
                            "WHERE assignment_rank = 1"
                        )
                    )

                if has_decision_table:
                    decision = Base.metadata.tables[decision_table]
                    await conn.run_sync(
                        lambda sync_conn: decision.create(
                            sync_conn,
                            checkfirst=False,
                        )
                    )
                    legacy_decision_columns = {
                        str(column["name"])
                        for column in await _table_column_schema(
                            conn,
                            legacy_decision_table,
                        )
                    }
                    target_columns: list[str] = []
                    source_expressions: list[str] = []
                    legacy_answer_source = next(
                        (
                            column_name
                            for column_name in (
                                "source_followup_task_answer_id",
                                "source_followup_task_utterance_id",
                            )
                            if column_name in legacy_decision_columns
                        ),
                        None,
                    )
                    for column in decision.columns:
                        target_columns.append(column.name)
                        if column.name == "source_followup_task_answer_id":
                            source_expressions.append(
                                f'"{legacy_answer_source}"'
                                if legacy_answer_source
                                else "NULL"
                            )
                        elif column.name == "source_transcript_version_id":
                            source_expressions.append(
                                '"source_transcript_version_id"'
                                if legacy_answer_source
                                and "source_transcript_version_id"
                                in legacy_decision_columns
                                else "NULL"
                            )
                        elif column.name in legacy_decision_columns:
                            source_expressions.append(f'"{column.name}"')
                        else:
                            raise RuntimeError(
                                "Cannot migrate narrative decisions; missing column "
                                f"{column.name}"
                            )
                    await conn.execute(
                        text(
                            f"INSERT INTO {decision_table} "
                            f"({', '.join(f'\"{name}\"' for name in target_columns)}) "
                            f"SELECT {', '.join(source_expressions)} "
                            f"FROM {legacy_decision_table}"
                        )
                    )
                    await conn.execute(text(f"DROP TABLE {legacy_decision_table}"))

                await conn.execute(text(f"DROP TABLE {legacy_answer_table}"))
                await conn.execute(text("PRAGMA legacy_alter_table=OFF"))
        finally:
            await conn.execute(text("PRAGMA legacy_alter_table=OFF"))
            await conn.execute(text("PRAGMA foreign_keys=ON"))
            await conn.commit()

        async with conn.begin():
            violations = (
                await conn.execute(text("PRAGMA foreign_key_check"))
            ).fetchall()
            if violations:
                raise RuntimeError(
                    "Follow-up answer assignment migration left foreign key violations: "
                    + ", ".join(str(tuple(row)) for row in violations)
                )
    changed = [answer_table, assignment_table, policy_table]
    if has_decision_table:
        changed.append(decision_table)
    return changed


async def _backfill_semantic_followup_task_target_slots(conn) -> None:
    if not await _table_exists(conn, "memoir_followup_tasks") or not await _table_exists(
        conn,
        "memoir_narrative_gaps",
    ):
        return
    await conn.execute(
        text(
            "UPDATE memoir_followup_tasks AS task "
            "SET target_slot = ("
            "SELECT gap.target_slot FROM memoir_narrative_gaps AS gap "
            "WHERE gap.project_id = task.project_id AND gap.id = task.gap_id"
            ") "
            "WHERE COALESCE(task.target_slot, '') = '' "
            "AND EXISTS ("
            "SELECT 1 FROM memoir_narrative_gaps AS gap "
            "WHERE gap.project_id = task.project_id AND gap.id = task.gap_id "
            "AND gap.target_slot LIKE 'semantic:%'"
            ")"
        )
    )


async def _table_sql(conn, table_name: str) -> str:
    result = await conn.execute(
        text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = :table_name"),
        {"table_name": table_name},
    )
    return str(result.scalar_one_or_none() or "")


async def _remove_derived_empty_story_slots(conn) -> None:
    if not await _table_exists(conn, "memoir_story_slots"):
        return
    await conn.execute(
        text(
            "DELETE FROM memoir_story_slots "
            "WHERE status = 'missing' "
            "AND resolution_actor_type IS NULL "
            "AND NOT EXISTS ("
            "SELECT 1 FROM memoir_story_slot_claims AS relation "
            "WHERE relation.project_id = memoir_story_slots.project_id "
            "AND relation.story_slot_id = memoir_story_slots.id"
            ")"
        )
    )


async def _ensure_decision_schema(conn) -> bool:
    table_name = "memoir_narrative_decisions"
    table_sql = await _table_sql(conn, table_name)
    if not table_sql:
        return False
    columns = {
        str(column["name"])
        for column in await _table_column_schema(conn, table_name)
    }
    required_columns = {
        "source_followup_task_answer_id",
        "source_transcript_version_id",
    }
    required_constraints = {
        "fk_memoir_narrative_decisions_followup_source_project",
        "fk_memoir_narrative_decisions_transcript_source",
        "ck_memoir_narrative_decisions_followup_source_shape",
    }
    if (
        "'include'" in table_sql
        and "'unable_to_answer'" in table_sql
        and "'stop_asking'" in table_sql
        and required_columns <= columns
        and all(name in table_sql for name in required_constraints)
    ):
        return False

    for trigger_name in MEMOIR_NARRATIVE_DECISION_TRIGGERS:
        await conn.execute(text(f"DROP TRIGGER IF EXISTS {trigger_name}"))
    for index_name in await _index_names(conn, table_name):
        if not index_name.startswith("sqlite_autoindex"):
            await conn.execute(text(f'DROP INDEX IF EXISTS "{index_name}"'))

    legacy_table_name = f"{table_name}_before_schema_upgrade"
    await conn.execute(text(f"ALTER TABLE {table_name} RENAME TO {legacy_table_name}"))
    table = Base.metadata.tables[table_name]
    await conn.run_sync(
        lambda sync_conn: table.create(sync_conn, checkfirst=False)
    )
    legacy_columns = {
        str(column["name"])
        for column in await _table_column_schema(conn, legacy_table_name)
    }
    legacy_answer_source = next(
        (
            column_name
            for column_name in (
                "source_followup_task_answer_id",
                "source_followup_task_utterance_id",
            )
            if column_name in legacy_columns
        ),
        None,
    )
    copied_columns: list[str] = []
    source_expressions: list[str] = []
    for column in table.columns:
        if column.name == "source_followup_task_answer_id":
            copied_columns.append(column.name)
            source_expressions.append(
                f'"{legacy_answer_source}"' if legacy_answer_source else "NULL"
            )
        elif column.name == "source_transcript_version_id":
            copied_columns.append(column.name)
            source_expressions.append(
                '"source_transcript_version_id"'
                if legacy_answer_source
                and "source_transcript_version_id" in legacy_columns
                else "NULL"
            )
        elif column.name in legacy_columns:
            copied_columns.append(column.name)
            source_expressions.append(f'"{column.name}"')
    column_names = ", ".join(f'"{name}"' for name in copied_columns)
    await conn.execute(
        text(
            f"INSERT INTO {table_name} ({column_names}) "
            f"SELECT {', '.join(source_expressions)} FROM {legacy_table_name}"
        )
    )
    await conn.execute(text(f"DROP TABLE {legacy_table_name}"))
    return True


async def _index_names(conn, table_name: str) -> set[str]:
    rows = (await conn.execute(text(f"PRAGMA index_list({table_name})"))).fetchall()
    return {str(row[1]) for row in rows}


async def _trigger_exists(conn, trigger_name: str) -> bool:
    result = await conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type = 'trigger' AND name = :trigger_name"),
        {"trigger_name": trigger_name},
    )
    return result.scalar_one_or_none() is not None


async def _trigger_sql(conn, trigger_name: str) -> str:
    result = await conn.execute(
        text(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'trigger' AND name = :trigger_name"
        ),
        {"trigger_name": trigger_name},
    )
    return str(result.scalar_one_or_none() or "")


async def _replace_unguarded_delete_trigger(conn, trigger_name: str) -> None:
    current_sql = await _trigger_sql(conn, trigger_name)
    if not current_sql or "__delete_in_progress" in current_sql:
        return
    await conn.execute(text(f"DROP TRIGGER {trigger_name}"))


async def _ensure_decision_triggers(conn) -> list[str]:
    changed: list[str] = []
    statements = {
        "memoir_narrative_decisions_no_update": """
            CREATE TRIGGER memoir_narrative_decisions_no_update
            BEFORE UPDATE ON memoir_narrative_decisions
            BEGIN
                SELECT RAISE(ABORT, 'memoir narrative decisions are append-only');
            END
        """,
        "memoir_narrative_decisions_no_delete": """
            CREATE TRIGGER memoir_narrative_decisions_no_delete
            BEFORE DELETE ON memoir_narrative_decisions
            WHEN EXISTS (
                SELECT 1
                FROM projects
                WHERE id = OLD.project_id
                  AND COALESCE(
                    json_extract(metadata_json, '$.__delete_in_progress'),
                    0
                  ) != 1
            )
            BEGIN
                SELECT RAISE(ABORT, 'memoir narrative decisions are append-only');
            END
        """,
    }
    for trigger_name, statement in statements.items():
        if trigger_name == "memoir_narrative_decisions_no_delete":
            await _replace_unguarded_delete_trigger(conn, trigger_name)
        if await _trigger_exists(conn, trigger_name):
            continue
        await conn.execute(text(statement))
        changed.append(trigger_name)
    return changed


async def _ensure_interview_policy_triggers(conn) -> list[str]:
    changed: list[str] = []
    failure_code_values = ", ".join(
        f"'{value}'" for value in INTERVIEW_POLICY_FAILURE_CODES
    )
    statements = {
        "memoir_utterance_interview_assignments_immutable_update": """
            CREATE TRIGGER memoir_utterance_interview_assignments_immutable_update
            BEFORE UPDATE ON memoir_utterance_interview_assignments
            WHEN NEW.utterance_id IS NOT OLD.utterance_id
              OR NEW.followup_task_id IS NOT OLD.followup_task_id
              OR NEW.policy_decision_id IS NOT OLD.policy_decision_id
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'memoir utterance interview assignment targets are immutable'
                );
            END
        """,
        "memoir_interview_policy_decisions_immutable_selection": """
            CREATE TRIGGER memoir_interview_policy_decisions_immutable_selection
            BEFORE UPDATE ON memoir_interview_policy_decisions
            WHEN NEW.session_id IS NOT OLD.session_id
              OR (
                NEW.trigger_message_id IS NOT OLD.trigger_message_id
                AND NEW.trigger_message_id IS NOT NULL
              )
              OR NEW.brief_fingerprint IS NOT OLD.brief_fingerprint
              OR NEW.state_snapshot_json IS NOT OLD.state_snapshot_json
              OR NEW.candidate_actions_json IS NOT OLD.candidate_actions_json
              OR NEW.selected_action IS NOT OLD.selected_action
              OR NEW.reason_code IS NOT OLD.reason_code
              OR NEW.reason_summary IS NOT OLD.reason_summary
              OR NEW.policy_version IS NOT OLD.policy_version
              OR NEW.model_provider IS NOT OLD.model_provider
              OR NEW.model_name IS NOT OLD.model_name
              OR NEW.execution_mode IS NOT OLD.execution_mode
              OR (
                OLD.generated_message_id IS NOT NULL
                AND NEW.generated_message_id IS NOT OLD.generated_message_id
                AND NEW.generated_message_id IS NOT NULL
              )
              OR NEW.created_at IS NOT OLD.created_at
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'memoir interview policy selections are immutable'
                );
            END
        """,
        "memoir_interview_policy_decisions_candidate_membership": """
            CREATE TRIGGER memoir_interview_policy_decisions_candidate_membership
            BEFORE INSERT ON memoir_interview_policy_decisions
            WHEN NOT EXISTS (
                SELECT 1
                FROM json_each(NEW.candidate_actions_json)
                WHERE json_extract(value, '$.action') = NEW.selected_action
            )
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'selected interview action must belong to candidate actions'
                );
            END
        """,
        "memoir_interview_policy_decisions_append_only_evaluation": """
            CREATE TRIGGER memoir_interview_policy_decisions_append_only_evaluation
            BEFORE UPDATE ON memoir_interview_policy_decisions
            WHEN (
                OLD.user_response_outcome IS NOT NULL
                AND NEW.user_response_outcome IS NOT OLD.user_response_outcome
              )
              OR (
                OLD.human_rating IS NOT NULL
                AND NEW.human_rating IS NOT OLD.human_rating
              )
              OR (
                OLD.evaluated_at IS NOT NULL
                AND NEW.evaluated_at IS NOT OLD.evaluated_at
              )
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'memoir interview policy evaluations are append-only'
                );
            END
        """,
        "memoir_interview_policy_evaluations_failure_codes": f"""
            CREATE TRIGGER memoir_interview_policy_evaluations_failure_codes
            BEFORE INSERT ON memoir_interview_policy_evaluations
            WHEN json_type(NEW.failure_codes_json) IS NOT 'array'
              OR EXISTS (
                SELECT 1
                FROM json_each(NEW.failure_codes_json)
                WHERE value NOT IN ({failure_code_values})
              )
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'unknown memoir interview policy failure code'
                );
            END
        """,
        "memoir_interview_policy_evaluations_no_update": """
            CREATE TRIGGER memoir_interview_policy_evaluations_no_update
            BEFORE UPDATE ON memoir_interview_policy_evaluations
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'memoir interview policy evaluations are append-only'
                );
            END
        """,
        "memoir_interview_policy_evaluations_no_delete": """
            CREATE TRIGGER memoir_interview_policy_evaluations_no_delete
            BEFORE DELETE ON memoir_interview_policy_evaluations
            WHEN EXISTS (
                SELECT 1
                FROM memoir_interview_policy_decisions AS decision
                JOIN memoir_voice_sessions AS session
                  ON session.id = decision.session_id
                JOIN projects AS project
                  ON project.id = session.project_id
                WHERE decision.id = OLD.decision_id
                  AND COALESCE(
                    json_extract(project.metadata_json, '$.__delete_in_progress'),
                    0
                  ) != 1
            )
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'memoir interview policy evaluations are append-only'
                );
            END
        """,
    }
    for trigger_name, statement in statements.items():
        if await _trigger_exists(conn, trigger_name):
            continue
        await conn.execute(text(statement))
        changed.append(trigger_name)
    return changed


async def _ensure_chapter_structure_triggers(conn) -> list[str]:
    changed: list[str] = []
    statements = {
        "memoir_chapter_contracts_immutable_update": """
            CREATE TRIGGER memoir_chapter_contracts_immutable_update
            BEFORE UPDATE ON memoir_chapter_contracts
            WHEN OLD.confirmed_at IS NOT NULL
              AND NOT (
                OLD.status = 'confirmed'
                AND NEW.status = 'superseded'
                AND NEW.superseded_at IS NOT NULL
                AND NEW.id IS OLD.id
                AND NEW.project_id IS OLD.project_id
                AND NEW.chapter_id IS OLD.chapter_id
                AND NEW.version IS OLD.version
                AND NEW.objective IS OLD.objective
                AND NEW.reader_takeaway IS OLD.reader_takeaway
                AND NEW.time_scope_text IS OLD.time_scope_text
                AND NEW.time_start IS OLD.time_start
                AND NEW.time_end IS OLD.time_end
                AND NEW.core_questions_json IS OLD.core_questions_json
                AND NEW.constraints_json IS OLD.constraints_json
                AND NEW.created_by IS OLD.created_by
                AND NEW.created_by_user_id IS OLD.created_by_user_id
                AND NEW.confirmed_by_user_id IS OLD.confirmed_by_user_id
                AND NEW.confirmed_at IS OLD.confirmed_at
                AND NEW.created_at IS OLD.created_at
              )
            BEGIN
                SELECT RAISE(ABORT, 'confirmed chapter contracts are immutable');
            END
        """,
        "memoir_chapter_contracts_immutable_delete": """
            CREATE TRIGGER memoir_chapter_contracts_immutable_delete
            BEFORE DELETE ON memoir_chapter_contracts
            WHEN OLD.confirmed_at IS NOT NULL
              AND EXISTS (
                SELECT 1
                FROM projects
                WHERE id = OLD.project_id
                  AND COALESCE(
                    json_extract(metadata_json, '$.__delete_in_progress'),
                    0
                  ) != 1
              )
            BEGIN
                SELECT RAISE(ABORT, 'confirmed chapter contracts are immutable');
            END
        """,
        "memoir_chapter_nodes_immutable_insert": """
            CREATE TRIGGER memoir_chapter_nodes_immutable_insert
            BEFORE INSERT ON memoir_chapter_nodes
            WHEN EXISTS (
                SELECT 1 FROM memoir_chapter_contracts
                WHERE id = NEW.contract_id
                  AND project_id = NEW.project_id
                  AND confirmed_at IS NOT NULL
            )
            BEGIN
                SELECT RAISE(ABORT, 'confirmed chapter nodes are immutable');
            END
        """,
        "memoir_chapter_nodes_immutable_update": """
            CREATE TRIGGER memoir_chapter_nodes_immutable_update
            BEFORE UPDATE ON memoir_chapter_nodes
            WHEN EXISTS (
                SELECT 1 FROM memoir_chapter_contracts
                WHERE confirmed_at IS NOT NULL
                  AND (
                    (id = OLD.contract_id AND project_id = OLD.project_id)
                    OR (id = NEW.contract_id AND project_id = NEW.project_id)
                  )
            )
            BEGIN
                SELECT RAISE(ABORT, 'confirmed chapter nodes are immutable');
            END
        """,
        "memoir_chapter_nodes_immutable_delete": """
            CREATE TRIGGER memoir_chapter_nodes_immutable_delete
            BEFORE DELETE ON memoir_chapter_nodes
            WHEN EXISTS (
                SELECT 1 FROM memoir_chapter_contracts
                WHERE id = OLD.contract_id
                  AND project_id = OLD.project_id
                  AND confirmed_at IS NOT NULL
            )
              AND EXISTS (
                SELECT 1
                FROM projects
                WHERE id = OLD.project_id
                  AND COALESCE(
                    json_extract(metadata_json, '$.__delete_in_progress'),
                    0
                  ) != 1
              )
            BEGIN
                SELECT RAISE(ABORT, 'confirmed chapter nodes are immutable');
            END
        """,
        "memoir_chapter_node_stories_immutable_insert": """
            CREATE TRIGGER memoir_chapter_node_stories_immutable_insert
            BEFORE INSERT ON memoir_chapter_node_stories
            WHEN EXISTS (
                SELECT 1
                FROM memoir_chapter_nodes AS node
                JOIN memoir_chapter_contracts AS contract
                  ON contract.id = node.contract_id
                 AND contract.project_id = node.project_id
                WHERE node.id = NEW.node_id
                  AND node.project_id = NEW.project_id
                  AND contract.confirmed_at IS NOT NULL
            )
            BEGIN
                SELECT RAISE(ABORT, 'confirmed chapter node stories are immutable');
            END
        """,
        "memoir_chapter_node_stories_immutable_update": """
            CREATE TRIGGER memoir_chapter_node_stories_immutable_update
            BEFORE UPDATE ON memoir_chapter_node_stories
            WHEN EXISTS (
                SELECT 1
                FROM memoir_chapter_nodes AS node
                JOIN memoir_chapter_contracts AS contract
                  ON contract.id = node.contract_id
                 AND contract.project_id = node.project_id
                WHERE contract.confirmed_at IS NOT NULL
                  AND (
                    (node.id = OLD.node_id AND node.project_id = OLD.project_id)
                    OR (node.id = NEW.node_id AND node.project_id = NEW.project_id)
                  )
            )
            BEGIN
                SELECT RAISE(ABORT, 'confirmed chapter node stories are immutable');
            END
        """,
        "memoir_chapter_node_stories_immutable_delete": """
            CREATE TRIGGER memoir_chapter_node_stories_immutable_delete
            BEFORE DELETE ON memoir_chapter_node_stories
            WHEN EXISTS (
                SELECT 1
                FROM memoir_chapter_nodes AS node
                JOIN memoir_chapter_contracts AS contract
                  ON contract.id = node.contract_id
                 AND contract.project_id = node.project_id
                WHERE node.id = OLD.node_id
                  AND node.project_id = OLD.project_id
                  AND contract.confirmed_at IS NOT NULL
            )
              AND EXISTS (
                SELECT 1
                FROM projects
                WHERE id = OLD.project_id
                  AND COALESCE(
                    json_extract(metadata_json, '$.__delete_in_progress'),
                    0
                  ) != 1
              )
            BEGIN
                SELECT RAISE(ABORT, 'confirmed chapter node stories are immutable');
            END
        """,
    }
    guarded_delete_triggers = {
        "memoir_chapter_contracts_immutable_delete",
        "memoir_chapter_nodes_immutable_delete",
        "memoir_chapter_node_stories_immutable_delete",
    }
    for trigger_name, statement in statements.items():
        if trigger_name in guarded_delete_triggers:
            await _replace_unguarded_delete_trigger(conn, trigger_name)
        if await _trigger_exists(conn, trigger_name):
            continue
        await conn.execute(text(statement))
        changed.append(trigger_name)
    return changed


async def migrate_memoir_narrative_schema(engine: AsyncEngine) -> dict[str, object]:
    """Create and advance the narrative fact schema."""
    changed_tables: list[str] = []
    changed_columns: list[str] = []
    changed_indexes: list[str] = []
    changed_triggers: list[str] = []
    if await _ensure_editing_job_type_constraint(engine):
        changed_tables.append("memoir_editing_jobs")
    if await _ensure_followup_task_outcome_schema(engine):
        changed_tables.append("memoir_followup_tasks")
    changed_tables.extend(
        await _migrate_followup_answer_assignment_schema(engine)
    )
    async with engine.begin() as conn:
        await _retire_legacy_completion_rows(conn)
        for table in _narrative_tables_from_metadata():
            if not await _table_exists(conn, table.name):
                await conn.run_sync(lambda sync_conn, table=table: table.create(sync_conn, checkfirst=True))
                changed_tables.append(table.name)
        if await _ensure_decision_schema(conn):
            changed_tables.append("memoir_narrative_decisions")
        changed_columns.extend(await _ensure_narrative_gap_action_columns(conn))
        for table in _narrative_tables_from_metadata():
            existing_indexes = await _index_names(conn, table.name)
            for index in table.indexes:
                if not index.name or index.name in existing_indexes:
                    continue
                await conn.run_sync(lambda sync_conn, index=index: index.create(sync_conn, checkfirst=True))
                changed_indexes.append(index.name)
        changed_columns.extend(await _ensure_structured_draft_snapshot_column(conn))
        changed_columns.extend(await _ensure_book_output_formal_columns(conn))
        changed_columns.extend(await _ensure_book_plan_chapter_include_column(conn))
        changed_columns.extend(await _ensure_followup_task_answer_use_column(conn))
        await _backfill_semantic_followup_task_target_slots(conn)
        await _remove_derived_empty_story_slots(conn)
        changed_triggers.extend(await _ensure_decision_triggers(conn))
        changed_triggers.extend(await _ensure_interview_policy_triggers(conn))
        changed_triggers.extend(await _ensure_chapter_structure_triggers(conn))
        changed_triggers.extend(await ensure_draft_block_triggers(conn))
    if await _ensure_narrative_gap_action_constraints(engine):
        changed_tables.append("memoir_narrative_gaps")
    return {
        "changed_tables": changed_tables,
        "changed_columns": changed_columns,
        "changed_indexes": changed_indexes,
        "changed_triggers": changed_triggers,
    }


async def assert_memoir_narrative_schema(engine: AsyncEngine) -> None:
    expected_columns = {
        table_name: {str(column["name"]): column for column in columns}
        for table_name, columns in expected_memoir_narrative_schema().items()
    }
    tables = {table.name: table for table in _narrative_tables_from_metadata()}
    missing_tables: list[str] = []
    missing_columns: list[str] = []
    column_mismatches: list[str] = []
    missing_indexes: list[str] = []
    missing_constraints: list[str] = []
    missing_triggers: list[str] = []

    async with engine.begin() as conn:
        if await _table_exists(conn, "memoir_book_outputs"):
            output_columns = {
                str(column["name"])
                for column in await _table_column_schema(conn, "memoir_book_outputs")
            }
            missing_columns.extend(
                f"memoir_book_outputs.{column_name}"
                for column_name in sorted(
                    set(MEMOIR_BOOK_OUTPUT_FORMAL_COLUMNS) - output_columns
                )
            )
        if await _table_exists(conn, "memoir_book_plan_chapters"):
            chapter_columns = {
                str(column["name"])
                for column in await _table_column_schema(conn, "memoir_book_plan_chapters")
            }
            if MEMOIR_BOOK_PLAN_CHAPTER_INCLUDE_COLUMN not in chapter_columns:
                missing_columns.append(
                    "memoir_book_plan_chapters."
                    f"{MEMOIR_BOOK_PLAN_CHAPTER_INCLUDE_COLUMN}"
                )
        for table_name, expected in expected_columns.items():
            if not await _table_exists(conn, table_name):
                missing_tables.append(table_name)
                continue

            actual = {
                str(column["name"]): column
                for column in await _table_column_schema(conn, table_name)
            }
            missing_columns.extend(
                f"{table_name}.{column_name}"
                for column_name in sorted(set(expected) - set(actual))
            )
            for column_name in sorted(set(expected) & set(actual)):
                for attribute in ("nullable", "primary_key"):
                    if expected[column_name][attribute] != actual[column_name][attribute]:
                        column_mismatches.append(
                            f"{table_name}.{column_name}.{attribute} "
                            f"expected={expected[column_name][attribute]} "
                            f"actual={actual[column_name][attribute]}"
                        )

            table = tables[table_name]
            actual_indexes = await _index_names(conn, table_name)
            missing_indexes.extend(
                index.name
                for index in table.indexes
                if index.name and index.name not in actual_indexes
            )
            actual_sql = await _table_sql(conn, table_name)
            missing_constraints.extend(
                constraint.name
                for constraint in table.constraints
                if constraint.name and constraint.name not in actual_sql
            )
        for trigger_name in MEMOIR_NARRATIVE_TRIGGERS:
            if not await _trigger_exists(conn, trigger_name):
                missing_triggers.append(trigger_name)

    if (
        missing_tables
        or missing_columns
        or column_mismatches
        or missing_indexes
        or missing_constraints
        or missing_triggers
    ):
        details: list[str] = []
        if missing_tables:
            details.append(f"missing tables: {', '.join(sorted(missing_tables))}")
        if missing_columns:
            details.append(f"missing columns: {', '.join(sorted(missing_columns))}")
        if column_mismatches:
            details.append(f"column mismatches: {', '.join(sorted(column_mismatches))}")
        if missing_indexes:
            details.append(f"missing indexes: {', '.join(sorted(missing_indexes))}")
        if missing_constraints:
            details.append(f"missing constraints: {', '.join(sorted(missing_constraints))}")
        if missing_triggers:
            details.append(f"missing triggers: {', '.join(sorted(missing_triggers))}")
        raise RuntimeError(
            "Memoir narrative schema mismatch; "
            + "; ".join(details)
            + "; run `PYTHONPATH=backend python backend/scripts/migrate_runtime_schema.py` before starting the API"
        )
