"""add memoir semantic retrieval units

Revision ID: a91e5c72d4b8
Revises: f47c2a91d6e8
Create Date: 2026-08-30 20:45:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a91e5c72d4b8"
down_revision: Union[str, Sequence[str], None] = "f47c2a91d6e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OLD_EDITING_JOB_TYPES = (
    "correct_transcript",
    "reprocess_material_offline_asr",
    "generate_material_image",
    "regenerate_chapter_sections",
    "evaluate_interview_policy_shadow",
    "evaluate_interview_policy_outcome",
    "organize_material",
    "update_topic_map",
    "generate_book_editorial_copy",
    "generate_book_review",
    "apply_book_review_answers",
    "generate_book_revision_proposal",
    "generate_chapter_section_revision",
    "prepare_chapter",
    "index_retrieval_source",
)
NEW_EDITING_JOB_TYPES = OLD_EDITING_JOB_TYPES + (
    "index_retrieval_semantics",
)


def _enum_check(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def upgrade() -> None:
    op.drop_constraint(
        "ck_memoir_editing_jobs_type",
        "memoir_editing_jobs",
        type_="check",
    )
    op.create_check_constraint(
        "ck_memoir_editing_jobs_type",
        "memoir_editing_jobs",
        _enum_check("job_type", NEW_EDITING_JOB_TYPES),
    )
    op.drop_constraint(
        "ck_memoir_retrieval_entries_kind",
        "memoir_retrieval_entries",
        type_="check",
    )
    op.create_check_constraint(
        "ck_memoir_retrieval_entries_kind",
        "memoir_retrieval_entries",
        "entry_kind IN ('source_text', 'semantic_unit')",
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM memoir_retrieval_entries "
            "WHERE entry_kind = 'semantic_unit'"
        )
    )
    op.execute(
        sa.text(
            "DELETE FROM memoir_editing_jobs "
            "WHERE job_type = 'index_retrieval_semantics'"
        )
    )
    op.drop_constraint(
        "ck_memoir_retrieval_entries_kind",
        "memoir_retrieval_entries",
        type_="check",
    )
    op.create_check_constraint(
        "ck_memoir_retrieval_entries_kind",
        "memoir_retrieval_entries",
        "entry_kind IN ('source_text')",
    )
    op.drop_constraint(
        "ck_memoir_editing_jobs_type",
        "memoir_editing_jobs",
        type_="check",
    )
    op.create_check_constraint(
        "ck_memoir_editing_jobs_type",
        "memoir_editing_jobs",
        _enum_check("job_type", OLD_EDITING_JOB_TYPES),
    )
