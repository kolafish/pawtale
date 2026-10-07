"""Store the model's scores, reason and opening sentence for share video candidates."""

import sqlalchemy as sa
from alembic import op

revision = "d8e2f4a6c135"
down_revision = "c4d1e7a9b352"
branch_labels = None
depends_on = None

TABLE = "memoir_share_video_candidates"


def upgrade() -> None:
    for column in ("emotion_score", "visual_score", "standalone_score"):
        op.add_column(TABLE, sa.Column(column, sa.Integer(), nullable=True))
    op.add_column(TABLE, sa.Column("reason", sa.Text(), nullable=False, server_default=""))
    op.add_column(TABLE, sa.Column("hook_text", sa.Text(), nullable=False, server_default=""))
    op.alter_column(TABLE, "reason", server_default=None)
    op.alter_column(TABLE, "hook_text", server_default=None)


def downgrade() -> None:
    for column in ("hook_text", "reason", "standalone_score", "visual_score", "emotion_score"):
        op.drop_column(TABLE, column)
