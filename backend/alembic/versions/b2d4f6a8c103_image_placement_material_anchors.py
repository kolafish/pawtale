"""Anchor book illustrations to their source materials across directory changes."""

from alembic import op
import sqlalchemy as sa


revision = "b2d4f6a8c103"
down_revision = "c9f4b7e2a601"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("memoir_book_image_placements", sa.Column(
        "source_utterance_ids_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'"),
    ))
    op.execute(sa.text("""
        UPDATE memoir_book_image_placements AS placement
        SET source_utterance_ids_json = CASE
            WHEN asset.source_utterance_id IS NOT NULL THEN json_build_array(asset.source_utterance_id)
            ELSE COALESCE((
                SELECT json_agg(anchors.utterance_id ORDER BY anchors.utterance_id)
                FROM (
                    SELECT DISTINCT material.utterance_id
                    FROM memoir_section_materials AS material
                    JOIN memoir_sections AS section ON section.id = material.section_id
                    WHERE material.project_id = placement.project_id
                      AND section.project_id = placement.project_id
                      AND section.chapter_id = placement.chapter_id
                      AND material.include_in_book AND section.include_in_book
                      AND section.status = 'active'
                ) AS anchors
            ), '[]'::json)
        END
        FROM memoir_image_assets AS asset
        WHERE asset.id = placement.image_asset_id
          AND (placement.placement_type IN ('chapter_opening', 'chapter_inline')
               OR (placement.placement_type = 'appendix' AND asset.source_utterance_id IS NOT NULL
                   AND asset.source_type IN ('share_video_background', 'chapter_illustration', 'utterance_illustration')))
    """))


def downgrade() -> None:
    op.drop_column("memoir_book_image_placements", "source_utterance_ids_json")
