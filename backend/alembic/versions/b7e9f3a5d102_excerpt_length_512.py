"""Allow selected-text shares up to 512 Unicode characters."""
from alembic import op

revision = "b7e9f3a5d102"
down_revision = "a6d8e2c4f901"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_memoir_excerpt_share_length", "memoir_excerpt_shares", type_="check")
    op.create_check_constraint("ck_memoir_excerpt_share_length", "memoir_excerpt_shares", "char_length(content) BETWEEN 1 AND 512")


def downgrade() -> None:
    # Refuse incompatible existing content rather than truncate published shares.
    op.drop_constraint("ck_memoir_excerpt_share_length", "memoir_excerpt_shares", type_="check")
    op.create_check_constraint("ck_memoir_excerpt_share_length", "memoir_excerpt_shares", "char_length(content) BETWEEN 1 AND 120")
