from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.database import Base, register_models
from app.migrations.schema_inspection import table_columns, table_exists, trigger_exists


MEMOIR_NARRATIVE_TABLES = (
    "memoir_project_frameworks",
    "memoir_framework_items",
    "memoir_framework_item_materials",
    "memoir_interview_policy_decisions",
    "memoir_interview_policy_evaluations",
)
MEMOIR_NARRATIVE_TRIGGERS = (
    "memoir_interview_policy_decisions_immutable_selection",
    "memoir_interview_policy_decisions_candidate_membership",
    "memoir_interview_policy_decisions_append_only_evaluation",
    "memoir_interview_policy_evaluations_failure_codes",
    "memoir_interview_policy_evaluations_no_update",
    "memoir_interview_policy_evaluations_no_delete",
)
INTERVIEW_POLICY_FAILURE_CODES = (
    "action_outside_candidates",
    "interrupted_user",
    "repeated_question",
    "wrong_topic_switch",
    "privacy_violation",
)


async def _ensure_narrative_triggers(conn) -> list[str]:
    failure_codes = ", ".join(
        f"'{value}'" for value in INTERVIEW_POLICY_FAILURE_CODES
    )
    statements = {
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
                SELECT 1 FROM json_each(NEW.candidate_actions_json)
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
                SELECT 1 FROM json_each(NEW.failure_codes_json)
                WHERE value NOT IN ({failure_codes})
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
                JOIN projects AS project ON project.id = session.project_id
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
    changed: list[str] = []
    for name, statement in statements.items():
        if await trigger_exists(conn, name):
            continue
        await conn.execute(text(statement))
        changed.append(name)
    return changed


async def migrate_memoir_narrative_schema(
    engine: AsyncEngine,
) -> dict[str, object]:
    """Create the current narrative schema; legacy upgrades are intentionally removed."""
    register_models()
    created: list[str] = []
    async with engine.begin() as conn:
        for table_name in MEMOIR_NARRATIVE_TABLES:
            if not await table_exists(conn, table_name):
                created.append(table_name)
        await conn.run_sync(Base.metadata.create_all)
        changed_triggers = await _ensure_narrative_triggers(conn)
    return {
        "changed_tables": created,
        "changed_columns": [],
        "changed_indexes": [],
        "changed_triggers": changed_triggers,
    }


async def assert_memoir_narrative_schema(engine: AsyncEngine) -> None:
    register_models()
    missing_tables: list[str] = []
    missing_columns: list[str] = []
    async with engine.begin() as conn:
        for table_name in MEMOIR_NARRATIVE_TABLES:
            if not await table_exists(conn, table_name):
                missing_tables.append(table_name)
                continue
            actual = await table_columns(conn, table_name)
            expected = {
                column.name
                for column in Base.metadata.tables[table_name].columns
            }
            missing_columns.extend(
                f"{table_name}.{name}" for name in sorted(expected - actual)
            )
        missing_triggers = [
            name
            for name in MEMOIR_NARRATIVE_TRIGGERS
            if not await trigger_exists(conn, name)
        ]
    if missing_tables or missing_columns or missing_triggers:
        raise RuntimeError(
            "Memoir narrative schema mismatch: "
            f"missing_tables={missing_tables}, missing_columns={missing_columns}, "
            f"missing_triggers={missing_triggers}"
        )
