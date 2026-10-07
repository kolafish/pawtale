"""Remember when memoir book images have been selected manually."""

from alembic import op
import sqlalchemy as sa


revision = "9f2c6b4d8a10"
down_revision = "7a4c8e2b1d90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "memoir_book_image_selection_manual",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.execute(
        """
        UPDATE projects
        SET memoir_book_image_selection_manual = TRUE
        WHERE EXISTS (
            SELECT 1
            FROM memoir_book_image_placements AS placement
            WHERE placement.project_id = projects.id
        )
        """
    )


def downgrade() -> None:
    op.drop_column("projects", "memoir_book_image_selection_manual")
