"""Allow the durable memoir draft coordinator in the existing editing queue."""
from alembic import op

revision = "f2c8a419d607"
down_revision = "e7b2a901c463"
branch_labels = None
depends_on = None

PREVIOUS = (
    "'correct_transcript', 'index_retrieval_source', 'index_retrieval_semantics', "
    "'reprocess_material_offline_asr', 'generate_material_image', 'regenerate_chapter_sections', "
    "'evaluate_interview_policy_shadow', 'evaluate_interview_policy_outcome', 'organize_material', "
    "'update_topic_map', 'generate_book_editorial_copy', 'generate_book_review', "
    "'apply_book_review_answers', 'generate_book_revision_proposal', 'generate_chapter_section_revision', "
    "'prepare_chapter'"
)


def _replace(values):
    op.drop_constraint("ck_memoir_editing_jobs_type", "memoir_editing_jobs", type_="check")
    op.create_check_constraint("ck_memoir_editing_jobs_type", "memoir_editing_jobs", f"job_type IN ({values})")


def upgrade():
    _replace(PREVIOUS + ", 'update_book_draft'")
    op.execute("""
        UPDATE memoir_book_chapter_drafts d SET metadata_json = d.metadata_json::jsonb ||
          jsonb_build_object('confirmed_version_id', (
            SELECT v.id FROM memoir_book_chapter_draft_versions v WHERE v.draft_id = d.id
            ORDER BY v.version DESC, v.created_at DESC, v.id DESC LIMIT 1
          )) WHERE d.status = 'confirmed'
    """)


def downgrade():
    op.execute("DELETE FROM memoir_editing_jobs WHERE job_type = 'update_book_draft'")
    _replace(PREVIOUS)
