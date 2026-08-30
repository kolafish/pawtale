"""drop memoir retrieval hnsw index

Revision ID: f47c2a91d6e8
Revises: 8d3f9a1c2b47
Create Date: 2026-08-30 19:10:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = "f47c2a91d6e8"
down_revision: Union[str, Sequence[str], None] = "8d3f9a1c2b47"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index(
        "ix_memoir_retrieval_entries_embedding_hnsw",
        table_name="memoir_retrieval_entries",
    )


def downgrade() -> None:
    op.create_index(
        "ix_memoir_retrieval_entries_embedding_hnsw",
        "memoir_retrieval_entries",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
