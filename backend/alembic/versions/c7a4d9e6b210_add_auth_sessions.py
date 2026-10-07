"""add revocable auth sessions

Revision ID: c7a4d9e6b210
Revises: b7e2c4d6f809
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c7a4d9e6b210"
down_revision: Union[str, Sequence[str], None] = "b7e2c4d6f809"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "auth_sessions",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("token_hash"),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])
    op.create_table(
        "auth_login_attempts",
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("window_start", sa.DateTime(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.CheckConstraint("attempts > 0", name="ck_auth_login_attempts_positive"),
        sa.PrimaryKeyConstraint("key_hash"),
    )
    op.create_index("ix_auth_login_attempts_window_start", "auth_login_attempts", ["window_start"])


def downgrade() -> None:
    op.drop_index("ix_auth_login_attempts_window_start", table_name="auth_login_attempts")
    op.drop_table("auth_login_attempts")
    op.drop_index("ix_auth_sessions_expires_at", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")
