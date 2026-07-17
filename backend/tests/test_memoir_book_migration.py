from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine

from app.database import create_database_engine, register_models, settings
from app.migrations.memoir_schema_cutover import OBSOLETE_MEMOIR_TABLES
from app.migrations.runtime import assert_runtime_schema, migrate_runtime_schema
from app.models.memoir import (
    MemoirAutomationEvent,
    MemoirBookOutput,
    MemoirBookPlan,
    MemoirEditingJob,
    MemoirVoiceSession,
)
from app.models.memoir_narrative import (
    MemoirNarrativeDecision,
    MemoirStory,
)
from app.models.projects import Project


async def _table_columns(conn, table_name: str) -> set[str]:
    rows = (await conn.execute(text(f"PRAGMA table_info({table_name})"))).fetchall()
    return {row[1] for row in rows}


async def test_runtime_schema_assert_rejects_extra_columns_without_mutating_schema():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        await migrate_runtime_schema(engine)
        async with engine.begin() as conn:
            await conn.execute(
                text("ALTER TABLE memoir_book_plan_chapters ADD COLUMN summary TEXT NOT NULL DEFAULT ''")
            )

        with pytest.raises(RuntimeError, match="unexpected columns") as exc_info:
            await assert_runtime_schema(engine, require_sqlite_foreign_keys=False)

        assert "memoir_book_plan_chapters.summary" in str(exc_info.value)

        async with engine.begin() as conn:
            columns = await _table_columns(conn, "memoir_book_plan_chapters")
            output_columns = await _table_columns(conn, "memoir_book_outputs")

        assert "summary" in columns
        assert {
            "formal_version_id",
            "formal_audit_job_id",
            "formalized_by_user_id",
            "formalized_at",
        }.issubset(output_columns)
    finally:
        await engine.dispose()


async def test_runtime_schema_assert_rejects_unexpected_tables_without_deleting_them():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        await migrate_runtime_schema(engine)
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE obsolete_runtime_data (id TEXT)"))

        with pytest.raises(RuntimeError, match="unexpected tables") as exc_info:
            await assert_runtime_schema(engine, require_sqlite_foreign_keys=False)

        assert "obsolete_runtime_data" in str(exc_info.value)
        async with engine.begin() as conn:
            table_exists = await conn.scalar(
                text(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'obsolete_runtime_data'"
                )
            )
        assert table_exists == 1
    finally:
        await engine.dispose()


async def test_runtime_schema_assert_rejects_disabled_sqlite_foreign_keys():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        await migrate_runtime_schema(engine)

        with pytest.raises(RuntimeError, match="foreign-key enforcement is disabled"):
            await assert_runtime_schema(engine)
    finally:
        await engine.dispose()


async def test_runtime_schema_assert_reports_missing_columns_without_mutating_schema():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        await migrate_runtime_schema(engine)
        async with engine.begin() as conn:
            await conn.execute(text("ALTER TABLE memoir_book_output_versions DROP COLUMN manuscript_json"))

        with pytest.raises(RuntimeError, match="memoir_book_output_versions.manuscript_json"):
            await assert_runtime_schema(engine)

        async with engine.begin() as conn:
            columns = await _table_columns(conn, "memoir_book_output_versions")

        assert "manuscript_json" not in columns
    finally:
        await engine.dispose()


async def test_destructive_memoir_cutover_drops_only_known_legacy_schema():
    register_models()
    engine = create_database_engine(
        settings.model_copy(
            update={
                "app_env": "test",
                "database_url": "sqlite+aiosqlite:///:memory:",
            }
        )
    )
    now = datetime(2026, 7, 17, 12, 0, 0)
    try:
        await migrate_runtime_schema(engine)
        async with AsyncSession(engine, expire_on_commit=False) as session:
            session.add(
                Project(
                    id="project-1",
                    type="memoir",
                    title="测试回忆录",
                    metadata_json={},
                )
            )
            await session.flush()
            session.add(
                MemoirStory(
                    id="story-1",
                    project_id="project-1",
                    title="保留的故事",
                    title_normalized="保留的故事",
                    story_type="event",
                    summary="",
                    time_precision="unknown",
                    lifecycle_status="active",
                    verification_status="unreviewed",
                    publication_status="eligible",
                    version=1,
                    created_by="system",
                    updated_by="system",
                    created_at=now,
                    updated_at=now,
                )
            )
            for job_id in ("job-current", "job-obsolete"):
                session.add(
                    MemoirEditingJob(
                        id=job_id,
                        project_id="project-1",
                        job_type="generate_chapter_draft",
                        status="pending",
                        run_after=now,
                        attempts=0,
                        payload_json={},
                        result_json={},
                        error_message="",
                        created_at=now,
                        updated_at=now,
                    )
                )
            await session.flush()
            session.add(
                MemoirBookPlan(
                    id="plan-1",
                    project_id="project-1",
                    title="章节规划",
                    status="draft",
                    created_by="system",
                    prompt_snapshot="",
                    metadata_json={},
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.flush()
            session.add(
                MemoirBookOutput(
                    id="output-1",
                    project_id="project-1",
                    plan_id="plan-1",
                    title="测试回忆录",
                    status="draft",
                    formal_audit_job_id="job-obsolete",
                    metadata_json={},
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                MemoirAutomationEvent(
                    id="event-1",
                    project_id="project-1",
                    editing_job_id="job-obsolete",
                    event_type="audit_completed",
                    status="completed",
                    utterance_ids_json=[],
                    summary_text="",
                    metadata_json={},
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                MemoirAutomationEvent(
                    id="event-obsolete",
                    project_id="project-1",
                    event_type="audit_completed",
                    status="completed",
                    utterance_ids_json=[],
                    summary_text="",
                    metadata_json={},
                    created_at=now,
                    updated_at=now,
                )
            )
            for decision_id in ("decision-current", "decision-obsolete"):
                session.add(
                    MemoirNarrativeDecision(
                        id=decision_id,
                        project_id="project-1",
                        target_type="story",
                        target_id="story-1",
                        action="confirm",
                        actor_type="system",
                        before_state_json={},
                        after_state_json={},
                        reason="",
                        idempotency_key=decision_id,
                        created_at=now,
                    )
                )
            session.add(
                MemoirVoiceSession(
                    id="session-1",
                    project_id="project-1",
                    status="created",
                    guidance_mode="free_narration",
                    interview_type="ai_interview",
                    session_goal="open_narration",
                    error_message="",
                    metadata_json={},
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.commit()

        async with engine.begin() as conn:
            assert await conn.scalar(text("PRAGMA foreign_keys")) == 1
            await conn.execute(
                text("DROP TRIGGER memoir_narrative_decisions_no_update")
            )
            await conn.execute(text("PRAGMA ignore_check_constraints = ON"))
            await conn.execute(
                text(
                    "UPDATE memoir_stories "
                    "SET lifecycle_status = 'merged' WHERE id = 'story-1'"
                )
            )
            await conn.execute(
                text(
                    "UPDATE memoir_editing_jobs "
                    "SET job_type = 'generate_draft_blocks' "
                    "WHERE id = 'job-obsolete'"
                )
            )
            await conn.execute(
                text(
                    "UPDATE memoir_narrative_decisions "
                    "SET target_type = 'gap', action = 'accept_incomplete' "
                    "WHERE id = 'decision-obsolete'"
                )
            )
            await conn.execute(
                text(
                    "UPDATE memoir_automation_events "
                    "SET event_type = 'followup_tasks_generated' "
                    "WHERE id = 'event-obsolete'"
                )
            )
            await conn.execute(text("PRAGMA ignore_check_constraints = OFF"))

            legacy_columns = {
                "memoir_stories": (
                    "completion_status TEXT",
                    "slot_schema_version TEXT",
                    "merged_into_story_id TEXT",
                ),
                "memoir_voice_sessions": (
                    "current_followup_task_id TEXT",
                ),
                "memoir_editing_jobs": (
                    "legacy_generate_draft_blocks TEXT",
                ),
                "memoir_automation_events": (
                    "followup_task_id TEXT",
                ),
                "memoir_narrative_decisions": (
                    "source_followup_task_answer_id TEXT",
                ),
                "memoir_book_chapter_draft_versions": (
                    "block_snapshot_json JSON",
                ),
            }
            for table_name, definitions in legacy_columns.items():
                for definition in definitions:
                    await conn.execute(
                        text(
                            f'ALTER TABLE "{table_name}" '
                            f"ADD COLUMN {definition}"
                        )
                    )
            for table_name in OBSOLETE_MEMOIR_TABLES:
                await conn.execute(
                    text(f'CREATE TABLE "{table_name}" (id TEXT PRIMARY KEY)')
                )

        result = await migrate_runtime_schema(engine)
        cutover = result["memoir_schema_cutover"]
        assert set(cutover["dropped_tables"]) == set(OBSOLETE_MEMOIR_TABLES)
        assert {
            "memoir_stories",
            "memoir_voice_sessions",
            "memoir_editing_jobs",
            "memoir_automation_events",
            "memoir_narrative_decisions",
            "memoir_book_chapter_draft_versions",
        }.issubset(cutover["rebuilt_tables"])
        assert cutover["discarded_rows"] == {
            "memoir_editing_jobs": 1,
            "memoir_automation_events": 1,
            "memoir_narrative_decisions": 1,
        }
        assert cutover["cleared_references"] == {
            "memoir_book_outputs.formal_audit_job_id": 1,
        }

        await assert_runtime_schema(engine)
        async with engine.begin() as conn:
            tables = set(
                (
                    await conn.execute(
                        text(
                            "SELECT name FROM sqlite_master "
                            "WHERE type = 'table'"
                        )
                    )
                ).scalars()
            )
            assert set(OBSOLETE_MEMOIR_TABLES).isdisjoint(tables)
            assert await conn.scalar(
                text(
                    "SELECT lifecycle_status FROM memoir_stories "
                    "WHERE id = 'story-1'"
                )
            ) == "archived"
            assert (
                await conn.execute(
                    text("SELECT id FROM memoir_editing_jobs ORDER BY id")
                )
            ).scalars().all() == ["job-current"]
            assert await conn.scalar(
                text(
                    "SELECT editing_job_id FROM memoir_automation_events "
                    "WHERE id = 'event-1'"
                )
            ) is None
            assert await conn.scalar(
                text(
                    "SELECT formal_audit_job_id FROM memoir_book_outputs "
                    "WHERE id = 'output-1'"
                )
            ) is None
            assert (
                await conn.execute(
                    text(
                        "SELECT id FROM memoir_narrative_decisions "
                        "ORDER BY id"
                    )
                )
            ).scalars().all() == ["decision-current"]
            assert await conn.scalar(
                text(
                    "SELECT COUNT(*) FROM memoir_voice_sessions "
                    "WHERE id = 'session-1'"
                )
            ) == 1

        repeated = await migrate_runtime_schema(engine)
        assert repeated["memoir_schema_cutover"] == {
            "dropped_tables": [],
            "dropped_triggers": [],
            "rebuilt_tables": [],
            "discarded_rows": {},
            "cleared_references": {},
        }
    finally:
        await engine.dispose()


async def test_destructive_memoir_cutover_keeps_unknown_tables_visible():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        await migrate_runtime_schema(engine)
        async with engine.begin() as conn:
            await conn.execute(
                text("CREATE TABLE unknown_product_data (id TEXT)")
            )

        with pytest.raises(RuntimeError, match="unknown_product_data"):
            await migrate_runtime_schema(engine)

        async with engine.begin() as conn:
            assert await conn.scalar(
                text(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'unknown_product_data'"
                )
            ) == 1
    finally:
        await engine.dispose()
