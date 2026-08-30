"""add memoir memory activation traces

Revision ID: c84f2b73e610
Revises: a91e5c72d4b8
Create Date: 2026-08-30 23:10:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c84f2b73e610"
down_revision: Union[str, Sequence[str], None] = "a91e5c72d4b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "memoir_memory_activation_traces",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("voice_session_id", sa.String(length=36), nullable=False),
        sa.Column("current_message_id", sa.String(length=36), nullable=True),
        sa.Column("current_utterance_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("should_retrieve", sa.Boolean(), nullable=False),
        sa.Column("query_count", sa.Integer(), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("selected_count", sa.Integer(), nullable=False),
        sa.Column("prompt_version", sa.String(length=80), nullable=False),
        sa.Column("requested_model", sa.String(length=120), nullable=True),
        sa.Column("resolved_model", sa.String(length=120), nullable=True),
        sa.Column("provider", sa.String(length=80), nullable=True),
        sa.Column("elapsed_ms", sa.Integer(), nullable=False),
        sa.Column("trace_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "query_count >= 0 AND candidate_count >= 0 AND selected_count >= 0",
            name="ck_memoir_memory_activation_traces_nonnegative_counts",
        ),
        sa.CheckConstraint(
            "status IN ('skipped', 'succeeded', 'failed')",
            name="ck_memoir_memory_activation_traces_status",
        ),
        sa.ForeignKeyConstraint(
            ["current_message_id"],
            ["memoir_chat_messages.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["current_utterance_id"],
            ["memoir_voice_utterances.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["voice_session_id"],
            ["memoir_voice_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_memoir_memory_activation_traces_current_message_id",
        "memoir_memory_activation_traces",
        ["current_message_id"],
        unique=False,
    )
    op.create_index(
        "ix_memoir_memory_activation_traces_project_created",
        "memoir_memory_activation_traces",
        ["project_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_memoir_memory_activation_traces_session_created",
        "memoir_memory_activation_traces",
        ["voice_session_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_memoir_memory_activation_traces_session_created",
        table_name="memoir_memory_activation_traces",
    )
    op.drop_index(
        "ix_memoir_memory_activation_traces_project_created",
        table_name="memoir_memory_activation_traces",
    )
    op.drop_index(
        "ix_memoir_memory_activation_traces_current_message_id",
        table_name="memoir_memory_activation_traces",
    )
    op.drop_table("memoir_memory_activation_traces")
