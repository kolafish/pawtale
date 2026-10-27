"""Default unselected illustration positions to automatic distribution."""
from alembic import op
import sqlalchemy as sa

revision = "f4b8d2a6c910"
down_revision = "e7a1c9d3f620"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_memoir_book_image_inline_position", "memoir_book_image_placements", type_="check")
    op.create_check_constraint("ck_memoir_book_image_inline_position", "memoir_book_image_placements", "inline_position IN ('auto', 'before', 'middle', 'after')")
    op.alter_column("memoir_book_image_placements", "inline_position", existing_type=sa.String(10), server_default="auto")


def downgrade() -> None:
    op.execute("UPDATE memoir_book_image_placements SET inline_position='after' WHERE inline_position='auto'")
    op.drop_constraint("ck_memoir_book_image_inline_position", "memoir_book_image_placements", type_="check")
    op.create_check_constraint("ck_memoir_book_image_inline_position", "memoir_book_image_placements", "inline_position IN ('before', 'middle', 'after')")
    op.alter_column("memoir_book_image_placements", "inline_position", existing_type=sa.String(10), server_default="after")
