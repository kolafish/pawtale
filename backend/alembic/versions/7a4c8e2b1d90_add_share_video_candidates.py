"""Evaluate one share-video recommendation per completed voice session."""

from alembic import op
import sqlalchemy as sa


revision = "7a4c8e2b1d90"
down_revision = "4d1e6f8a9b20"
branch_labels = None
depends_on = None

PREVIOUS_JOB_TYPES = (
    "'correct_transcript', 'index_retrieval_source', 'index_retrieval_semantics', "
    "'reprocess_material_offline_asr', 'generate_material_image', 'regenerate_chapter_sections', "
    "'evaluate_interview_policy_shadow', 'evaluate_interview_policy_outcome', 'organize_material', "
    "'update_topic_map', 'generate_book_editorial_copy', 'generate_book_review', "
    "'apply_book_review_answers', 'generate_book_revision_proposal', 'generate_chapter_section_revision', "
    "'prepare_chapter', 'update_book_draft'"
)


def _job_types(values: str) -> None:
    op.drop_constraint("ck_memoir_editing_jobs_type", "memoir_editing_jobs", type_="check")
    op.create_check_constraint("ck_memoir_editing_jobs_type", "memoir_editing_jobs", f"job_type IN ({values})")


def upgrade() -> None:
    _job_types(PREVIOUS_JOB_TYPES + ", 'evaluate_share_video_candidate'")
    op.create_table(
        "memoir_share_video_candidates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("voice_session_id", sa.String(36), sa.ForeignKey("memoir_voice_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("utterance_id", sa.String(36), sa.ForeignKey("memoir_voice_utterances.id", ondelete="SET NULL"), nullable=True),
        sa.Column("audio_file_id", sa.String(36), sa.ForeignKey("audio_files.id", ondelete="SET NULL"), nullable=True),
        sa.Column("transcript_version_id", sa.String(36), nullable=True),
        sa.Column("text_hash", sa.String(64), nullable=True),
        sa.Column("selected_text", sa.Text(), nullable=False),
        sa.Column("start_char", sa.Integer(), nullable=True),
        sa.Column("end_char", sa.Integer(), nullable=True),
        sa.Column("start_ms", sa.Integer(), nullable=True),
        sa.Column("end_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("video_job_id", sa.String(36), sa.ForeignKey("memoir_material_share_videos.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("voice_session_id", name="uq_memoir_share_video_candidates_session"),
        sa.CheckConstraint(
            "status IN ('none', 'ready', 'accepted', 'dismissed', 'stale')",
            name="ck_memoir_share_video_candidates_status",
        ),
    )
    op.create_index(
        "ix_memoir_share_video_candidates_project_latest",
        "memoir_share_video_candidates",
        ["project_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_memoir_share_video_candidates_project_latest", table_name="memoir_share_video_candidates")
    op.drop_table("memoir_share_video_candidates")
    op.execute("DELETE FROM memoir_editing_jobs WHERE job_type = 'evaluate_share_video_candidate'")
    _job_types(PREVIOUS_JOB_TYPES)
