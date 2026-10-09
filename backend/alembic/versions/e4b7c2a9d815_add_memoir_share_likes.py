"""Add likes on shared books, chapters and story videos.

Revision ID: e4b7c2a9d815
Revises: c6e3a8d1f204
"""
from alembic import op
import sqlalchemy as sa

revision = "e4b7c2a9d815"
down_revision = "c6e3a8d1f204"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memoir_share_likes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("book_output_id", sa.String(36), sa.ForeignKey("memoir_book_outputs.id", ondelete="CASCADE"), nullable=True),
        sa.Column("chapter_id", sa.String(36), sa.ForeignKey("memoir_book_plan_chapters.id", ondelete="CASCADE"), nullable=True),
        sa.Column("share_video_id", sa.String(36), sa.ForeignKey("memoir_material_share_videos.id", ondelete="CASCADE"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "num_nonnulls(book_output_id, chapter_id, share_video_id) = 1",
            name="ck_memoir_share_likes_one_target",
        ),
        sa.UniqueConstraint("user_id", "book_output_id", name="uq_memoir_share_likes_user_book"),
        sa.UniqueConstraint("user_id", "chapter_id", name="uq_memoir_share_likes_user_chapter"),
        sa.UniqueConstraint("user_id", "share_video_id", name="uq_memoir_share_likes_user_video"),
    )
    op.create_index("ix_memoir_share_likes_project_created", "memoir_share_likes", ["project_id", "created_at"])
    op.create_index("ix_memoir_share_likes_book_output_id", "memoir_share_likes", ["book_output_id"])
    op.create_index("ix_memoir_share_likes_chapter_id", "memoir_share_likes", ["chapter_id"])
    op.create_index("ix_memoir_share_likes_share_video_id", "memoir_share_likes", ["share_video_id"])


def downgrade() -> None:
    op.drop_table("memoir_share_likes")
