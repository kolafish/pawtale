"""Let all ordinary AI sessions retain a topic anchor."""

from typing import Sequence, Union

from alembic import op


revision: str = "a7c2e9f4b601"
down_revision: Union[str, Sequence[str], None] = "e4b7c2a9f615"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("ck_memoir_voice_sessions_anchor_mode", "memoir_voice_sessions", type_="check")
    op.create_check_constraint(
        "ck_memoir_voice_sessions_anchor_mode",
        "memoir_voice_sessions",
        "anchor_framework_item_id IS NULL OR "
        "(interview_type = 'ai_interview' AND session_goal = 'open_narration')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_memoir_voice_sessions_anchor_mode", "memoir_voice_sessions", type_="check")
    op.create_check_constraint(
        "ck_memoir_voice_sessions_anchor_mode",
        "memoir_voice_sessions",
        "anchor_framework_item_id IS NULL OR "
        "(guidance_mode = 'guided_interview' AND session_goal = 'open_narration')",
    )
