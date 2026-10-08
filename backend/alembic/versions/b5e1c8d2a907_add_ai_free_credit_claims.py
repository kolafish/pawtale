"""Remember WeChat identities that already received free AI credit before deleting their account."""

import sqlalchemy as sa
from alembic import op

revision = "b5e1c8d2a907"
down_revision = "d8e2f4a6c135"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_free_credit_claims",
        sa.Column("key_hash", sa.String(64), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("ai_free_credit_claims")
