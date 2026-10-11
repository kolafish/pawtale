"""Add AI credit pack orders paid with WeChat virtual payment."""

from alembic import op
import sqlalchemy as sa


revision = "c5e1a7d3b920"
down_revision = "a3d9f6c2e817"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_credit_orders",
        sa.Column("out_trade_no", sa.String(32), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("product_id", sa.String(64), nullable=False),
        sa.Column("price_fen", sa.Integer(), nullable=False),
        sa.Column("credit_rmb", sa.Numeric(14, 6), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("wx_order_id", sa.String(64), nullable=True),
        sa.Column("refunded_fen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'paid', 'refunded')", name="ck_ai_credit_orders_status"
        ),
    )
    op.create_index("ix_ai_credit_orders_user_id", "ai_credit_orders", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_ai_credit_orders_user_id", table_name="ai_credit_orders")
    op.drop_table("ai_credit_orders")
