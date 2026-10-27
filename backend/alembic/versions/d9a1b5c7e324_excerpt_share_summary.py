"""Persist the author's confirmed selected-text share summary."""
from alembic import op
import sqlalchemy as sa

revision = "d9a1b5c7e324"
down_revision = "c8f0a4b6e213"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("memoir_excerpt_shares", sa.Column("summary", sa.String(48), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("memoir_excerpt_shares", "summary")
