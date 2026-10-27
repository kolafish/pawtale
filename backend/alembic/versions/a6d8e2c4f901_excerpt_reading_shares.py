"""Immutable, independently authorized reading excerpts."""
from alembic import op
import sqlalchemy as sa

revision = "a6d8e2c4f901"
down_revision = "f4b8d2a6c910"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("memoir_excerpt_shares",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chapter_id", sa.String(36), sa.ForeignKey("memoir_book_plan_chapters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_id", sa.String(36), sa.ForeignKey("memoir_book_chapter_draft_versions.id", ondelete="SET NULL")),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("book_title", sa.Text(), nullable=False),
        sa.Column("chapter_title", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("char_length(content) BETWEEN 1 AND 120", name="ck_memoir_excerpt_share_length"))
    op.create_index("ix_memoir_excerpt_shares_project_id", "memoir_excerpt_shares", ["project_id"])


def downgrade() -> None:
    op.drop_table("memoir_excerpt_shares")
