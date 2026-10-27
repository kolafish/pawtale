"""Keep inline illustrations before, within or after their source section."""
from alembic import op
import sqlalchemy as sa

revision = "e7a1c9d3f620"
down_revision = "d2f6a8c1e903"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("memoir_book_image_placements", sa.Column("inline_position", sa.String(10), nullable=False, server_default="after"))
    op.create_check_constraint("ck_memoir_book_image_inline_position", "memoir_book_image_placements", "inline_position IN ('before', 'middle', 'after')")


def downgrade() -> None:
    op.drop_constraint("ck_memoir_book_image_inline_position", "memoir_book_image_placements", type_="check")
    op.drop_column("memoir_book_image_placements", "inline_position")
