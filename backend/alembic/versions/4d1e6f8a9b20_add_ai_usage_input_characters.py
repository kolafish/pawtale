"""Store character counts for character-priced AI calls."""

from alembic import op
import sqlalchemy as sa


revision = "4d1e6f8a9b20"
down_revision = "3c9f1e4a6b82"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_usage_records",
        sa.Column("input_characters", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ai_usage_records", "input_characters")
