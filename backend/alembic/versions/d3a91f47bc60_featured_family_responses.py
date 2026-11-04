"""Per-card scope and author-selected chapter-end family responses."""
from alembic import op
import sqlalchemy as sa

revision = "d3a91f47bc60"
down_revision = "e6b2c8d4f019"
branch_labels = None
depends_on = None


def targets(include_excerpt: bool) -> None:
    kinds = "'book','chapter','story','video'" + (",'excerpt'" if include_excerpt else "")
    for table in ("memoir_share_responses", "memoir_share_response_voices"):
        name = f"ck_{table}_target"
        op.drop_constraint(name, table, type_="check")
        op.create_check_constraint(name, table, f"target_kind IN ({kinds})")


def upgrade() -> None:
    targets(True)
    op.add_column("memoir_excerpt_shares", sa.Column("allow_book_link", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("memoir_share_responses", sa.Column("can_feature", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("memoir_share_responses", sa.Column("featured_at", sa.DateTime(), nullable=True))
    op.add_column("memoir_share_responses", sa.Column("featured_chapter_id", sa.String(36),
        sa.ForeignKey("memoir_book_plan_chapters.id", ondelete="SET NULL"), nullable=True))


def downgrade() -> None:
    # The old schema cannot represent responses addressed to an excerpt.
    targets(False)
    op.drop_column("memoir_share_responses", "featured_chapter_id")
    op.drop_column("memoir_share_responses", "featured_at")
    op.drop_column("memoir_share_responses", "can_feature")
    op.drop_column("memoir_excerpt_shares", "allow_book_link")
