"""Reuse the durable editing queue for incremental book audio."""

from alembic import op

revision = "b6a8d3e1f902"
down_revision = "9f2c6b4d8a10"
branch_labels = None
depends_on = None

PREVIOUS = (
    "'correct_transcript', 'index_retrieval_source', 'index_retrieval_semantics', "
    "'reprocess_material_offline_asr', 'generate_material_image', 'regenerate_chapter_sections', "
    "'evaluate_interview_policy_shadow', 'evaluate_interview_policy_outcome', 'organize_material', "
    "'update_topic_map', 'generate_book_editorial_copy', 'generate_book_review', "
    "'apply_book_review_answers', 'generate_book_revision_proposal', 'generate_chapter_section_revision', "
    "'prepare_chapter', 'update_book_draft', 'evaluate_share_video_candidate'"
)


def _replace(values: str) -> None:
    op.drop_constraint("ck_memoir_editing_jobs_type", "memoir_editing_jobs", type_="check")
    op.create_check_constraint("ck_memoir_editing_jobs_type", "memoir_editing_jobs", f"job_type IN ({values})")


def upgrade() -> None:
    _replace(PREVIOUS + ", 'generate_book_audio_chunk'")


def downgrade() -> None:
    op.execute("DELETE FROM memoir_editing_jobs WHERE job_type = 'generate_book_audio_chunk'")
    _replace(PREVIOUS)
