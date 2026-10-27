"""Give newly created AI credit accounts five yuan; preserve existing balances."""

from alembic import op

revision = "e6b2c8d4f019"
down_revision = "d9a1b5c7e324"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("ai_credit_accounts", "free_credit_rmb", server_default="5")


def downgrade() -> None:
    op.alter_column("ai_credit_accounts", "free_credit_rmb", server_default="10")
