"""Persist confirmed reading-share excerpts for their exact saved version."""
from alembic import op
import sqlalchemy as sa

revision = "d2f6a8c1e903"
down_revision = "b8e0f2c4d631"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("memoir_chapter_shares", "memoir_section_shares"):
        op.add_column(table, sa.Column("card_version_id", sa.String(36), nullable=True))
        op.add_column(table, sa.Column("card_excerpt_start", sa.Integer(), nullable=False, server_default="0"))
        op.add_column(table, sa.Column("card_excerpt_length", sa.Integer(), nullable=False, server_default="0"))
        op.create_foreign_key(f"fk_{table}_card_version", table, "memoir_book_chapter_draft_versions",
                              ["card_version_id"], ["id"], ondelete="SET NULL")
        op.create_check_constraint(f"ck_{table}_card_excerpt", table,
                                   "card_excerpt_start >= 0 AND card_excerpt_length BETWEEN 0 AND 48")


def downgrade() -> None:
    for table in ("memoir_chapter_shares", "memoir_section_shares"):
        op.drop_constraint(f"ck_{table}_card_excerpt", table, type_="check")
        op.drop_constraint(f"fk_{table}_card_version", table, type_="foreignkey")
        for column in ("card_excerpt_length", "card_excerpt_start", "card_version_id"):
            op.drop_column(table, column)
