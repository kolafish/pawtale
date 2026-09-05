"""Persist editing job lifetime and changes received during a running job."""
from alembic import op
import sqlalchemy as sa

revision = "e7b2a901c463"
down_revision = "a1c4d9e7b203"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("memoir_editing_jobs", sa.Column("first_started_at", sa.DateTime(), nullable=True))
    op.add_column("memoir_editing_jobs", sa.Column("rerun_requested", sa.Boolean(), nullable=False,
                                                  server_default=sa.false()))
    op.execute("UPDATE memoir_editing_jobs SET first_started_at = locked_at WHERE status = 'running'")


def downgrade():
    op.drop_column("memoir_editing_jobs", "rerun_requested")
    op.drop_column("memoir_editing_jobs", "first_started_at")
