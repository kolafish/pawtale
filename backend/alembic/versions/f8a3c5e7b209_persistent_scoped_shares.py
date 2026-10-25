"""Keep live reading links and separate video publication from generation."""
from alembic import op
import sqlalchemy as sa

revision = "f8a3c5e7b209"
down_revision = "b2d4f6a8c103"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("memoir_book_shares", "memoir_chapter_shares"):
        op.alter_column(table, "expires_at", existing_type=sa.DateTime(), nullable=True)
        op.execute(sa.text(f"UPDATE {table} SET expires_at = NULL WHERE expires_at > (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')"))
    op.add_column("memoir_material_share_videos", sa.Column("share_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("memoir_material_share_videos", sa.Column("rendered_share_code", sa.String(20), nullable=True))
    # Existing completed clips were already public. Preserve their cards and codes.
    op.execute("UPDATE memoir_material_share_videos SET share_enabled = true, rendered_share_code = share_code WHERE status = 'succeeded'")


def downgrade() -> None:
    for table in ("memoir_book_shares", "memoir_chapter_shares"):
        op.execute(sa.text(f"UPDATE {table} SET expires_at = (CURRENT_TIMESTAMP AT TIME ZONE 'UTC') + interval '7 days' WHERE expires_at IS NULL"))
        op.alter_column(table, "expires_at", existing_type=sa.DateTime(), nullable=False)
    op.drop_column("memoir_material_share_videos", "rendered_share_code")
    op.drop_column("memoir_material_share_videos", "share_enabled")
