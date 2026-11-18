"""Keep generated video images recoverable across publication and billing."""
from alembic import op
import sqlalchemy as sa

revision = "cb380df64a17"
down_revision = "9a74ec6f3d02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memoir_share_video_image_receipts",
        sa.Column("job_id", sa.String(36), sa.ForeignKey("memoir_material_share_videos.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("slot", sa.String(32), primary_key=True),
        sa.Column("cache_key", sa.JSON(), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("pending_content", sa.LargeBinary(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("memoir_share_video_image_receipts")
