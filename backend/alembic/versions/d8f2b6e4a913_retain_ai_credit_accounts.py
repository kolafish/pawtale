"""Retain credit wallets and ledgers across account deletion and re-registration."""

from alembic import op
import sqlalchemy as sa

revision = "d8f2b6e4a913"
down_revision = "c5e1a7d3b920"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Pending orders did not previously require a wallet to exist.
    op.execute("""
        INSERT INTO ai_credit_accounts
            (user_id, free_credit_rmb, recharged_credit_rmb, manual_grant_rmb, created_at, updated_at)
        SELECT user_id, 10, 0, 0, now(), now() FROM (
            SELECT user_id FROM ai_credit_orders UNION SELECT user_id FROM ai_credit_transactions
        ) AS owners
        ON CONFLICT (user_id) DO NOTHING
    """)
    op.add_column("ai_credit_accounts", sa.Column("id", sa.String(36), nullable=True))
    op.execute("UPDATE ai_credit_accounts SET id = user_id")
    op.alter_column("ai_credit_accounts", "id", nullable=False)
    op.drop_constraint("ai_credit_accounts_pkey", "ai_credit_accounts", type_="primary")
    op.create_primary_key("ai_credit_accounts_pkey", "ai_credit_accounts", ["id"])
    op.create_unique_constraint("ai_credit_accounts_user_id_key", "ai_credit_accounts", ["user_id"])
    for table in ("ai_credit_accounts", "ai_credit_transactions", "ai_credit_orders"):
        op.drop_constraint(f"{table}_user_id_fkey", table, type_="foreignkey")
        op.alter_column(table, "user_id", nullable=True)
        op.create_foreign_key(f"{table}_user_id_fkey", table, "users", ["user_id"], ["id"], ondelete="SET NULL")
        if table != "ai_credit_accounts":
            op.add_column(table, sa.Column("account_id", sa.String(36), nullable=True))
            op.execute(f"UPDATE {table} SET account_id = user_id")
            op.alter_column(table, "account_id", nullable=False)
            op.create_foreign_key(f"{table}_account_id_fkey", table, "ai_credit_accounts", ["account_id"], ["id"], ondelete="RESTRICT")
            op.create_index(f"ix_{table}_account_id", table, ["account_id"])
    op.rename_table("ai_free_credit_claims", "ai_credit_identities")
    op.add_column("ai_credit_identities", sa.Column("account_id", sa.String(36), nullable=True))
    op.create_foreign_key("ai_credit_identities_account_id_fkey", "ai_credit_identities", "ai_credit_accounts", ["account_id"], ["id"], ondelete="RESTRICT")
    op.create_index("ix_ai_credit_identities_account_id", "ai_credit_identities", ["account_id"])


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT 1 FROM ai_credit_accounts WHERE user_id IS NULL)")):
        raise RuntimeError("Cannot downgrade while deleted users have retained credit wallets")
    op.drop_index("ix_ai_credit_identities_account_id", table_name="ai_credit_identities")
    op.drop_column("ai_credit_identities", "account_id")
    op.rename_table("ai_credit_identities", "ai_free_credit_claims")
    for table in ("ai_credit_orders", "ai_credit_transactions", "ai_credit_accounts"):
        if table != "ai_credit_accounts":
            op.drop_index(f"ix_{table}_account_id", table_name=table)
            op.drop_column(table, "account_id")
        op.drop_constraint(f"{table}_user_id_fkey", table, type_="foreignkey")
        op.alter_column(table, "user_id", nullable=False)
        op.create_foreign_key(f"{table}_user_id_fkey", table, "users", ["user_id"], ["id"], ondelete="CASCADE")
    op.drop_constraint("ai_credit_accounts_pkey", "ai_credit_accounts", type_="primary")
    op.drop_constraint("ai_credit_accounts_user_id_key", "ai_credit_accounts", type_="unique")
    op.create_primary_key("ai_credit_accounts_pkey", "ai_credit_accounts", ["user_id"])
    op.drop_column("ai_credit_accounts", "id")
