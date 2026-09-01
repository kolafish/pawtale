"""add story structure automation event

Revision ID: a1c4d9e7b203
Revises: d4a8b7c9e201
Create Date: 2026-09-01 21:50:00
"""

from alembic import op


revision: str = "a1c4d9e7b203"
down_revision: str | None = "d4a8b7c9e201"
branch_labels: str | None = None
depends_on: str | None = None


CONSTRAINT_NAME = "ck_memoir_automation_events_event_type"
PREVIOUS_TYPES = "'chapter_draft_generated', 'book_output_generated', 'book_editorial_copy_generated'"
CURRENT_TYPES = PREVIOUS_TYPES + ", 'story_structure_updated'"


def _replace_constraint(allowed_types: str) -> None:
    op.drop_constraint(CONSTRAINT_NAME, "memoir_automation_events", type_="check")
    op.create_check_constraint(
        CONSTRAINT_NAME,
        "memoir_automation_events",
        f"event_type IN ({allowed_types})",
    )


def upgrade() -> None:
    _replace_constraint(CURRENT_TYPES)


def downgrade() -> None:
    _replace_constraint(PREVIOUS_TYPES)
