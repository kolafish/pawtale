from sqlalchemy import select

from app.domain.memoir.types import MemoirAutomationEventType
from app.models.memoir import MemoirAutomationEvent
from tests.memoir_helpers import (
    create_test_book_plan,
    create_test_chapter_draft,
    create_test_voice_transcript,
    ensure_mobile_memoir_project,
    memoir_source_ref,
)


async def test_book_quality_audit_flags_pending_chapter_update_and_records_event(client, test_db):
    first = await create_test_voice_transcript(client, test_db, "小时候在外婆家的院子里摘菜。")
    second = await create_test_voice_transcript(client, test_db, "后来又讲了夏天和外婆一起剥青豆。")
    project_id = first["session"]["project_id"]
    _, chapter = await create_test_book_plan(
        test_db,
        project_id=project_id,
        source_refs=[
            memoir_source_ref(first, "外婆家的院子"),
            memoir_source_ref(second, "剥青豆"),
        ],
        chapter_title="院子里的童年",
    )
    chapter.metadata_json = {"pending_source_utterance_ids": [second["utterance_id"]]}
    await create_test_chapter_draft(
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
        content="小时候在外婆家的院子里摘菜。",
    )
    await test_db.commit()

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-quality-audit/run",
        json={},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["overall_status"] == "needs_updates"
    assert data["summary_text"] == "当前有 1 个章节需要优先处理。"
    assert data["chapter_items"][0]["status"] == "needs_update"
    assert data["chapter_items"][0]["attention"] == "high"
    assert "稍后会补进正文" in data["chapter_items"][0]["interview_hint"]
    assert data["event"]["event_type"] == "audit_completed"

    event = await test_db.scalar(
        select(MemoirAutomationEvent).where(
            MemoirAutomationEvent.project_id == project_id,
            MemoirAutomationEvent.event_type == MemoirAutomationEventType.AUDIT_COMPLETED.value,
        )
    )
    assert event is not None
    assert event.summary_text == "当前有 1 个章节需要优先处理。"
    assert event.metadata_json["overall_status"] == "needs_updates"
    assert event.metadata_json["chapter_items"][0]["chapter_id"] == chapter.id
    assert event.metadata_json["chapter_items"][0]["status"] == "needs_update"


async def test_book_quality_audit_returns_needs_plan_without_recording_event(client, test_db):
    created = await create_test_voice_transcript(client, test_db, "我想先随便讲讲小时候的事。")
    project_id = created["session"]["project_id"]

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-quality-audit/run",
        json={"record_event": False},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["overall_status"] == "needs_plan"
    assert data["summary_text"] == "当前还没有确认章节规划，需要先整理出成书结构。"
    assert data["next_actions"] == [
        "先根据已有讲述生成并确认章节规划",
        "整理 1 条尚未归章的讲述记录",
    ]
    assert data["chapter_items"] == []
    assert data["event"] is None

    event_count = await test_db.scalar(
        select(MemoirAutomationEvent).where(
            MemoirAutomationEvent.project_id == project_id,
            MemoirAutomationEvent.event_type == MemoirAutomationEventType.AUDIT_COMPLETED.value,
        )
    )
    assert event_count is None


async def test_book_quality_audit_marks_empty_chapter_as_needing_sources(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    await create_test_book_plan(
        test_db,
        project_id=project_id,
        source_ref=None,
        chapter_title="空白章节",
    )

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-quality-audit/run",
        json={"record_event": False},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["overall_status"] == "needs_sources"
    assert data["chapter_items"][0]["status"] == "needs_sources"
    assert data["chapter_items"][0]["attention"] == "high"
