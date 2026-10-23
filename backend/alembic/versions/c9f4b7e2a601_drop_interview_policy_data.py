"""Remove the retired interview policy schema and persisted data."""

from alembic import op
import sqlalchemy as sa


revision = "c9f4b7e2a601"
down_revision = "a7c2e9f4b601"
branch_labels = None
depends_on = None


EDITING_JOB_TYPES = (
    "'generate_book_audio_chunk', 'correct_transcript', 'evaluate_share_video_candidate', "
    "'index_retrieval_source', 'index_retrieval_semantics', 'reprocess_material_offline_asr', "
    "'generate_material_image', 'regenerate_chapter_sections', 'organize_material', "
    "'update_topic_map', 'generate_book_editorial_copy', 'generate_book_review', "
    "'apply_book_review_answers', 'generate_book_revision_proposal', "
    "'generate_chapter_section_revision', 'prepare_chapter', 'update_book_draft', "
    "'fill_narrator_profile'"
)


def _execute(sql: str) -> None:
    op.execute(sa.text(sql))


def upgrade() -> None:
    # Remove the trigger that still references policy_decision_id before dropping
    # the column. The replacement keeps the framework/completion target checks.
    _execute("DROP TRIGGER IF EXISTS memoir_voice_utterances_targets_insert ON memoir_voice_utterances")
    _execute("DROP TRIGGER IF EXISTS memoir_voice_utterances_targets_update ON memoir_voice_utterances")
    _execute("DROP FUNCTION IF EXISTS pawtale_validate_voice_utterance_target()")

    # Discard queued policy jobs before tightening the editing-job constraint.
    _execute(
        "ALTER TABLE memoir_editing_jobs DROP CONSTRAINT IF EXISTS ck_memoir_editing_jobs_type"
    )
    _execute(
        "DELETE FROM memoir_editing_jobs "
        "WHERE job_type IN ('evaluate_interview_policy_shadow', 'evaluate_interview_policy_outcome')"
    )
    _execute(
        f"ALTER TABLE memoir_editing_jobs ADD CONSTRAINT ck_memoir_editing_jobs_type "
        f"CHECK (job_type IN ({EDITING_JOB_TYPES}))"
    )

    _execute("ALTER TABLE memoir_voice_utterances DROP COLUMN IF EXISTS policy_decision_id")
    # Evaluation rows reference decisions, so drop them first. This permanently
    # removes the retired classifier's database history as requested.
    _execute("DROP TABLE IF EXISTS memoir_interview_policy_evaluations")
    _execute("DROP TABLE IF EXISTS memoir_interview_policy_decisions")

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
        "CREATE TRIGGER memoir_voice_utterances_targets_insert "
        "BEFORE INSERT ON memoir_voice_utterances FOR EACH ROW "
        "EXECUTE FUNCTION pawtale_validate_voice_utterance_target()"
    )
    _execute(
        "CREATE TRIGGER memoir_voice_utterances_targets_update "
        "BEFORE UPDATE OF voice_session_id, completion_issue_id, framework_item_id "
        "ON memoir_voice_utterances FOR EACH ROW "
        "EXECUTE FUNCTION pawtale_validate_voice_utterance_target()"
    )

    # Keep project revision tracking, but remove the dead policy strategies from
    # its dispatcher so the database no longer contains policy-specific logic.
    _execute(
        """
        CREATE OR REPLACE FUNCTION pawtale_memoir_project_id(strategy text, row_data jsonb)
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

    for function in (
        "pawtale_validate_policy_decision_update",
        "pawtale_validate_policy_candidate_membership",
        "pawtale_validate_policy_evaluation_fields",
        "pawtale_validate_policy_failure_codes",
        "pawtale_reject_policy_evaluation_update",
        "pawtale_reject_policy_evaluation_delete",
    ):
        _execute(f"DROP FUNCTION IF EXISTS {function}()")


def downgrade() -> None:
    raise RuntimeError("c9f4b7e2a601 is irreversible because it deletes interview policy data")
