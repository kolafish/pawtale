"""Add manual grants and usage debit transactions."""

from alembic import op
import sqlalchemy as sa


revision = "3c9f1e4a6b82"
down_revision = "2b8e0d3f5a71"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_credit_transactions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "actor_user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("transaction_type", sa.String(30), nullable=False),
        sa.Column("amount_rmb", sa.Numeric(14, 6), nullable=False),
        sa.Column(
            "usage_record_id",
            sa.String(36),
            sa.ForeignKey("ai_usage_records.id", ondelete="SET NULL"),
            nullable=True,
            unique=True,
        ),
        sa.Column("operation_id", sa.String(36), nullable=True),
        sa.Column("note", sa.String(255), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("amount_rmb <> 0", name="ck_ai_credit_transactions_nonzero_amount"),
    )
    op.create_index(
        "ix_ai_credit_transactions_user_id", "ai_credit_transactions", ["user_id"]
    )
    op.create_index(
        "ix_ai_credit_transactions_actor_user_id", "ai_credit_transactions", ["actor_user_id"]
    )
    op.create_index(
        "ix_ai_credit_transactions_transaction_type",
        "ai_credit_transactions",
        ["transaction_type"],
    )
    op.create_index(
        "ix_ai_credit_transactions_operation_id",
        "ai_credit_transactions",
        ["operation_id"],
    )
    op.create_index(
        "ix_ai_credit_transactions_created_at", "ai_credit_transactions", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_ai_credit_transactions_created_at", table_name="ai_credit_transactions")
    op.drop_index("ix_ai_credit_transactions_operation_id", table_name="ai_credit_transactions")
    op.drop_index("ix_ai_credit_transactions_transaction_type", table_name="ai_credit_transactions")
    op.drop_index("ix_ai_credit_transactions_actor_user_id", table_name="ai_credit_transactions")
    op.drop_index("ix_ai_credit_transactions_user_id", table_name="ai_credit_transactions")
    op.drop_table("ai_credit_transactions")
