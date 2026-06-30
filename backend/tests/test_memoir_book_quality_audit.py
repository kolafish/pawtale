import json

import pytest
from sqlalchemy import select

from app.domain.memoir.types import (
    MemoirAutomationEventStatus,
    MemoirAutomationEventType,
    MemoirEditingJobStatus,
    MemoirEditingJobType,
)
from app.models.memoir import MemoirAutomationEvent, MemoirBookQualityAuditSnapshot, MemoirEditingJob
from app.schemas.memoir_book import MemoirBookQualityAuditRunRequest
from app.services.memoir import automation_events
from app.services.memoir.book_quality_audit import run_book_quality_audit, schedule_project_quality_audit
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


class FakeAuditAI:
    provider_name = "fake"
    model_name = "fake-audit"

    def __init__(self, responses: list[dict] | None = None, *, error: Exception | None = None):
        self.responses = list(responses or [])
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def _call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        timeout: float = 60.0,
        return_raw: bool = False,
    ) -> str:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "timeout": timeout,
            }
        )
        if self.error:
            raise self.error
        payload = self.responses.pop(0) if self.responses else {}
        return json.dumps(payload, ensure_ascii=False)


async def _create_needs_update_audit_context(client, test_db):
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
    user = (await client.get("/api/auth/me")).json()
    return user["id"], project_id, chapter, second


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
    assert event.metadata_json["snapshot_engine"] == "rules_v1"
    assert event.metadata_json["snapshot_id"]
    assert event.metadata_json["overall_status"] == "needs_updates"
    assert "chapter_items" not in event.metadata_json
    assert event.metadata_json["display"]["title"] == "「院子里的童年」有新内容待写入"
    assert event.metadata_json["primary_action"]["cta_text"] == "补写本章"
    assert event.metadata_json["primary_chapter"]["chapter_id"] == chapter.id
    assert event.metadata_json["event_signature"].startswith("book_quality_audit|needs_updates")


async def test_book_quality_audit_writes_semantic_snapshot_when_requested(client, test_db):
    user_id, project_id, chapter, second = await _create_needs_update_audit_context(client, test_db)
    chapter_ai = FakeAuditAI([
        {
            "summary": "本章围绕院子里的童年记忆。",
            "covered": {"time": ["小时候"], "places": ["外婆家的院子"], "people": ["外婆"], "events": ["摘菜"]},
            "gaps": [{"type": "event_sequence", "severity": "medium", "message": "剥青豆的前后经过还不连贯"}],
            "next_questions": ["那次剥青豆是怎么开始的？"],
            "evidence_refs": [{"utterance_id": second["utterance_id"], "quote": "剥青豆"}],
        }
    ])
    semantic_ai = FakeAuditAI([
        {
            "overall_status": "needs_updates",
            "summary_text": "语义审计认为「院子里的童年」需要补写新素材。",
            "display": {
                "title": "语义审计：院子里的童年待补写",
                "summary": "新讲述已经出现，但正文还没吸收。",
                "detail": "先补写剥青豆的经过，再继续追问细节。",
            },
            "primary_action": {
                "type": "revise_chapter",
                "cta_text": "补写本章",
                "chapter_id": chapter.id,
                "utterance_count": 1,
            },
            "chapter_audits": [
                {
                    "chapter_id": chapter.id,
                    "summary": "新增剥青豆素材待写入。",
                    "gaps": [
                        {"type": "event_sequence", "severity": "medium", "message": "剥青豆事件缺少起因和经过"}
                    ],
                    "next_questions": ["那天为什么会一起剥青豆？"],
                    "evidence_refs": [{"utterance_id": second["utterance_id"], "quote": "剥青豆"}],
                }
            ],
        }
    ])

    response = await run_book_quality_audit(
        test_db,
        user_id=user_id,
        project_id=project_id,
        req=MemoirBookQualityAuditRunRequest(record_event=True, semantic_audit=True),
        chapter_card_ai_client=chapter_ai,
        semantic_ai_client=semantic_ai,
    )

    assert response.summary_text == "语义审计认为「院子里的童年」需要补写新素材。"
    assert response.display.title == "语义审计：院子里的童年待补写"
    assert len(chapter_ai.calls) == 1
    assert len(semantic_ai.calls) == 1

    snapshots = (
        await test_db.execute(
            select(MemoirBookQualityAuditSnapshot).where(
                MemoirBookQualityAuditSnapshot.project_id == project_id,
            )
        )
    ).scalars().all()
    snapshots_by_engine = {snapshot.engine: snapshot for snapshot in snapshots}
    assert set(snapshots_by_engine) == {"rules_v1", "llm_semantic_v1"}
    semantic_snapshot = snapshots_by_engine["llm_semantic_v1"]
    assert semantic_snapshot.chapter_audits_json[0]["gaps"][0]["message"] == "剥青豆事件缺少起因和经过"
    assert semantic_snapshot.chapter_card_ids_json

    event = await test_db.scalar(
        select(MemoirAutomationEvent).where(
            MemoirAutomationEvent.project_id == project_id,
            MemoirAutomationEvent.event_type == MemoirAutomationEventType.AUDIT_COMPLETED.value,
        )
    )
    assert event is not None
    assert event.metadata_json["snapshot_id"] == semantic_snapshot.id
    assert event.metadata_json["snapshot_engine"] == "llm_semantic_v1"
    assert "chapter_items" not in event.metadata_json


async def test_book_quality_audit_reuses_semantic_snapshot_for_same_input(client, test_db):
    user_id, project_id, chapter, second = await _create_needs_update_audit_context(client, test_db)
    chapter_ai = FakeAuditAI([
        {
            "summary": "本章围绕院子里的童年记忆。",
            "covered": {"time": ["小时候"], "places": ["外婆家的院子"], "people": ["外婆"], "events": ["摘菜"]},
            "gaps": [{"type": "event_sequence", "severity": "medium", "message": "剥青豆的前后经过还不连贯"}],
            "next_questions": ["那次剥青豆是怎么开始的？"],
            "evidence_refs": [{"utterance_id": second["utterance_id"], "quote": "剥青豆"}],
        }
    ])
    semantic_ai = FakeAuditAI([
        {
            "overall_status": "needs_updates",
            "summary_text": "语义审计认为需要补写。",
            "display": {"title": "语义审计待补写", "summary": "需要补写。", "detail": ""},
            "primary_action": {"type": "revise_chapter", "cta_text": "补写本章", "chapter_id": chapter.id},
            "chapter_audits": [
                {
                    "chapter_id": chapter.id,
                    "summary": "新增素材待写入。",
                    "gaps": [{"type": "event_sequence", "severity": "medium", "message": "还缺经过"}],
                    "next_questions": ["那天为什么会一起剥青豆？"],
                    "evidence_refs": [{"utterance_id": second["utterance_id"], "quote": "剥青豆"}],
                }
            ],
        }
    ])

    first = await run_book_quality_audit(
        test_db,
        user_id=user_id,
        project_id=project_id,
        req=MemoirBookQualityAuditRunRequest(record_event=False, semantic_audit=True),
        chapter_card_ai_client=chapter_ai,
        semantic_ai_client=semantic_ai,
    )
    second_response = await run_book_quality_audit(
        test_db,
        user_id=user_id,
        project_id=project_id,
        req=MemoirBookQualityAuditRunRequest(record_event=False, semantic_audit=True),
        chapter_card_ai_client=chapter_ai,
        semantic_ai_client=semantic_ai,
    )

    assert first.summary_text == second_response.summary_text
    assert len(chapter_ai.calls) == 1
    assert len(semantic_ai.calls) == 1
    semantic_count = await test_db.scalar(
        select(MemoirBookQualityAuditSnapshot).where(
            MemoirBookQualityAuditSnapshot.project_id == project_id,
            MemoirBookQualityAuditSnapshot.engine == "llm_semantic_v1",
            MemoirBookQualityAuditSnapshot.error_message == "",
        )
    )
    assert semantic_count is not None


async def test_book_quality_audit_falls_back_to_rules_snapshot_when_semantic_fails(client, test_db):
    user_id, project_id, chapter, second = await _create_needs_update_audit_context(client, test_db)
    chapter_ai = FakeAuditAI([
        {
            "summary": "本章围绕院子里的童年记忆。",
            "covered": {"time": ["小时候"], "places": ["外婆家的院子"], "people": ["外婆"], "events": ["摘菜"]},
            "gaps": [{"type": "event_sequence", "severity": "medium", "message": "剥青豆的前后经过还不连贯"}],
            "next_questions": ["那次剥青豆是怎么开始的？"],
            "evidence_refs": [{"utterance_id": second["utterance_id"], "quote": "剥青豆"}],
        }
    ])

    response = await run_book_quality_audit(
        test_db,
        user_id=user_id,
        project_id=project_id,
        req=MemoirBookQualityAuditRunRequest(record_event=True, semantic_audit=True),
        chapter_card_ai_client=chapter_ai,
        semantic_ai_client=FakeAuditAI(error=RuntimeError("semantic provider down")),
    )

    assert response.summary_text == "当前有 1 个章节需要优先处理。"
    event = await test_db.scalar(
        select(MemoirAutomationEvent).where(
            MemoirAutomationEvent.project_id == project_id,
            MemoirAutomationEvent.event_type == MemoirAutomationEventType.AUDIT_COMPLETED.value,
        )
    )
    assert event is not None
    assert event.metadata_json["snapshot_engine"] == "rules_v1"
    failed_snapshot = await test_db.scalar(
        select(MemoirBookQualityAuditSnapshot).where(
            MemoirBookQualityAuditSnapshot.project_id == project_id,
            MemoirBookQualityAuditSnapshot.engine == "llm_semantic_v1",
            MemoirBookQualityAuditSnapshot.error_message != "",
        )
    )
    assert failed_snapshot is not None
    assert "semantic provider down" in failed_snapshot.error_message


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
    first_data = first_response.json()
    event_id = first_data["event"]["id"]
    event = await test_db.get(MemoirAutomationEvent, event_id)
    assert event is not None
    legacy_metadata = {
        "source": "book_quality_audit",
        "overall_status": first_data["overall_status"],
        "pending_utterance_count": 0,
        "next_actions": first_data["next_actions"],
        "chapter_items": first_data["chapter_items"],
        "display": first_data["display"],
        "primary_action": first_data["primary_action"],
        "primary_chapter": first_data["primary_chapter"],
    }
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
