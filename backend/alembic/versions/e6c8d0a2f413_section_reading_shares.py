"""Share saved stories by stable section identity."""
from alembic import op
import sqlalchemy as sa

revision = "e6c8d0a2f413"
down_revision = "f8a3c5e7b209"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("memoir_section_shares",
        sa.Column("section_id", sa.String(36), sa.ForeignKey("memoir_sections.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.add_column("memoir_share_likes", sa.Column("section_id", sa.String(36), nullable=True))
    op.create_foreign_key("fk_memoir_share_likes_section", "memoir_share_likes", "memoir_sections", ["section_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_memoir_share_likes_section_id", "memoir_share_likes", ["section_id"])
    op.create_unique_constraint("uq_memoir_share_likes_user_section", "memoir_share_likes", ["user_id", "section_id"])
    op.drop_constraint("ck_memoir_share_likes_one_target", "memoir_share_likes", type_="check")
    op.create_check_constraint("ck_memoir_share_likes_one_target", "memoir_share_likes", "num_nonnulls(book_output_id, chapter_id, section_id, share_video_id) = 1")


def downgrade() -> None:
    op.execute("DELETE FROM memoir_share_likes WHERE section_id IS NOT NULL")
    op.drop_constraint("ck_memoir_share_likes_one_target", "memoir_share_likes", type_="check")
    op.create_check_constraint("ck_memoir_share_likes_one_target", "memoir_share_likes", "num_nonnulls(book_output_id, chapter_id, share_video_id) = 1")
    op.drop_constraint("uq_memoir_share_likes_user_section", "memoir_share_likes", type_="unique")
    op.drop_index("ix_memoir_share_likes_section_id", "memoir_share_likes")
    op.drop_constraint("fk_memoir_share_likes_section", "memoir_share_likes", type_="foreignkey")
    op.drop_column("memoir_share_likes", "section_id")
    op.drop_table("memoir_section_shares")
