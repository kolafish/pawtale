import pytest
from sqlalchemy import select

from app.domain.memoir.types import (
    MemoirAutomationEventStatus,
    MemoirAutomationEventType,
    MemoirEditingJobStatus,
    MemoirEditingJobType,
)
from app.models.memoir import MemoirAutomationEvent, MemoirEditingJob
from app.services.memoir import automation_events
from app.services.memoir.book_quality_audit import schedule_project_quality_audit
from app.services.memoir.editing_job_handlers import register_memoir_editing_job_handlers
from app.services.memoir.editing_jobs import clear_editing_job_handlers, run_editing_job
from tests.memoir_helpers import (
    create_test_book_plan,
    create_test_chapter_draft,
    create_test_voice_transcript,
    ensure_mobile_memoir_project,
    memoir_source_ref,
)


@pytest.fixture(autouse=True)
def clear_handlers():
    clear_editing_job_handlers()
    yield
    clear_editing_job_handlers()


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
    assert data["display"] == {
        "title": "「院子里的童年」有新内容待写入",
        "summary": "有 1 条新讲述还没有补进正文。",
        "detail": "建议先补写本章，再继续补充人物、地点和细节。",
    }
    assert data["primary_action"] == {
        "type": "revise_chapter",
        "cta_text": "补写本章",
        "chapter_id": chapter.id,
        "utterance_count": 1,
    }
    assert data["primary_chapter"]["chapter_id"] == chapter.id
    assert data["primary_chapter"]["status"] == "needs_update"
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
    assert event.metadata_json["display"]["title"] == "「院子里的童年」有新内容待写入"
    assert event.metadata_json["primary_action"]["cta_text"] == "补写本章"
    assert event.metadata_json["primary_chapter"]["chapter_id"] == chapter.id
    assert event.metadata_json["event_signature"].startswith("book_quality_audit|needs_updates")


async def test_book_quality_audit_reuses_acknowledged_equivalent_event(client, test_db):
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

    first_response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-quality-audit/run",
        json={},
    )
    assert first_response.status_code == 200
    event_id = first_response.json()["event"]["id"]
    event = await test_db.get(MemoirAutomationEvent, event_id)
    assert event is not None
    legacy_metadata = dict(event.metadata_json)
    legacy_metadata.pop(automation_events.EVENT_SIGNATURE_KEY)
    event.metadata_json = legacy_metadata
    test_db.add(event)
    await test_db.commit()

    acknowledge_response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/automation-events/{event_id}/acknowledge"
    )
    assert acknowledge_response.status_code == 200
    await automation_events.record_automation_event(
        test_db,
        project_id=project_id,
        event_type=MemoirAutomationEventType.AUDIT_COMPLETED,
        status=MemoirAutomationEventStatus.COMPLETED,
        summary_text="当前有 1 个章节需要优先处理。",
        metadata_json=legacy_metadata,
    )
    await test_db.commit()

    duplicate_visible_response = await client.get(f"/api/mobile/memoir/projects/{project_id}/automation-events")
    assert duplicate_visible_response.status_code == 200
    assert duplicate_visible_response.json()["items"] == []

    second_response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-quality-audit/run",
        json={},
    )
    assert second_response.status_code == 200
    assert second_response.json()["event"]["id"] == event_id

    events = (
        await test_db.execute(
            select(MemoirAutomationEvent).where(
                MemoirAutomationEvent.project_id == project_id,
                MemoirAutomationEvent.event_type == MemoirAutomationEventType.AUDIT_COMPLETED.value,
            )
        )
    ).scalars().all()
    assert len(events) == 2
    acknowledged_events = [
        event for event in events if event.metadata_json.get(automation_events.ACKNOWLEDGED_BY_USER_KEY) is True
    ]
    assert [event.id for event in acknowledged_events] == [event_id]

    visible_response = await client.get(f"/api/mobile/memoir/projects/{project_id}/automation-events")
    assert visible_response.status_code == 200
    assert visible_response.json()["items"] == []


async def test_book_quality_audit_job_records_event(client, test_db):
    register_memoir_editing_job_handlers()
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

    job = await schedule_project_quality_audit(
        test_db,
        project_id=project_id,
        reason="test",
    )
    await test_db.commit()

    assert job.job_type == MemoirEditingJobType.QUALITY_AUDIT.value
    assert job.dedupe_key == f"quality_audit:{project_id}"
    assert await run_editing_job(test_db, job.id) is True
    await test_db.refresh(job)
    assert job.status == MemoirEditingJobStatus.SUCCEEDED.value
    assert job.result_json["overall_status"] == "needs_updates"
    assert job.result_json["reason"] == "test"

    event = await test_db.scalar(
        select(MemoirAutomationEvent).where(
            MemoirAutomationEvent.project_id == project_id,
            MemoirAutomationEvent.event_type == MemoirAutomationEventType.AUDIT_COMPLETED.value,
        )
    )
    assert event is not None
    assert event.summary_text == "当前有 1 个章节需要优先处理。"
    assert event.id == job.result_json["event_id"]


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
    assert data["display"]["title"] == "先整理成书结构"
    assert data["display"]["summary"] == "还没有确认章节规划。"
    assert data["primary_action"]["type"] == "generate_plan"
    assert data["primary_action"]["cta_text"] == "生成目录"
    assert data["primary_chapter"] is None
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
    assert data["display"]["title"] == "「空白章节」还缺讲述素材"
    assert data["primary_action"]["type"] == "continue_interview"
    assert data["primary_action"]["cta_text"] == "继续访谈"


async def test_book_quality_audit_display_covers_needs_drafts(client, test_db):
    created = await create_test_voice_transcript(client, test_db, "小时候在外婆家的院子里摘菜。")
    project_id = created["session"]["project_id"]
    _, chapter = await create_test_book_plan(
        test_db,
        project_id=project_id,
        source_refs=[memoir_source_ref(created, "外婆家的院子")],
        chapter_title="院子里的童年",
    )
    await test_db.commit()

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-quality-audit/run",
        json={"record_event": False},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["overall_status"] == "needs_drafts"
    assert data["display"]["title"] == "「院子里的童年」可以生成正文"
    assert data["display"]["summary"] == "本章已有 1 条素材，但还没有章节正文。"
    assert data["primary_action"]["type"] == "generate_chapter_draft"
    assert data["primary_action"]["cta_text"] == "生成本章"
    assert data["primary_chapter"]["chapter_id"] == chapter.id


async def test_book_quality_audit_display_covers_collecting(client, test_db):
    created = await create_test_voice_transcript(client, test_db, "小时候在外婆家的院子里摘菜。")
    project_id = created["session"]["project_id"]
    _, chapter = await create_test_book_plan(
        test_db,
        project_id=project_id,
        source_refs=[memoir_source_ref(created, "外婆家的院子")],
        chapter_title="院子里的童年",
    )
    await create_test_chapter_draft(
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
        content="小时候在外婆家的院子里摘菜。",
    )
    await test_db.commit()

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-quality-audit/run",
        json={"record_event": False},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["overall_status"] == "collecting"
    assert data["display"]["title"] == "「院子里的童年」还可以继续丰富"
    assert data["display"]["summary"] == "素材已有基础，还可以继续补充关键细节。"
    assert data["primary_action"]["type"] == "continue_interview"
    assert data["primary_action"]["cta_text"] == "继续追问"


async def test_book_quality_audit_display_covers_healthy(client, test_db):
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
    await create_test_chapter_draft(
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
        content="小时候在外婆家的院子里摘菜。" * 12,
    )
    await test_db.commit()

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-quality-audit/run",
        json={"record_event": False},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["overall_status"] == "healthy"
    assert data["display"]["title"] == "成书整理状态良好"
    assert data["display"]["summary"] == "当前章节素材和正文已有稳定基础。"
    assert data["primary_action"]["type"] == "review_book"
    assert data["primary_action"]["cta_text"] == "查看成书"
