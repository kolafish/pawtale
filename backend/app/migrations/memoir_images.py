from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.schema import Column, Table

from app.database import Base, register_models
from app.migrations.schema_inspection import table_exists


MEMOIR_IMAGE_TABLES = (
    "memoir_image_assets",
    "memoir_book_image_placements",
)


def _memoir_image_tables_from_metadata() -> tuple[Table, ...]:
    register_models()
    return tuple(Base.metadata.tables[table_name] for table_name in MEMOIR_IMAGE_TABLES)


def _column_schema(column: Column) -> dict[str, object]:
    return {
        "name": column.name,
        "nullable": bool(column.nullable),
        "primary_key": bool(column.primary_key),
    }


def expected_memoir_image_schema() -> dict[str, list[dict[str, object]]]:
    return {
        table.name: [_column_schema(column) for column in table.columns]
        for table in _memoir_image_tables_from_metadata()
    }


async def _table_column_schema(conn, table: str) -> list[dict[str, object]]:
    columns, primary_key = await conn.run_sync(
        lambda sync_conn: (
            inspect(sync_conn).get_columns(table),
            inspect(sync_conn).get_pk_constraint(table),
        )
    )
    primary_key_columns = set(primary_key.get("constrained_columns") or ())
    return [
        {
            "name": column["name"],
            "nullable": bool(column["nullable"]),
            "primary_key": column["name"] in primary_key_columns,
        }
        for column in columns
    ]


async def _relocate_source_linked_appendix_placements(conn) -> int:
    required_tables = (
        "memoir_image_assets",
        "memoir_book_image_placements",
        "memoir_sections",
        "memoir_section_materials",
    )
    for table_name in required_tables:
        if not await table_exists(conn, table_name):
            return 0
    await conn.execute(
        text(
            """
            UPDATE memoir_book_image_placements
            SET enabled = FALSE,
                updated_at = CURRENT_TIMESTAMP
            WHERE enabled IS TRUE
              AND placement_type = 'appendix'
              AND EXISTS (
                    SELECT 1
                    FROM memoir_book_image_placements AS inline_placement
                    WHERE inline_placement.project_id =
                          memoir_book_image_placements.project_id
                      AND inline_placement.image_asset_id =
                          memoir_book_image_placements.image_asset_id
                      AND inline_placement.placement_type = 'chapter_inline'
                      AND inline_placement.enabled IS TRUE
              )
              AND EXISTS (
                    SELECT 1
                    FROM memoir_image_assets AS asset
                    JOIN memoir_section_materials AS material
                      ON material.project_id = asset.project_id
                     AND material.utterance_id = asset.source_utterance_id
                    JOIN memoir_sections AS section
                      ON section.project_id = material.project_id
                     AND section.id = material.section_id
                    WHERE asset.id = memoir_book_image_placements.image_asset_id
                      AND asset.project_id = memoir_book_image_placements.project_id
                      AND asset.source_type IN (
                        'share_video_background',
                        'chapter_illustration',
                        'utterance_illustration'
                      )
                      AND material.include_in_book IS TRUE
                      AND section.include_in_book IS TRUE
                      AND section.status = 'active'
              )
            """
        )
    )
    result = await conn.execute(
        text(
            """
            UPDATE memoir_book_image_placements
            SET placement_type = 'chapter_inline',
                chapter_id = (
                    SELECT section.chapter_id
                    FROM memoir_image_assets AS asset
                    JOIN memoir_section_materials AS material
                      ON material.project_id = asset.project_id
                     AND material.utterance_id = asset.source_utterance_id
                    JOIN memoir_sections AS section
                      ON section.project_id = material.project_id
                     AND section.id = material.section_id
                    WHERE asset.id = memoir_book_image_placements.image_asset_id
                      AND asset.project_id = memoir_book_image_placements.project_id
                      AND asset.source_type IN (
                        'share_video_background',
                        'chapter_illustration',
                        'utterance_illustration'
                      )
                      AND material.include_in_book IS TRUE
                      AND section.include_in_book IS TRUE
                      AND section.status = 'active'
                    LIMIT 1
                ),
                updated_at = CURRENT_TIMESTAMP
            WHERE enabled IS TRUE
              AND placement_type = 'appendix'
              AND EXISTS (
                    SELECT 1
                    FROM memoir_image_assets AS asset
                    JOIN memoir_section_materials AS material
                      ON material.project_id = asset.project_id
                     AND material.utterance_id = asset.source_utterance_id
                    JOIN memoir_sections AS section
                      ON section.project_id = material.project_id
                     AND section.id = material.section_id
                    WHERE asset.id = memoir_book_image_placements.image_asset_id
                      AND asset.project_id = memoir_book_image_placements.project_id
                      AND asset.source_type IN (
                        'share_video_background',
                        'chapter_illustration',
                        'utterance_illustration'
                      )
                      AND material.include_in_book IS TRUE
                      AND section.include_in_book IS TRUE
                      AND section.status = 'active'
              )
            """
        )
    )
    return max(int(result.rowcount or 0), 0)


async def migrate_memoir_image_schema(engine: AsyncEngine) -> dict[str, object]:
    changed_tables: list[str] = []
    async with engine.begin() as conn:
        for table in _memoir_image_tables_from_metadata():
            if not await table_exists(conn, table.name):
                await conn.run_sync(lambda sync_conn, table=table: table.create(sync_conn, checkfirst=True))
                changed_tables.append(table.name)
        relocated_placements = await _relocate_source_linked_appendix_placements(conn)
    return {
        "changed_tables": changed_tables,
        "relocated_source_linked_placements": relocated_placements,
    }


async def assert_memoir_image_schema(engine: AsyncEngine) -> None:
    expected_schema = {
        table: {str(column["name"]): column for column in columns}
        for table, columns in expected_memoir_image_schema().items()
    }
    missing_tables: list[str] = []
    missing_columns: list[str] = []
    column_mismatches: list[str] = []
    async with engine.begin() as conn:
        for table_name, expected_columns in expected_schema.items():
            if not await table_exists(conn, table_name):
                missing_tables.append(table_name)
                continue
            actual_schema = {
                str(column["name"]): column
                for column in await _table_column_schema(conn, table_name)
            }
            expected_names = set(expected_columns)
            actual_names = set(actual_schema)
            missing_columns.extend(
                f"{table_name}.{column}"
                for column in sorted(expected_names - actual_names)
            )
            for column_name in sorted(expected_names & actual_names):
                expected = expected_columns[column_name]
                actual = actual_schema[column_name]
                for attribute in ("nullable", "primary_key"):
                    if expected[attribute] != actual[attribute]:
                        column_mismatches.append(
                            f"{table_name}.{column_name}.{attribute} "
                            f"expected={expected[attribute]} actual={actual[attribute]}"
                        )

    if missing_tables or missing_columns or column_mismatches:
        details: list[str] = []
        if missing_tables:
            details.append(f"missing tables: {', '.join(sorted(missing_tables))}")
        if missing_columns:
            details.append(f"missing columns: {', '.join(sorted(missing_columns))}")
        if column_mismatches:
            details.append(f"column mismatches: {', '.join(sorted(column_mismatches))}")
        raise RuntimeError(
            "Memoir image schema mismatch; "
            + "; ".join(details)
            + "; run `PYTHONPATH=backend python backend/scripts/migrate_runtime_schema.py` before starting the API"
        )
