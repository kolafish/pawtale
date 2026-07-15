from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.schema import CreateIndex, CreateTable

from app.database import Base, register_models
from app.migrations.schema_inspection import table_columns, table_exists


OBSOLETE_RUNTIME_TABLES = (
    "characters",
    "scenes",
    "memoir_answers",
    "memoir_book_auto_revision_jobs",
    "memoir_chapter_event_cluster_snapshots",
    "memoir_coverage_snapshots",
    "memoir_coverage_topics",
    "memoir_drafts",
    "memoir_event_memories",
    "memoir_memory_evidence",
    "memoir_memory_links",
    "memoir_open_questions",
    "memoir_output_versions",
    "memoir_outputs",
    "memoir_person_memories",
    "memoir_place_memories",
    "memoir_question_candidates",
    "memoir_questions",
    "memoir_session_summaries",
    "memoir_sessions",
    "memoir_source_artifacts",
    "memoir_theme_memories",
)

RUNTIME_MANAGED_TABLES = {
    "memoir_voice_utterances_fts",
    "memoir_voice_utterances_fts_config",
    "memoir_voice_utterances_fts_data",
    "memoir_voice_utterances_fts_docsize",
    "memoir_voice_utterances_fts_idx",
}

EXACT_REBUILD_TABLES = (
    "memoir_material_share_videos",
    "memoir_image_assets",
    "memoir_voice_sessions",
    "memoir_transcript_versions",
)

LEGACY_DRAFT_BLOCK_TABLES = (
    "memoir_draft_block_claims",
    "memoir_draft_block_stories",
    "memoir_draft_block_versions",
    "memoir_draft_blocks",
)


async def _runtime_table_names(conn) -> set[str]:
    rows = (
        await conn.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        )
    ).scalars()
    return {str(name) for name in rows}


async def _drop_non_auto_indexes(conn, table_name: str) -> None:
    indexes = (
        await conn.execute(text(f'PRAGMA index_list("{table_name}")'))
    ).fetchall()
    for index in indexes:
        index_name = str(index[1])
        if index_name and not index_name.startswith("sqlite_autoindex"):
            await conn.execute(text(f'DROP INDEX IF EXISTS "{index_name}"'))


async def _rebuild_exact_table(conn, table_name: str) -> None:
    table = Base.metadata.tables[table_name]
    expected_columns = [column.name for column in table.columns]
    actual_columns = await table_columns(conn, table_name)
    missing_columns = set(expected_columns) - actual_columns
    if missing_columns:
        raise RuntimeError(
            f"Cannot rebuild {table_name}; missing current columns: "
            + ", ".join(sorted(missing_columns))
        )

    temporary_name = f"{table_name}__exact_schema"
    if await table_exists(conn, temporary_name):
        raise RuntimeError(
            f"Cannot rebuild {table_name}; stale table exists: {temporary_name}"
        )
    create_sql = str(CreateTable(table).compile(dialect=conn.dialect))
    create_sql = create_sql.replace(
        f"CREATE TABLE {table_name}",
        f'CREATE TABLE "{temporary_name}"',
        1,
    )
    columns_sql = ", ".join(f'"{column}"' for column in expected_columns)

    await conn.execute(text(create_sql))
    try:
        await conn.execute(
            text(
                f'INSERT INTO "{temporary_name}" ({columns_sql}) '
                f'SELECT {columns_sql} FROM "{table_name}"'
            )
        )
        await _drop_non_auto_indexes(conn, table_name)
        await conn.execute(text(f'DROP TABLE "{table_name}"'))
        await conn.execute(
            text(f'ALTER TABLE "{temporary_name}" RENAME TO "{table_name}"')
        )
        for index in sorted(table.indexes, key=lambda item: item.name or ""):
            await conn.execute(CreateIndex(index))
    except Exception:
        if await table_exists(conn, temporary_name):
            await conn.execute(text(f'DROP TABLE "{temporary_name}"'))
        raise


async def _migrate_share_video_outputs(conn) -> None:
    table_name = "memoir_material_share_videos"
    legacy_columns = {
        "video_storage_path",
        "video_mime_type",
        "video_size_bytes",
    }
    columns = await table_columns(conn, table_name)
    present_legacy_columns = columns & legacy_columns
    if not present_legacy_columns:
        return
    if present_legacy_columns != legacy_columns:
        raise RuntimeError(
            "Cannot migrate share video outputs; incomplete legacy columns: "
            + ", ".join(sorted(present_legacy_columns))
        )
    if not await table_exists(conn, "memoir_share_video_outputs"):
        raise RuntimeError(
            "Cannot migrate share video outputs; current output table is missing"
        )

    invalid_output_count = int(
        (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM memoir_material_share_videos "
                    "WHERE video_storage_path IS NOT NULL "
                    "AND (TRIM(video_storage_path) = '' "
                    "OR video_size_bytes IS NULL OR video_size_bytes <= 0)"
                )
            )
        ).scalar_one()
    )
    if invalid_output_count:
        raise RuntimeError(
            "Cannot migrate share video outputs; "
            f"found {invalid_output_count} invalid published outputs"
        )

    succeeded_without_output_count = int(
        (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM memoir_material_share_videos "
                    "WHERE status = 'succeeded' "
                    "AND (video_storage_path IS NULL OR TRIM(video_storage_path) = '')"
                )
            )
        ).scalar_one()
    )
    if succeeded_without_output_count:
        raise RuntimeError(
            "Cannot migrate share video outputs; "
            f"found {succeeded_without_output_count} succeeded jobs without output"
        )

    conflicting_output_count = int(
        (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM memoir_material_share_videos AS jobs "
                    "JOIN memoir_share_video_outputs AS outputs ON outputs.job_id = jobs.id "
                    "WHERE jobs.video_storage_path IS NOT NULL AND ("
                    "outputs.project_id != jobs.project_id "
                    "OR outputs.storage_path != jobs.video_storage_path "
                    "OR outputs.mime_type != COALESCE(NULLIF(jobs.video_mime_type, ''), 'video/mp4') "
                    "OR outputs.size_bytes != jobs.video_size_bytes)"
                )
            )
        ).scalar_one()
    )
    if conflicting_output_count:
        raise RuntimeError(
            "Cannot migrate share video outputs; "
            f"found {conflicting_output_count} conflicting output rows"
        )

    await conn.execute(
        text(
            "INSERT OR IGNORE INTO memoir_share_video_outputs ("
            "id, job_id, project_id, storage_path, mime_type, size_bytes, created_at"
            ") SELECT id, id, project_id, video_storage_path, "
            "COALESCE(NULLIF(video_mime_type, ''), 'video/mp4'), video_size_bytes, "
            "COALESCE(completed_at, updated_at, created_at, CURRENT_TIMESTAMP) "
            "FROM memoir_material_share_videos "
            "WHERE video_storage_path IS NOT NULL AND TRIM(video_storage_path) != ''"
        )
    )

    missing_output_count = int(
        (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM memoir_material_share_videos AS jobs "
                    "LEFT JOIN memoir_share_video_outputs AS outputs ON outputs.job_id = jobs.id "
                    "WHERE jobs.video_storage_path IS NOT NULL AND outputs.job_id IS NULL"
                )
            )
        ).scalar_one()
    )
    if missing_output_count:
        raise RuntimeError(
            "Cannot migrate share video outputs; "
            f"failed to create {missing_output_count} output rows"
        )


async def _validate_image_inclusion_projection(conn) -> None:
    if "include_in_book" not in await table_columns(conn, "memoir_image_assets"):
        return
    mismatch_count = int(
        (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM memoir_image_assets AS assets "
                    "WHERE CAST(assets.include_in_book AS INTEGER) != EXISTS ("
                    "SELECT 1 FROM memoir_book_image_placements AS placements "
                    "WHERE placements.image_asset_id = assets.id AND placements.enabled = 1)"
                )
            )
        ).scalar_one()
    )
    if mismatch_count:
        raise RuntimeError(
            "Cannot derive image inclusion from placements; "
            f"found {mismatch_count} inconsistent image assets"
        )


async def migrate_base_runtime_schema(engine: AsyncEngine) -> dict[str, list[str]]:
    """Remove structures that are not part of the current runtime model."""
    if engine.dialect.name != "sqlite":
        return {"dropped_tables": [], "rebuilt_tables": [], "reset_tables": []}

    register_models()
    dropped_tables: list[str] = []
    rebuilt_tables: list[str] = []
    reset_tables: list[str] = []
    async with engine.begin() as conn:
        if int((await conn.execute(text("PRAGMA foreign_keys"))).scalar_one()):
            raise RuntimeError(
                "Exact SQLite cleanup requires the migration connection; "
                "run scripts/migrate_runtime_schema.py"
            )
        existing_tables = await _runtime_table_names(conn)
        for table_name in OBSOLETE_RUNTIME_TABLES:
            if table_name not in existing_tables:
                continue
            await conn.execute(text(f'DROP TABLE "{table_name}"'))
            dropped_tables.append(table_name)

        if await table_exists(conn, "memoir_draft_blocks") and "legacy_draft_id" in (
            await table_columns(conn, "memoir_draft_blocks")
        ):
            current_block_count = int(
                (
                    await conn.execute(
                        text(
                            "SELECT COUNT(*) FROM memoir_draft_blocks "
                            "WHERE block_role != 'legacy_snapshot' "
                            "OR node_id IS NOT NULL OR legacy_draft_id IS NULL"
                        )
                    )
                ).scalar_one()
            )
            if current_block_count:
                raise RuntimeError(
                    "Cannot reset legacy DraftBlock schema; "
                    f"found {current_block_count} current node-based rows"
                )
            for table_name in LEGACY_DRAFT_BLOCK_TABLES:
                if not await table_exists(conn, table_name):
                    continue
                await conn.execute(text(f'DROP TABLE "{table_name}"'))
                reset_tables.append(table_name)

        if await table_exists(conn, "memoir_material_share_videos"):
            await _migrate_share_video_outputs(conn)
        if await table_exists(conn, "memoir_image_assets"):
            await _validate_image_inclusion_projection(conn)

        for table_name in EXACT_REBUILD_TABLES:
            if not await table_exists(conn, table_name):
                continue
            expected_columns = set(Base.metadata.tables[table_name].columns.keys())
            if await table_columns(conn, table_name) == expected_columns:
                continue
            await _rebuild_exact_table(conn, table_name)
            rebuilt_tables.append(table_name)

    return {
        "dropped_tables": dropped_tables,
        "rebuilt_tables": rebuilt_tables,
        "reset_tables": reset_tables,
    }


async def base_schema_mismatches(engine: AsyncEngine) -> dict[str, list[str]]:
    register_models()
    missing_tables: list[str] = []
    unexpected_tables: list[str] = []
    missing_columns: list[str] = []
    unexpected_columns: list[str] = []

    async with engine.begin() as conn:
        actual_tables = await _runtime_table_names(conn)
        expected_tables = set(Base.metadata.tables)
        unexpected_tables.extend(
            sorted(actual_tables - expected_tables - RUNTIME_MANAGED_TABLES)
        )
        for table_name, table in sorted(Base.metadata.tables.items()):
            if table_name not in actual_tables:
                missing_tables.append(table_name)
                continue
            columns = await table_columns(conn, table_name)
            expected_columns = set(table.columns.keys())
            missing_columns.extend(
                f"{table_name}.{column}"
                for column in sorted(expected_columns - columns)
            )
            unexpected_columns.extend(
                f"{table_name}.{column}"
                for column in sorted(columns - expected_columns)
            )

    return {
        "missing_tables": missing_tables,
        "unexpected_tables": unexpected_tables,
        "missing_columns": missing_columns,
        "unexpected_columns": unexpected_columns,
    }


async def assert_base_runtime_schema(engine: AsyncEngine) -> None:
    mismatches = await base_schema_mismatches(engine)
    details: list[str] = []
    for key, label in (
        ("missing_tables", "missing tables"),
        ("unexpected_tables", "unexpected tables"),
        ("missing_columns", "missing columns"),
        ("unexpected_columns", "unexpected columns"),
    ):
        values = mismatches[key]
        if values:
            details.append(f"{label}: {', '.join(values)}")
    if details:
        raise RuntimeError(
            "Runtime schema mismatch; "
            + "; ".join(details)
            + "; run `PYTHONPATH=backend python backend/scripts/migrate_runtime_schema.py` before starting the API"
        )
