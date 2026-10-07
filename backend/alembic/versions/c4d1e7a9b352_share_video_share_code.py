"""Give every share video a short public code for cards and mini program codes."""

import sqlalchemy as sa
from alembic import op

revision = "c4d1e7a9b352"
down_revision = "b6a8d3e1f902"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("memoir_material_share_videos", sa.Column("share_code", sa.String(length=20), nullable=True))
    op.execute(
        "UPDATE memoir_material_share_videos "
        "SET share_code = substr(md5(id || random()::text || clock_timestamp()::text), 1, 20)"
    )
    op.alter_column("memoir_material_share_videos", "share_code", nullable=False)
    op.create_unique_constraint(
        "uq_memoir_material_share_videos_share_code", "memoir_material_share_videos", ["share_code"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_memoir_material_share_videos_share_code", "memoir_material_share_videos", type_="unique")
    op.drop_column("memoir_material_share_videos", "share_code")
