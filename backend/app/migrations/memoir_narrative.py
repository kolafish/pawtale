from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.schema import Column, Table

from app.database import Base, register_models
from app.migrations.memoir_draft_blocks import (
    MEMOIR_DRAFT_BLOCK_TRIGGERS,
    backfill_legacy_chapter_drafts,
    ensure_draft_block_triggers,
)


MEMOIR_NARRATIVE_TABLES = (
    "memoir_narrative_entities",
    "memoir_claims",
    "memoir_claim_evidence",
    "memoir_narrative_decisions",
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
    "memoir_followup_task_utterances",
    "memoir_draft_blocks",
    "memoir_draft_block_versions",
    "memoir_draft_block_claims",
    "memoir_draft_block_stories",
)
MEMOIR_NARRATIVE_DECISION_TRIGGERS = (
    "memoir_narrative_decisions_no_update",
    "memoir_narrative_decisions_no_delete",
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
    *MEMOIR_CHAPTER_STRUCTURE_TRIGGERS,
    *MEMOIR_DRAFT_BLOCK_TRIGGERS,
)
MEMOIR_BOOK_OUTPUT_FORMAL_COLUMNS = {
    "formal_version_id": "VARCHAR(36)",
    "formal_audit_job_id": "VARCHAR(36)",
    "formalized_by_user_id": "VARCHAR(36)",
    "formalized_at": "DATETIME",
}
FOLLOWUP_TASK_UTTERANCE_ASSIGNMENT_COLUMN = "is_assignment_snapshot"
FOLLOWUP_TASK_ANSWER_USE_COLUMN = "answer_use"
FOLLOWUP_TASK_UTTERANCE_INTERPRETATION_COLUMNS = {
    "interpretation_status": "VARCHAR(24) NOT NULL DEFAULT 'pending'",
    "answer_kind": "VARCHAR(24)",
    "direct_narrative_text": "TEXT NOT NULL DEFAULT ''",
    "clarification_text": "TEXT NOT NULL DEFAULT ''",
    "fact_resolution_text": "TEXT NOT NULL DEFAULT ''",
    "confirmation_question": "TEXT NOT NULL DEFAULT ''",
    "interpretation_reason": "TEXT NOT NULL DEFAULT ''",
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


async def _ensure_followup_task_utterance_assignment_column(conn) -> list[str]:
    table_name = "memoir_followup_task_utterances"
    if not await _table_exists(conn, table_name):
        return []
    columns = {
        str(column["name"])
        for column in await _table_column_schema(conn, table_name)
    }
    if FOLLOWUP_TASK_UTTERANCE_ASSIGNMENT_COLUMN in columns:
        return []
    await conn.execute(
        text(
            f"ALTER TABLE {table_name} "
            f"ADD COLUMN {FOLLOWUP_TASK_UTTERANCE_ASSIGNMENT_COLUMN} "
            "BOOLEAN NOT NULL DEFAULT 0"
        )
    )
    return [f"{table_name}.{FOLLOWUP_TASK_UTTERANCE_ASSIGNMENT_COLUMN}"]


async def _ensure_followup_task_utterance_interpretation_columns(conn) -> list[str]:
    table_name = "memoir_followup_task_utterances"
    if not await _table_exists(conn, table_name):
        return []
    columns = {
        str(column["name"])
        for column in await _table_column_schema(conn, table_name)
    }
    changed: list[str] = []
    for column_name, column_type in FOLLOWUP_TASK_UTTERANCE_INTERPRETATION_COLUMNS.items():
        if column_name in columns:
            continue
        await conn.execute(
            text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
        )
        changed.append(f"{table_name}.{column_name}")
    return changed


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


async def _table_sql(conn, table_name: str) -> str:
    result = await conn.execute(
        text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = :table_name"),
        {"table_name": table_name},
    )
    return str(result.scalar_one_or_none() or "")


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
        "source_followup_task_utterance_id",
        "source_transcript_version_id",
    }
    required_constraints = {
        "fk_memoir_narrative_decisions_followup_source_project",
        "fk_memoir_narrative_decisions_transcript_source",
        "ck_memoir_narrative_decisions_followup_source_shape",
    }
    if (
        "'include'" in table_sql
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
            BEGIN
                SELECT RAISE(ABORT, 'memoir narrative decisions are append-only');
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
            BEGIN
                SELECT RAISE(ABORT, 'confirmed chapter node stories are immutable');
            END
        """,
    }
    for trigger_name, statement in statements.items():
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
    legacy_backfill: dict[str, int]
    async with engine.begin() as conn:
        for table in _narrative_tables_from_metadata():
            if not await _table_exists(conn, table.name):
                await conn.run_sync(lambda sync_conn, table=table: table.create(sync_conn, checkfirst=True))
                changed_tables.append(table.name)
        if await _ensure_decision_schema(conn):
            changed_tables.append("memoir_narrative_decisions")
        for table in _narrative_tables_from_metadata():
            existing_indexes = await _index_names(conn, table.name)
            for index in table.indexes:
                if not index.name or index.name in existing_indexes:
                    continue
                await conn.run_sync(lambda sync_conn, index=index: index.create(sync_conn, checkfirst=True))
                changed_indexes.append(index.name)
        changed_columns.extend(await _ensure_structured_draft_snapshot_column(conn))
        changed_columns.extend(await _ensure_book_output_formal_columns(conn))
        changed_columns.extend(
            await _ensure_followup_task_utterance_assignment_column(conn)
        )
        changed_columns.extend(
            await _ensure_followup_task_utterance_interpretation_columns(conn)
        )
        changed_columns.extend(await _ensure_followup_task_answer_use_column(conn))
        changed_triggers.extend(await _ensure_decision_triggers(conn))
        changed_triggers.extend(await _ensure_chapter_structure_triggers(conn))
        changed_triggers.extend(await ensure_draft_block_triggers(conn))
        legacy_backfill = await backfill_legacy_chapter_drafts(conn)
    return {
        "changed_tables": changed_tables,
        "changed_columns": changed_columns,
        "changed_indexes": changed_indexes,
        "changed_triggers": changed_triggers,
        "legacy_backfill": legacy_backfill,
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
