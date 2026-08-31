"""simplify memory activation traces

Revision ID: d4a8b7c9e201
Revises: c84f2b73e610
Create Date: 2026-08-31 10:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4a8b7c9e201"
down_revision: Union[str, Sequence[str], None] = "c84f2b73e610"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_memoir_memory_activation_traces_nonnegative_counts",
        "memoir_memory_activation_traces",
        type_="check",
    )
    op.drop_column("memoir_memory_activation_traces", "should_retrieve")
    op.drop_column("memoir_memory_activation_traces", "query_count")
    op.alter_column(
        "memoir_memory_activation_traces",
        "selected_count",
        new_column_name="injected_count",
        existing_type=sa.Integer(),
        existing_nullable=False,
    )
    op.create_check_constraint(
        "ck_memoir_memory_activation_traces_nonnegative_counts",
        "memoir_memory_activation_traces",
        "candidate_count >= 0 AND injected_count >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_memoir_memory_activation_traces_nonnegative_counts",
        "memoir_memory_activation_traces",
        type_="check",
    )
    op.alter_column(
        "memoir_memory_activation_traces",
        "injected_count",
        new_column_name="selected_count",
        existing_type=sa.Integer(),
        existing_nullable=False,
    )
    op.add_column(
        "memoir_memory_activation_traces",
        sa.Column("query_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "memoir_memory_activation_traces",
        sa.Column("should_retrieve", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column(
        "memoir_memory_activation_traces",
        "query_count",
        server_default=None,
    )
    op.alter_column(
        "memoir_memory_activation_traces",
        "should_retrieve",
        server_default=None,
    )
    op.create_check_constraint(
        "ck_memoir_memory_activation_traces_nonnegative_counts",
        "memoir_memory_activation_traces",
        "query_count >= 0 AND candidate_count >= 0 AND selected_count >= 0",
    )
