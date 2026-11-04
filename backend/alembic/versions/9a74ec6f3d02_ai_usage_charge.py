"""Keep new user charges separate from estimated provider costs."""
from alembic import op
import sqlalchemy as sa

revision = "9a74ec6f3d02"
down_revision = "d3a91f47bc60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_usage_records", sa.Column("charged_rmb", sa.Numeric(14, 6), nullable=True))


def downgrade() -> None:
    op.drop_column("ai_usage_records", "charged_rmb")
