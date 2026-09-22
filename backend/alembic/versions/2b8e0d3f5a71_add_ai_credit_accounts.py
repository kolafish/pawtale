"""Add per-user AI credit account foundation."""

from alembic import op
import sqlalchemy as sa


revision = "2b8e0d3f5a71"
down_revision = "1a7d9c2e4f60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_credit_accounts",
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "free_credit_rmb",
            sa.Numeric(14, 6),
            nullable=False,
            server_default="10",
        ),
        sa.Column(
            "recharged_credit_rmb",
            sa.Numeric(14, 6),
            nullable=False,
            server_default="0",
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("ai_credit_accounts")
