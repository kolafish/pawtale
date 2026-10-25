"""Private visitor recording and original transcript provenance."""
from alembic import op
import sqlalchemy as sa

revision = "b8e0f2c4d631"
down_revision = "a7d9e1b3c520"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("memoir_share_response_voices",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_kind", sa.String(20), nullable=False),
        sa.Column("target_id", sa.String(36), nullable=False),
        sa.Column("share_key", sa.String(64), nullable=False),
        sa.Column("audio_file_id", sa.String(36), sa.ForeignKey("audio_files.id"), nullable=True),
        sa.Column("asr_text", sa.Text(), nullable=False),
        sa.Column("provider_payload_json", sa.JSON(), nullable=False),
        sa.Column("provider", sa.String(100), nullable=True),
        sa.Column("start_ms", sa.Integer(), nullable=True),
        sa.Column("end_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("target_kind IN ('book','chapter','story','video')", name="ck_memoir_share_response_voices_target"),
    )
    op.create_index("ix_memoir_share_response_voices_project_created", "memoir_share_response_voices", ["project_id", "created_at"])
    op.add_column("memoir_share_responses", sa.Column("voice_capture_id", sa.String(36), nullable=True))
    op.create_foreign_key("fk_memoir_share_responses_voice", "memoir_share_responses", "memoir_share_response_voices", ["voice_capture_id"], ["id"], ondelete="SET NULL")
    op.create_unique_constraint("uq_memoir_share_responses_voice", "memoir_share_responses", ["voice_capture_id"])


def downgrade() -> None:
    op.drop_constraint("uq_memoir_share_responses_voice", "memoir_share_responses", type_="unique")
    op.drop_constraint("fk_memoir_share_responses_voice", "memoir_share_responses", type_="foreignkey")
    op.drop_column("memoir_share_responses", "voice_capture_id")
    op.drop_table("memoir_share_response_voices")
