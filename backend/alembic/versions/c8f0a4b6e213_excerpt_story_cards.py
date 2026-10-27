"""Story titles and managed images for selected-text sharing."""
from alembic import op
import sqlalchemy as sa

revision = "c8f0a4b6e213"
down_revision = "b7e9f3a5d102"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("memoir_excerpt_shares", sa.Column("title", sa.Text(), nullable=False, server_default=""))
    op.add_column("memoir_excerpt_shares", sa.Column("image_asset_id", sa.String(36), nullable=True))
    op.create_foreign_key("fk_excerpt_share_image_asset", "memoir_excerpt_shares", "memoir_image_assets", ["image_asset_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    op.drop_column("memoir_excerpt_shares", "image_asset_id")
    op.drop_column("memoir_excerpt_shares", "title")
