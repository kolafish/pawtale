"""Bind GitHub backups to user authorization instead of App installation tokens."""
from alembic import op
import sqlalchemy as sa

revision = "b48a613d29e0"
down_revision = "c4d1e7a9b352"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("github_setup_states", sa.Column("installation_id", sa.BigInteger(), nullable=True))
    op.add_column("github_setup_states", sa.Column("code_verifier", sa.String(128), nullable=True))
    op.create_table(
        "github_user_authorizations",
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("github_user_id", sa.BigInteger(), nullable=False),
        sa.Column("access_token_encrypted", sa.Text(), nullable=False),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("refresh_expires_at", sa.DateTime(), nullable=True),
    )


def downgrade():
    op.drop_table("github_user_authorizations")
    op.drop_column("github_setup_states", "code_verifier")
    op.drop_column("github_setup_states", "installation_id")
