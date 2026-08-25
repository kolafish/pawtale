"""add PostgreSQL runtime parity

Revision ID: 3b5e639b4b76
Revises: 063b98576f7b
Create Date: 2026-08-25 11:48:44.820506
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '3b5e639b4b76'
down_revision: Union[str, Sequence[str], None] = '063b98576f7b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


DIRECT_REVISION_TABLES = (
    "memoir_automation_events",
    "memoir_book_chapter_draft_versions",
    "memoir_book_chapter_drafts",
    "memoir_book_image_placements",
    "memoir_book_output_versions",
    "memoir_book_outputs",
    "memoir_book_plan_chapters",
    "memoir_book_plans",
    "memoir_book_reviews",
    "memoir_book_revision_proposals",
    "memoir_chapter_digests",
    "memoir_chat_messages",
    "memoir_completion_issues",
    "memoir_editing_jobs",
    "memoir_image_assets",
    "memoir_material_share_videos",
    "memoir_project_frameworks",
    "memoir_section_digests",
    "memoir_section_materials",
    "memoir_sections",
    "memoir_share_video_outputs",
    "memoir_voice_sessions",
)

INDIRECT_REVISION_TABLES = {
    "memoir_framework_item_materials": "framework_item_material",
    "memoir_framework_items": "framework_item",
    "memoir_interview_policy_decisions": "policy_decision",
    "memoir_interview_policy_evaluations": "policy_evaluation",
    "memoir_transcript_alignments": "transcript_alignment",
    "memoir_transcript_versions": "transcript_version",
    "memoir_voice_authorization_consents": "voice_consent",
    "memoir_voice_utterances": "voice_utterance",
}

VOICE_TRIGGERS = (
    "memoir_voice_sessions_targets_insert",
    "memoir_voice_sessions_targets_update",
    "memoir_voice_utterances_targets_insert",
    "memoir_voice_utterances_targets_update",
    "memoir_automation_events_job_insert",
    "memoir_automation_events_job_update",
)

NARRATIVE_TRIGGERS = (
    "memoir_interview_policy_decisions_immutable_selection",
    "memoir_interview_policy_decisions_candidate_membership",
    "memoir_interview_policy_decisions_append_only_evaluation",
    "memoir_interview_policy_evaluations_failure_codes",
    "memoir_interview_policy_evaluations_no_update",
    "memoir_interview_policy_evaluations_no_delete",
)


def _execute(statement: str) -> None:
    op.execute(sa.text(statement))


def _create_voice_integrity() -> None:
    _execute(
        """
        CREATE FUNCTION pawtale_validate_voice_session_target()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF (
                NEW.current_completion_issue_id IS NOT NULL
                AND (
                    NEW.session_goal != 'book_completion'
                    OR NOT EXISTS (
                        SELECT 1 FROM memoir_completion_issues AS issue
                        WHERE issue.id = NEW.current_completion_issue_id
                          AND issue.project_id = NEW.project_id
                    )
                )
            ) OR (
                NEW.current_completion_issue_id IS NOT NULL
                AND NEW.anchor_framework_item_id IS NOT NULL
            ) OR (
                NEW.anchor_framework_item_id IS NOT NULL
                AND NOT EXISTS (
                    SELECT 1
                    FROM memoir_framework_items AS item
                    JOIN memoir_project_frameworks AS framework
                      ON framework.id = item.project_framework_id
                    WHERE item.id = NEW.anchor_framework_item_id
                      AND framework.project_id = NEW.project_id
                )
            ) THEN
                RAISE EXCEPTION 'invalid memoir voice session target'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    _execute(
        """
        CREATE FUNCTION pawtale_validate_voice_utterance_target()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF (
                NEW.completion_issue_id IS NOT NULL
                AND NOT EXISTS (
                    SELECT 1
                    FROM memoir_completion_issues AS issue
                    JOIN memoir_voice_sessions AS session
                      ON session.id = NEW.voice_session_id
                    WHERE issue.id = NEW.completion_issue_id
                      AND issue.project_id = session.project_id
                )
            ) OR (
                NEW.completion_issue_id IS NOT NULL
                AND NEW.framework_item_id IS NOT NULL
            ) OR (
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
            ) OR (
                NEW.policy_decision_id IS NOT NULL
                AND NOT EXISTS (
                    SELECT 1 FROM memoir_interview_policy_decisions AS decision
                    WHERE decision.id = NEW.policy_decision_id
                      AND decision.session_id = NEW.voice_session_id
                )
            ) THEN
                RAISE EXCEPTION 'invalid memoir voice utterance target'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    _execute(
        """
        CREATE FUNCTION pawtale_validate_automation_event_job()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.editing_job_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM memoir_editing_jobs AS job
                WHERE job.id = NEW.editing_job_id
                  AND job.project_id = NEW.project_id
            ) THEN
                RAISE EXCEPTION 'invalid memoir automation event job'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    trigger_specs = (
        (
            "memoir_voice_sessions_targets_insert",
            "BEFORE INSERT",
            "memoir_voice_sessions",
            "pawtale_validate_voice_session_target",
        ),
        (
            "memoir_voice_sessions_targets_update",
            "BEFORE UPDATE OF project_id, session_goal, current_completion_issue_id, anchor_framework_item_id",
            "memoir_voice_sessions",
            "pawtale_validate_voice_session_target",
        ),
        (
            "memoir_voice_utterances_targets_insert",
            "BEFORE INSERT",
            "memoir_voice_utterances",
            "pawtale_validate_voice_utterance_target",
        ),
        (
            "memoir_voice_utterances_targets_update",
            "BEFORE UPDATE OF voice_session_id, completion_issue_id, framework_item_id, policy_decision_id",
            "memoir_voice_utterances",
            "pawtale_validate_voice_utterance_target",
        ),
        (
            "memoir_automation_events_job_insert",
            "BEFORE INSERT",
            "memoir_automation_events",
            "pawtale_validate_automation_event_job",
        ),
        (
            "memoir_automation_events_job_update",
            "BEFORE UPDATE OF project_id, editing_job_id",
            "memoir_automation_events",
            "pawtale_validate_automation_event_job",
        ),
    )
    for name, event, table, function in trigger_specs:
        _execute(
            f"CREATE TRIGGER {name} {event} ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION {function}()"
        )


def _create_narrative_integrity() -> None:
    _execute(
        """
        CREATE FUNCTION pawtale_validate_policy_decision_update()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.session_id IS DISTINCT FROM OLD.session_id
              OR (
                NEW.trigger_message_id IS DISTINCT FROM OLD.trigger_message_id
                AND NEW.trigger_message_id IS NOT NULL
              )
              OR NEW.brief_fingerprint IS DISTINCT FROM OLD.brief_fingerprint
              OR NEW.state_snapshot_json::jsonb IS DISTINCT FROM OLD.state_snapshot_json::jsonb
              OR NEW.candidate_actions_json::jsonb IS DISTINCT FROM OLD.candidate_actions_json::jsonb
              OR NEW.selected_action IS DISTINCT FROM OLD.selected_action
              OR NEW.reason_code IS DISTINCT FROM OLD.reason_code
              OR NEW.reason_summary IS DISTINCT FROM OLD.reason_summary
              OR NEW.policy_version IS DISTINCT FROM OLD.policy_version
              OR NEW.model_provider IS DISTINCT FROM OLD.model_provider
              OR NEW.model_name IS DISTINCT FROM OLD.model_name
              OR NEW.execution_mode IS DISTINCT FROM OLD.execution_mode
              OR (
                OLD.generated_message_id IS NOT NULL
                AND NEW.generated_message_id IS DISTINCT FROM OLD.generated_message_id
                AND NEW.generated_message_id IS NOT NULL
              )
              OR NEW.created_at IS DISTINCT FROM OLD.created_at
            THEN
                RAISE EXCEPTION 'memoir interview policy selections are immutable'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    _execute(
        """
        CREATE FUNCTION pawtale_validate_policy_candidate_membership()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM json_array_elements(NEW.candidate_actions_json) AS candidate
                WHERE candidate ->> 'action' = NEW.selected_action
            ) THEN
                RAISE EXCEPTION 'selected interview action must belong to candidate actions'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    _execute(
        """
        CREATE FUNCTION pawtale_validate_policy_evaluation_fields()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF (
                OLD.user_response_outcome IS NOT NULL
                AND NEW.user_response_outcome IS DISTINCT FROM OLD.user_response_outcome
            ) OR (
                OLD.human_rating IS NOT NULL
                AND NEW.human_rating IS DISTINCT FROM OLD.human_rating
            ) OR (
                OLD.evaluated_at IS NOT NULL
                AND NEW.evaluated_at IS DISTINCT FROM OLD.evaluated_at
            ) THEN
                RAISE EXCEPTION 'memoir interview policy evaluations are append-only'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    _execute(
        """
        CREATE FUNCTION pawtale_validate_policy_failure_codes()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF json_typeof(NEW.failure_codes_json) IS DISTINCT FROM 'array'
              OR EXISTS (
                SELECT 1 FROM json_array_elements_text(NEW.failure_codes_json) AS code
                WHERE code NOT IN (
                    'action_outside_candidates',
                    'interrupted_user',
                    'repeated_question',
                    'wrong_topic_switch',
                    'privacy_violation'
                )
              )
            THEN
                RAISE EXCEPTION 'unknown memoir interview policy failure code'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    _execute(
        """
        CREATE FUNCTION pawtale_reject_policy_evaluation_update()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'memoir interview policy evaluations are append-only'
                USING ERRCODE = '23514';
        END;
        $$
        """
    )
    _execute(
        """
        CREATE FUNCTION pawtale_reject_policy_evaluation_delete()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM memoir_interview_policy_decisions AS decision
                JOIN memoir_voice_sessions AS session
                  ON session.id = decision.session_id
                JOIN projects AS project ON project.id = session.project_id
                WHERE decision.id = OLD.decision_id
                  AND COALESCE(
                    project.metadata_json ->> '__delete_in_progress',
                    '0'
                  ) NOT IN ('1', 'true')
            ) THEN
                RAISE EXCEPTION 'memoir interview policy evaluations are append-only'
                    USING ERRCODE = '23514';
            END IF;
            RETURN OLD;
        END;
        $$
        """
    )
    trigger_specs = (
        (
            "memoir_interview_policy_decisions_immutable_selection",
            "BEFORE UPDATE",
            "memoir_interview_policy_decisions",
            "pawtale_validate_policy_decision_update",
        ),
        (
            "memoir_interview_policy_decisions_candidate_membership",
            "BEFORE INSERT",
            "memoir_interview_policy_decisions",
            "pawtale_validate_policy_candidate_membership",
        ),
        (
            "memoir_interview_policy_decisions_append_only_evaluation",
            "BEFORE UPDATE",
            "memoir_interview_policy_decisions",
            "pawtale_validate_policy_evaluation_fields",
        ),
        (
            "memoir_interview_policy_evaluations_failure_codes",
            "BEFORE INSERT",
            "memoir_interview_policy_evaluations",
            "pawtale_validate_policy_failure_codes",
        ),
        (
            "memoir_interview_policy_evaluations_no_update",
            "BEFORE UPDATE",
            "memoir_interview_policy_evaluations",
            "pawtale_reject_policy_evaluation_update",
        ),
        (
            "memoir_interview_policy_evaluations_no_delete",
            "BEFORE DELETE",
            "memoir_interview_policy_evaluations",
            "pawtale_reject_policy_evaluation_delete",
        ),
    )
    for name, event, table, function in trigger_specs:
        _execute(
            f"CREATE TRIGGER {name} {event} ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION {function}()"
        )


def _create_revision_tracking() -> None:
    _execute(
        """
        CREATE FUNCTION pawtale_memoir_project_id(strategy text, row_data jsonb)
        RETURNS varchar LANGUAGE plpgsql STABLE AS $$
        DECLARE
            project_id varchar;
        BEGIN
            CASE strategy
                WHEN 'direct' THEN
                    project_id := row_data ->> 'project_id';
                WHEN 'framework_item_material' THEN
                    SELECT framework.project_id INTO project_id
                    FROM memoir_framework_items AS item
                    JOIN memoir_project_frameworks AS framework
                      ON framework.id = item.project_framework_id
                    WHERE item.id = row_data ->> 'framework_item_id';
                WHEN 'framework_item' THEN
                    SELECT framework.project_id INTO project_id
                    FROM memoir_project_frameworks AS framework
                    WHERE framework.id = row_data ->> 'project_framework_id';
                WHEN 'policy_decision' THEN
                    SELECT session.project_id INTO project_id
                    FROM memoir_voice_sessions AS session
                    WHERE session.id = row_data ->> 'session_id';
                WHEN 'policy_evaluation' THEN
                    SELECT session.project_id INTO project_id
                    FROM memoir_interview_policy_decisions AS decision
                    JOIN memoir_voice_sessions AS session
                      ON session.id = decision.session_id
                    WHERE decision.id = row_data ->> 'decision_id';
                WHEN 'transcript_alignment' THEN
                    SELECT session.project_id INTO project_id
                    FROM memoir_voice_utterances AS utterance
                    JOIN memoir_voice_sessions AS session
                      ON session.id = utterance.voice_session_id
                    WHERE utterance.id = row_data ->> 'utterance_id';
                WHEN 'transcript_version' THEN
                    SELECT session.project_id INTO project_id
                    FROM memoir_voice_sessions AS session
                    WHERE session.id = row_data ->> 'voice_session_id';
                WHEN 'voice_consent' THEN
                    SELECT session.project_id INTO project_id
                    FROM memoir_voice_sessions AS session
                    WHERE session.id = row_data ->> 'source_session_id';
                WHEN 'voice_utterance' THEN
                    SELECT session.project_id INTO project_id
                    FROM memoir_voice_sessions AS session
                    WHERE session.id = row_data ->> 'voice_session_id';
                ELSE
                    RAISE EXCEPTION 'unknown memoir revision strategy: %', strategy;
            END CASE;
            RETURN project_id;
        END;
        $$
        """
    )
    _execute(
        """
        CREATE FUNCTION pawtale_bump_memoir_revision()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            old_project_id varchar;
            new_project_id varchar;
        BEGIN
            IF TG_OP != 'INSERT' THEN
                old_project_id := pawtale_memoir_project_id(TG_ARGV[0], to_jsonb(OLD));
            END IF;
            IF TG_OP != 'DELETE' THEN
                new_project_id := pawtale_memoir_project_id(TG_ARGV[0], to_jsonb(NEW));
            END IF;
            UPDATE projects
            SET memoir_data_revision = memoir_data_revision + 1
            WHERE type = 'memoir'
              AND id IN (
                SELECT DISTINCT value
                FROM unnest(ARRAY[old_project_id, new_project_id]) AS value
                WHERE value IS NOT NULL
              );
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    for table in DIRECT_REVISION_TABLES:
        _execute(
            f"CREATE TRIGGER trg_memoir_revision_{table} "
            f"AFTER INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW "
            "EXECUTE FUNCTION pawtale_bump_memoir_revision('direct')"
        )
    for table, strategy in INDIRECT_REVISION_TABLES.items():
        _execute(
            f"CREATE TRIGGER trg_memoir_revision_{table} "
            f"AFTER INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW "
            f"EXECUTE FUNCTION pawtale_bump_memoir_revision('{strategy}')"
        )
    _execute(
        """
        CREATE FUNCTION pawtale_bump_memoir_project_content_revision()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.type = 'memoir' THEN
                UPDATE projects
                SET memoir_data_revision = memoir_data_revision + 1
                WHERE id = NEW.id;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    _execute(
        """
        CREATE TRIGGER trg_memoir_revision_projects_content_update
        AFTER UPDATE OF title, description, metadata_json ON projects
        FOR EACH ROW EXECUTE FUNCTION pawtale_bump_memoir_project_content_revision()
        """
    )


def upgrade() -> None:
    _execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    _execute(
        "CREATE INDEX ix_memoir_voice_utterances_transcript_raw_trgm "
        "ON memoir_voice_utterances USING gin (transcript_raw gin_trgm_ops)"
    )
    _execute(
        "CREATE INDEX ix_memoir_voice_utterances_transcript_edited_trgm "
        "ON memoir_voice_utterances USING gin (transcript_edited gin_trgm_ops)"
    )
    _create_voice_integrity()
    _create_narrative_integrity()
    _create_revision_tracking()


def downgrade() -> None:
    _execute(
        "DROP TRIGGER trg_memoir_revision_projects_content_update ON projects"
    )
    _execute("DROP FUNCTION pawtale_bump_memoir_project_content_revision()")
    for table in (*DIRECT_REVISION_TABLES, *INDIRECT_REVISION_TABLES):
        _execute(f"DROP TRIGGER trg_memoir_revision_{table} ON {table}")
    _execute("DROP FUNCTION pawtale_bump_memoir_revision()")
    _execute("DROP FUNCTION pawtale_memoir_project_id(text, jsonb)")

    trigger_tables = {
        "memoir_interview_policy_decisions_immutable_selection": "memoir_interview_policy_decisions",
        "memoir_interview_policy_decisions_candidate_membership": "memoir_interview_policy_decisions",
        "memoir_interview_policy_decisions_append_only_evaluation": "memoir_interview_policy_decisions",
        "memoir_interview_policy_evaluations_failure_codes": "memoir_interview_policy_evaluations",
        "memoir_interview_policy_evaluations_no_update": "memoir_interview_policy_evaluations",
        "memoir_interview_policy_evaluations_no_delete": "memoir_interview_policy_evaluations",
    }
    for name, table in trigger_tables.items():
        _execute(f"DROP TRIGGER {name} ON {table}")
    for function in (
        "pawtale_validate_policy_decision_update",
        "pawtale_validate_policy_candidate_membership",
        "pawtale_validate_policy_evaluation_fields",
        "pawtale_validate_policy_failure_codes",
        "pawtale_reject_policy_evaluation_update",
        "pawtale_reject_policy_evaluation_delete",
    ):
        _execute(f"DROP FUNCTION {function}()")

    trigger_tables = {
        "memoir_voice_sessions_targets_insert": "memoir_voice_sessions",
        "memoir_voice_sessions_targets_update": "memoir_voice_sessions",
        "memoir_voice_utterances_targets_insert": "memoir_voice_utterances",
        "memoir_voice_utterances_targets_update": "memoir_voice_utterances",
        "memoir_automation_events_job_insert": "memoir_automation_events",
        "memoir_automation_events_job_update": "memoir_automation_events",
    }
    for name, table in trigger_tables.items():
        _execute(f"DROP TRIGGER {name} ON {table}")
    for function in (
        "pawtale_validate_voice_session_target",
        "pawtale_validate_voice_utterance_target",
        "pawtale_validate_automation_event_job",
    ):
        _execute(f"DROP FUNCTION {function}()")

    _execute("DROP INDEX ix_memoir_voice_utterances_transcript_edited_trgm")
    _execute("DROP INDEX ix_memoir_voice_utterances_transcript_raw_trgm")
