"""Private family responses and explicit adoption."""
from alembic import op
import sqlalchemy as sa

revision = "a7d9e1b3c520"
down_revision = "e6c8d0a2f413"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("memoir_share_responses",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("request_id", sa.String(80), nullable=False),
        sa.Column("target_kind", sa.String(20), nullable=False),
        sa.Column("target_id", sa.String(36), nullable=False),
        sa.Column("target_title", sa.Text(), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.Column("adopted_at", sa.DateTime(), nullable=True),
        sa.Column("adopted_utterance_id", sa.String(36), sa.ForeignKey("memoir_voice_utterances.id", ondelete="SET NULL"), nullable=True),
        sa.UniqueConstraint("user_id", "request_id", name="uq_memoir_share_responses_request"),
        sa.CheckConstraint("target_kind IN ('book','chapter','story','video')", name="ck_memoir_share_responses_target"),
    )
    op.create_index("ix_memoir_share_responses_project_created", "memoir_share_responses", ["project_id", "created_at"])


def downgrade() -> None:
    op.drop_table("memoir_share_responses")
