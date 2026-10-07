"""add revocable book reading links

Revision ID: e3d9a7c1b502
Revises: c7a4d9e6b210
"""
from alembic import op
import sqlalchemy as sa


revision = "e3d9a7c1b502"
down_revision = "c7a4d9e6b210"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memoir_book_shares",
        sa.Column("output_id", sa.String(36), sa.ForeignKey("memoir_book_outputs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("version_id", sa.String(36), sa.ForeignKey("memoir_book_output_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("memoir_book_shares")
