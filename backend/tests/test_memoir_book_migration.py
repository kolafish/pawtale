import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.schema import CreateTable

from app.database import Base, register_models
from app.migrations.memoir_schema_cutover import (
    OBSOLETE_MEMOIR_TABLES,
    migrate_memoir_schema_cutover,
)
from app.migrations.runtime import assert_runtime_schema, migrate_runtime_schema


async def _table_columns(conn, table_name: str) -> set[str]:
    rows = (
        await conn.execute(text(f"PRAGMA table_info({table_name})"))
    ).fetchall()
    return {row[1] for row in rows}


async def _table_names(conn) -> set[str]:
    rows = (
        await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type = 'table'")
        )
    ).fetchall()
    return {row[0] for row in rows}


async def test_runtime_schema_assert_rejects_extra_columns_without_mutating():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        await migrate_runtime_schema(engine)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "ALTER TABLE memoir_book_plan_chapters "
                    "ADD COLUMN summary TEXT NOT NULL DEFAULT ''"
                )
            )

        with pytest.raises(RuntimeError, match="unexpected columns"):
            await assert_runtime_schema(
                engine,
                require_sqlite_foreign_keys=False,
            )

        async with engine.begin() as conn:
            assert "summary" in await _table_columns(
                conn,
                "memoir_book_plan_chapters",
            )
            output_columns = await _table_columns(
                conn,
                "memoir_book_outputs",
            )
        assert "formal_version_id" not in output_columns
        assert "formal_audit_job_id" not in output_columns
    finally:
        await engine.dispose()


async def test_runtime_schema_assert_rejects_unexpected_tables():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        await migrate_runtime_schema(engine)
        async with engine.begin() as conn:
            await conn.execute(
                text("CREATE TABLE obsolete_runtime_data (id TEXT)")
            )

        with pytest.raises(RuntimeError, match="unexpected tables"):
            await assert_runtime_schema(
                engine,
                require_sqlite_foreign_keys=False,
            )
        async with engine.begin() as conn:
            assert "obsolete_runtime_data" in await _table_names(conn)
    finally:
        await engine.dispose()


async def test_runtime_schema_assert_rejects_disabled_foreign_keys():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        await migrate_runtime_schema(engine)
        with pytest.raises(
            RuntimeError,
            match="foreign-key enforcement is disabled",
        ):
            await assert_runtime_schema(engine)
    finally:
        await engine.dispose()


async def test_runtime_schema_assert_reports_missing_columns():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        await migrate_runtime_schema(engine)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "ALTER TABLE memoir_book_output_versions "
                    "DROP COLUMN manuscript_json"
                )
            )

        with pytest.raises(
            RuntimeError,
            match="memoir_book_output_versions.manuscript_json",
        ):
            await assert_runtime_schema(engine)
    finally:
        await engine.dispose()


async def test_runtime_schema_migration_creates_book_completion_jobs():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        await migrate_runtime_schema(engine)
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE memoir_book_completion_jobs"))

        await migrate_runtime_schema(engine)

        async with engine.begin() as conn:
            assert "memoir_book_completion_jobs" in await _table_names(conn)
            columns = await _table_columns(conn, "memoir_book_completion_jobs")
        assert {
            "project_id",
            "active_key",
            "target_chapters_json",
            "chapter_job_ids_json",
            "enqueue_errors_json",
        } <= columns
        assert "chapter_results_json" not in columns
        await assert_runtime_schema(
            engine,
            require_sqlite_foreign_keys=False,
        )
    finally:
        await engine.dispose()


async def test_runtime_schema_includes_book_review_answer_application_job_type():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        current_sql = str(
            CreateTable(
                Base.metadata.tables["memoir_editing_jobs"]
            ).compile(dialect=engine.sync_engine.dialect)
        )
        old_sql = current_sql.replace(", 'apply_book_review_answers'", "")
        assert old_sql != current_sql
        async with engine.begin() as conn:
            await conn.execute(text(old_sql))

        migration = await migrate_runtime_schema(engine)
        async with engine.begin() as conn:
            table_sql = str(
                await conn.scalar(
                    text(
                        "SELECT sql FROM sqlite_master "
                        "WHERE type = 'table' "
                        "AND name = 'memoir_editing_jobs'"
                    )
                )
                or ""
            )
        assert "memoir_editing_jobs" in (
            migration["memoir_schema_cutover"]["rebuilt_tables"]
        )
        assert "apply_book_review_answers" in table_sql
        await assert_runtime_schema(
            engine,
            require_sqlite_foreign_keys=False,
        )
    finally:
        await engine.dispose()


async def test_cutover_removes_unused_book_completion_and_review_columns():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        await migrate_runtime_schema(engine)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "ALTER TABLE memoir_completion_issues "
                    "ADD COLUMN section_id VARCHAR(36)"
                )
            )
            await conn.execute(
                text(
                    "ALTER TABLE memoir_book_completion_jobs "
                    "ADD COLUMN scope VARCHAR(20) NOT NULL DEFAULT 'pending'"
                )
            )
            await conn.execute(
                text(
                    "ALTER TABLE memoir_book_completion_jobs "
                    "ADD COLUMN chapter_results_json "
                    "JSON NOT NULL DEFAULT '[]'"
                )
            )
            await conn.execute(
                text(
                    "ALTER TABLE memoir_book_revision_proposals "
                    "ADD COLUMN comparison_json JSON NOT NULL DEFAULT '{}'"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO projects ("
                    "id, type, title, metadata_json, created_at, updated_at"
                    ") VALUES ("
                    "'project-legacy-review', 'memoir', '旧项目', '{}', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            await conn.execute(text("PRAGMA ignore_check_constraints = ON"))
            await conn.execute(
                text(
                    "INSERT INTO memoir_completion_issues ("
                    "id, project_id, scope, section_id, chapter_id, issue_type, "
                    "channel, question, reason, priority, fingerprint, "
                    "related_material_ids_json, input_signature, status, "
                    "outcome, outcome_note, created_at, updated_at"
                    ") VALUES ("
                    "'legacy-section-issue', 'project-legacy-review', "
                    "'section', 'section-1', NULL, 'clarity', 'interview', "
                    "'旧问题', '', 'medium', :fingerprint, '[]', "
                    "'old-input', 'open', '', '', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"fingerprint": "a" * 64},
            )
            await conn.execute(text("PRAGMA ignore_check_constraints = OFF"))

        migration = await migrate_runtime_schema(engine)
        rebuilt = set(
            migration["memoir_schema_cutover"]["rebuilt_tables"]
        )
        assert {
            "memoir_completion_issues",
            "memoir_book_completion_jobs",
            "memoir_book_revision_proposals",
        } <= rebuilt
        assert migration["memoir_schema_cutover"]["discarded_rows"][
            "memoir_completion_issues"
        ] == 1
        async with engine.begin() as conn:
            assert "section_id" not in await _table_columns(
                conn,
                "memoir_completion_issues",
            )
            book_completion_columns = await _table_columns(
                conn,
                "memoir_book_completion_jobs",
            )
            assert "scope" not in book_completion_columns
            assert "chapter_results_json" not in book_completion_columns
            assert "comparison_json" not in await _table_columns(
                conn,
                "memoir_book_revision_proposals",
            )
        await assert_runtime_schema(
            engine,
            require_sqlite_foreign_keys=False,
        )
    finally:
        await engine.dispose()


async def test_cutover_drops_all_known_legacy_tables():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        await migrate_runtime_schema(engine)
        sample_tables = {
            "memoir_claims",
            "memoir_stories",
            "memoir_completion_items",
            "memoir_source_cards",
            "memoir_chapter_source_links",
        }
        assert sample_tables <= set(OBSOLETE_MEMOIR_TABLES)
        async with engine.begin() as conn:
            for table_name in sample_tables:
                await conn.execute(
                    text(f"CREATE TABLE {table_name} (id TEXT)")
                )
                await conn.execute(
                    text(f"INSERT INTO {table_name} (id) VALUES ('legacy')")
                )

        result = await migrate_memoir_schema_cutover(engine)

        async with engine.begin() as conn:
            names = await _table_names(conn)
        assert sample_tables.isdisjoint(names)
        assert sample_tables <= set(result["dropped_tables"])
        assert "memoir_voice_sessions" in names
        assert "memoir_section_materials" in names
    finally:
        await engine.dispose()


async def test_cutover_leaves_unknown_table_for_schema_assertion():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        await migrate_runtime_schema(engine)
        async with engine.begin() as conn:
            await conn.execute(
                text("CREATE TABLE unknown_memoir_table (id TEXT)")
            )
        await migrate_memoir_schema_cutover(engine)
        async with engine.begin() as conn:
            assert "unknown_memoir_table" in await _table_names(conn)
    finally:
        await engine.dispose()
