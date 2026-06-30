from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine

import app.main as app_main
from app.database import Base, register_models
from app.domain.memoir.types import (
    MemoirBookPlanStatus,
    MemoirCreatedBy,
    MemoirGuidanceMode,
    MemoirSemanticCacheStatus,
)
from app.migrations.memoir_semantic import MEMOIR_SEMANTIC_TABLES, migrate_memoir_semantic_schema
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirBookQualityAuditSnapshot,
    MemoirChapterCard,
    MemoirChapterSourceLink,
    MemoirSourceCard,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.models.projects import Project
from app.services.memoir.semantic_state import (
    upsert_book_quality_audit_snapshot,
    upsert_chapter_card,
    upsert_source_card,
)


async def _table_columns(conn, table_name: str) -> set[str]:
    rows = (await conn.execute(text(f"PRAGMA table_info({table_name})"))).fetchall()
    return {row[1] for row in rows}


async def _index_names(conn, table_name: str) -> set[str]:
    rows = (await conn.execute(text(f"PRAGMA index_list({table_name})"))).fetchall()
    return {row[1] for row in rows}


async def test_memoir_semantic_migration_creates_missing_tables():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            for table_name in reversed(MEMOIR_SEMANTIC_TABLES):
                await conn.execute(text(f"DROP TABLE {table_name}"))

        result = await migrate_memoir_semantic_schema(engine)
        second_result = await migrate_memoir_semantic_schema(engine)

        assert result["changed_tables"] == list(MEMOIR_SEMANTIC_TABLES)
        assert second_result["changed_tables"] == []
        async with engine.begin() as conn:
            assert "source_text_hash" in await _table_columns(conn, "memoir_source_cards")
            assert "input_signature" in await _table_columns(conn, "memoir_chapter_cards")
            assert "chapter_audits_json" in await _table_columns(conn, "memoir_book_quality_audit_snapshots")
            assert "assignment_reason" in await _table_columns(conn, "memoir_chapter_source_links")
    finally:
        await engine.dispose()


async def test_auto_migrate_creates_memoir_semantic_tables(monkeypatch):
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            for table_name in reversed(MEMOIR_SEMANTIC_TABLES):
                await conn.execute(text(f"DROP TABLE {table_name}"))

        monkeypatch.setattr(app_main, "engine", engine)

        await app_main._auto_migrate()
        await app_main._auto_migrate()

        async with engine.begin() as conn:
            for table_name in MEMOIR_SEMANTIC_TABLES:
                assert await _table_columns(conn, table_name)
    finally:
        await engine.dispose()


async def test_memoir_semantic_tables_define_constraints_and_indexes():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            source_sql = (
                await conn.execute(
                    text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'memoir_source_cards'")
                )
            ).scalar_one()
            chapter_sql = (
                await conn.execute(
                    text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'memoir_chapter_cards'")
                )
            ).scalar_one()
            snapshot_sql = (
                await conn.execute(
                    text(
                        "SELECT sql FROM sqlite_master "
                        "WHERE type = 'table' AND name = 'memoir_book_quality_audit_snapshots'"
                    )
                )
            ).scalar_one()
            source_link_sql = (
                await conn.execute(
                    text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'memoir_chapter_source_links'")
                )
            ).scalar_one()

            assert "uq_memoir_source_cards_signature" in source_sql
            assert "ck_memoir_source_cards_status" in source_sql
            assert "uq_memoir_chapter_cards_signature" in chapter_sql
            assert "ck_memoir_chapter_cards_status" in chapter_sql
            assert "uq_memoir_book_quality_audit_snapshots_signature" in snapshot_sql
            assert "uq_memoir_chapter_source_links_chapter_utterance" in source_link_sql
            assert "ck_memoir_chapter_source_links_status" in source_link_sql
            assert "ck_memoir_chapter_source_links_assigned_by" in source_link_sql

            source_indexes = await _index_names(conn, "memoir_source_cards")
            chapter_indexes = await _index_names(conn, "memoir_chapter_cards")
            snapshot_indexes = await _index_names(conn, "memoir_book_quality_audit_snapshots")
            source_link_indexes = await _index_names(conn, "memoir_chapter_source_links")

            assert "ix_memoir_source_cards_project_id" in source_indexes
            assert "ix_memoir_source_cards_utterance_id" in source_indexes
            assert "ix_memoir_chapter_cards_project_id" in chapter_indexes
            assert "ix_memoir_chapter_cards_chapter_id" in chapter_indexes
            assert "ix_memoir_book_quality_audit_snapshots_project_id" in snapshot_indexes
            assert "ix_memoir_book_quality_audit_snapshots_input_signature" in snapshot_indexes
            assert "ix_memoir_chapter_source_links_project_id" in source_link_indexes
            assert "ix_memoir_chapter_source_links_chapter_id" in source_link_indexes
            assert "ix_memoir_chapter_source_links_utterance_id" in source_link_indexes
    finally:
        await engine.dispose()


async def test_semantic_state_upserts_reuse_signature_rows(test_db):
    project = Project(title="回忆录项目", type="memoir")
    test_db.add(project)
    await test_db.flush()

    voice_session = MemoirVoiceSession(
        project_id=project.id,
        status="created",
        guidance_mode=MemoirGuidanceMode.FREE_NARRATION.value,
    )
    test_db.add(voice_session)
    await test_db.flush()

    utterance = MemoirVoiceUtterance(
        voice_session_id=voice_session.id,
        transcript_raw="小时候常在外婆家厨房帮忙。",
        transcript_edited="小时候常在外婆家厨房帮忙。",
    )
    test_db.add(utterance)

    plan = MemoirBookPlan(
        project_id=project.id,
        title="童年",
        status=MemoirBookPlanStatus.CONFIRMED.value,
    )
    test_db.add(plan)
    await test_db.flush()

    chapter = MemoirBookPlanChapter(
        project_id=project.id,
        plan_id=plan.id,
        title="外婆家的厨房",
        sort_order=1,
    )
    test_db.add(chapter)
    await test_db.flush()

    draft = MemoirBookChapterDraft(project_id=project.id, chapter_id=chapter.id)
    test_db.add(draft)
    await test_db.flush()

    draft_version = MemoirBookChapterDraftVersion(
        project_id=project.id,
        chapter_id=chapter.id,
        draft_id=draft.id,
        version=1,
        content="我记得外婆家的厨房。",
        created_by=MemoirCreatedBy.AI.value,
    )
    test_db.add(draft_version)
    await test_db.flush()

    source_card = await upsert_source_card(
        test_db,
        project_id=project.id,
        utterance_id=utterance.id,
        source_text_hash="hash-1",
        engine="rules_v1",
        card_json={"summary": "厨房回忆"},
    )
    source_card_again = await upsert_source_card(
        test_db,
        project_id=project.id,
        utterance_id=utterance.id,
        source_text_hash="hash-1",
        engine="rules_v1",
        status=MemoirSemanticCacheStatus.STALE,
        card_json={"summary": "更新后的厨房回忆"},
    )

    chapter_card = await upsert_chapter_card(
        test_db,
        project_id=project.id,
        plan_id=plan.id,
        chapter_id=chapter.id,
        draft_version_id=draft_version.id,
        input_signature="chapter-signature-1",
        engine="rules_v1",
        card_json={"summary": "章节摘要"},
    )
    chapter_card_again = await upsert_chapter_card(
        test_db,
        project_id=project.id,
        plan_id=plan.id,
        chapter_id=chapter.id,
        draft_version_id=draft_version.id,
        input_signature="chapter-signature-1",
        engine="rules_v1",
        card_json={"summary": "更新后的章节摘要"},
    )

    snapshot = await upsert_book_quality_audit_snapshot(
        test_db,
        project_id=project.id,
        engine="rules_v1",
        input_signature="audit-signature-1",
        overall_status="needs_updates",
        summary_text="有新素材待补写。",
        source_card_ids_json=[source_card.id],
        chapter_card_ids_json=[chapter_card.id],
    )
    snapshot_again = await upsert_book_quality_audit_snapshot(
        test_db,
        project_id=project.id,
        engine="rules_v1",
        input_signature="audit-signature-1",
        overall_status="healthy",
        summary_text="当前状态良好。",
        source_card_ids_json=[source_card.id],
        chapter_card_ids_json=[chapter_card.id],
    )
    await test_db.commit()

    assert source_card_again.id == source_card.id
    assert source_card_again.status == MemoirSemanticCacheStatus.STALE.value
    assert source_card_again.card_json["summary"] == "更新后的厨房回忆"
    assert chapter_card_again.id == chapter_card.id
    assert chapter_card_again.card_json["summary"] == "更新后的章节摘要"
    assert snapshot_again.id == snapshot.id
    assert snapshot_again.overall_status == "healthy"

    assert await test_db.scalar(select(func.count()).select_from(MemoirSourceCard)) == 1
    assert await test_db.scalar(select(func.count()).select_from(MemoirChapterCard)) == 1
    assert await test_db.scalar(select(func.count()).select_from(MemoirBookQualityAuditSnapshot)) == 1
    assert await test_db.scalar(select(func.count()).select_from(MemoirChapterSourceLink)) == 0
