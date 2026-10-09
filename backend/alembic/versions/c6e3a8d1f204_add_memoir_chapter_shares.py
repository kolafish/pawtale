"""Add independently revocable chapter reading links.

Revision ID: c6e3a8d1f204
Revises: b5e1c8d2a907
"""
from alembic import op
import sqlalchemy as sa

revision = "c6e3a8d1f204"
down_revision = "b5e1c8d2a907"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memoir_chapter_shares",
        sa.Column("chapter_id", sa.String(36), sa.ForeignKey("memoir_book_plan_chapters.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("version_id", sa.String(36), sa.ForeignKey("memoir_book_chapter_draft_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("memoir_chapter_shares")
