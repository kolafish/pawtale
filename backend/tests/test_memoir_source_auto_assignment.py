import json

from sqlalchemy import select

from app.domain.memoir.types import (
    MemoirAutomationEventType,
    MemoirEditingJobStatus,
    MemoirEditingJobType,
)
from app.models.memoir import MemoirAutomationEvent, MemoirEditingJob
from app.services.memoir.editing_job_handlers import register_memoir_editing_job_handlers
from app.services.memoir.editing_jobs import clear_editing_job_handlers, enqueue_editing_job, run_editing_job
from tests.memoir_helpers import (
    create_test_book_plan,
    create_test_chapter_draft,
    create_test_voice_transcript,
    memoir_source_ref,
)


class FakeSourceRouterAI:
    provider_name = "fake-ai"
    model_name = "fake-source-router"

    def __init__(self, *payloads: dict[str, object]):
        self.payloads = list(payloads)

    async def _call_llm(self, system_prompt: str, user_prompt: str, **kwargs):
        if not self.payloads:
            raise AssertionError("fake source router payload missing")
        return json.dumps(self.payloads.pop(0), ensure_ascii=False)


def _patch_source_router_ai(monkeypatch, *payloads: dict[str, object]) -> None:
    fake = FakeSourceRouterAI(*payloads)
    monkeypatch.setattr("app.services.memoir.source_router.get_ai_service", lambda ai_model=None: fake)


def _assign_route(chapter_id: str, *, confidence: float = 0.91) -> dict[str, object]:
    return {
        "material_type": "memoir_content",
        "action": "assign_to_chapter",
        "target_chapter_id": chapter_id,
        "suggested_title": None,
        "confidence": confidence,
        "reason": "讲述延续同一个章节主题",
        "evidence": [{"quote": "夏天一起摘菜和剥青豆", "reason": "与章节已有素材一致"}],
    }


def _new_chapter_route(title: str) -> dict[str, object]:
    return {
        "material_type": "memoir_content",
        "action": "suggest_new_chapter",
        "target_chapter_id": None,
        "suggested_title": title,
        "confidence": 0.88,
        "reason": "讲述集中在新的工作经历",
        "evidence": [{"quote": "在供销社负责盘点货物", "reason": "是明确的新人生阶段"}],
    }


def _ignore_route() -> dict[str, object]:
    return {
        "material_type": "ai_interaction",
        "action": "ignore",
        "target_chapter_id": None,
        "suggested_title": None,
        "confidence": 0.94,
        "reason": "这是和 AI 的对话，不是回忆素材",
        "evidence": [{"quote": "你能不能别问了", "reason": "用户在回应 AI 交互"}],
    }


def _register_source_routing_handler() -> None:
    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()


async def test_source_auto_assignment_assigns_high_confidence_source_and_records_progress(
    client,
    test_db,
    monkeypatch,
):
    first = await create_test_voice_transcript(
        client,
        test_db,
        "小时候在外婆家的院子里摘菜，夏天还会剥青豆。",
    )
    project_id = first["session"]["project_id"]
    _, chapter = await create_test_book_plan(
        test_db,
        project_id=project_id,
        source_ref=memoir_source_ref(first, "外婆家的院子"),
        chapter_title="院子里的童年",
    )
    await create_test_chapter_draft(
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
        content="小时候在外婆家的院子里摘菜。",
    )
    uncovered = await create_test_voice_transcript(
        client,
        test_db,
        "后来想起外婆家的院子，最记得夏天一起摘菜和剥青豆。",
    )
    _patch_source_router_ai(monkeypatch, _assign_route(chapter.id))

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-source-auto-assignments/run",
        json={"ai_model": "deepseek-v4-pro"},
    )

    assert response.status_code == 200, response.json()
    data = response.json()
    assert data["evaluated_source_count"] == 1
    assert data["assigned_source_count"] == 1
    assert data["skipped_source_count"] == 0
    assert data["items"][0]["assigned"] is True
    assert data["items"][0]["target_chapter_id"] == chapter.id
    assert data["events"][0]["event_type"] == "source_assigned"
    assert data["events"][0]["utterance_ids"] == [uncovered["utterance_id"]]

    plan_chapter = data["plan"]["chapters"][0]
    source_ids = [ref["utterance_id"] for ref in plan_chapter["source_refs"]]
    assert source_ids == [first["utterance_id"], uncovered["utterance_id"]]
    assert plan_chapter["pending_source_count"] == 1

    event = await test_db.scalar(
        select(MemoirAutomationEvent).where(
            MemoirAutomationEvent.project_id == project_id,
            MemoirAutomationEvent.event_type == MemoirAutomationEventType.SOURCE_ASSIGNED.value,
        )
    )
    assert event is not None
    assert event.chapter_id == chapter.id
    assert event.utterance_ids_json == [uncovered["utterance_id"]]
    assert event.metadata_json["source"] == "auto_assignment"
    assert event.metadata_json["confidence_threshold"] == 0.53

    job = await test_db.scalar(
        select(MemoirEditingJob).where(MemoirEditingJob.chapter_id == chapter.id)
    )
    assert job is not None
    assert job.job_type == MemoirEditingJobType.REVISE_CHAPTER.value
    assert job.status == MemoirEditingJobStatus.PENDING.value
    assert job.payload_json["pending_source_utterance_ids"] == [uncovered["utterance_id"]]
    assert job.payload_json["ai_model"] == "deepseek-v4-pro"


async def test_source_routing_job_assigns_high_confidence_source(client, test_db, monkeypatch):
    _register_source_routing_handler()
    first = await create_test_voice_transcript(
        client,
        test_db,
        "小时候在外婆家的院子里摘菜，夏天还会剥青豆。",
    )
    project_id = first["session"]["project_id"]
    _, chapter = await create_test_book_plan(
        test_db,
        project_id=project_id,
        source_ref=memoir_source_ref(first, "外婆家的院子"),
        chapter_title="院子里的童年",
    )
    await create_test_chapter_draft(
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
        content="小时候在外婆家的院子里摘菜。",
    )
    uncovered = await create_test_voice_transcript(
        client,
        test_db,
        "后来想起外婆家的院子，最记得夏天一起摘菜和剥青豆。",
    )
    _patch_source_router_ai(monkeypatch, _assign_route(chapter.id))
    job = await enqueue_editing_job(
        test_db,
        project_id=project_id,
        job_type=MemoirEditingJobType.ROUTE_SOURCES,
        payload_json={"utterance_ids": [uncovered["utterance_id"]], "ai_model": "deepseek-v4-pro"},
        dedupe_key=f"route_source:{uncovered['utterance_id']}",
    )
    await test_db.commit()

    assert await run_editing_job(test_db, job.id) is True
    await test_db.refresh(job)
    assert job.status == MemoirEditingJobStatus.SUCCEEDED.value
    assert job.result_json["assigned_source_count"] == 1

    event = await test_db.scalar(
        select(MemoirAutomationEvent).where(
            MemoirAutomationEvent.project_id == project_id,
            MemoirAutomationEvent.event_type == MemoirAutomationEventType.SOURCE_ASSIGNED.value,
        )
    )
    assert event is not None
    assert event.chapter_id == chapter.id
    assert event.utterance_ids_json == [uncovered["utterance_id"]]
    assert event.summary_text == "1 条讲述已自动整理进「院子里的童年」。"

    audit_job = await test_db.scalar(
        select(MemoirEditingJob).where(
            MemoirEditingJob.project_id == project_id,
            MemoirEditingJob.job_type == MemoirEditingJobType.QUALITY_AUDIT.value,
        )
    )
    assert audit_job is not None
    assert audit_job.status == MemoirEditingJobStatus.PENDING.value
    assert audit_job.dedupe_key == f"quality_audit:{project_id}"
    assert audit_job.payload_json["reason"] == "source_routing_completed"


async def test_source_auto_assignment_skips_new_topic_without_changing_plan(client, test_db, monkeypatch):
    first = await create_test_voice_transcript(
        client,
        test_db,
        "小时候在外婆家的院子里摘菜，夏天还会剥青豆。",
    )
    project_id = first["session"]["project_id"]
    await create_test_book_plan(
        test_db,
        project_id=project_id,
        source_ref=memoir_source_ref(first, "外婆家的院子"),
        chapter_title="院子里的童年",
    )
    uncovered = await create_test_voice_transcript(
        client,
        test_db,
        "参加工作以后，我在供销社负责盘点货物，第一次拿到奖金特别高兴。",
    )
    _patch_source_router_ai(monkeypatch, _new_chapter_route("供销社工作初记"))

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-source-auto-assignments/run",
        json={},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["evaluated_source_count"] == 1
    assert data["assigned_source_count"] == 0
    assert data["skipped_source_count"] == 1
    assert data["items"][0]["utterance_id"] == uncovered["utterance_id"]
    assert data["items"][0]["action"] == "suggest_new_chapter"
    assert data["items"][0]["assigned"] is False
    assert data["items"][0]["skip_reason"] == "not_existing_chapter_assignment"
    assert data["events"] == []
    assert [ref["utterance_id"] for ref in data["plan"]["chapters"][0]["source_refs"]] == [
        first["utterance_id"],
    ]


async def test_source_routing_job_holds_new_topic_for_review(client, test_db, monkeypatch):
    _register_source_routing_handler()
    first = await create_test_voice_transcript(
        client,
        test_db,
        "小时候在外婆家的院子里摘菜，夏天还会剥青豆。",
    )
    project_id = first["session"]["project_id"]
    await create_test_book_plan(
        test_db,
        project_id=project_id,
        source_ref=memoir_source_ref(first, "外婆家的院子"),
        chapter_title="院子里的童年",
    )
    uncovered = await create_test_voice_transcript(
        client,
        test_db,
        "参加工作以后，我在供销社负责盘点货物，第一次拿到奖金特别高兴。",
    )
    _patch_source_router_ai(monkeypatch, _new_chapter_route("供销社工作初记"))
    job = await enqueue_editing_job(
        test_db,
        project_id=project_id,
        job_type=MemoirEditingJobType.ROUTE_SOURCES,
        payload_json={"utterance_ids": [uncovered["utterance_id"]]},
        dedupe_key=f"route_source:{uncovered['utterance_id']}",
    )
    await test_db.commit()

    assert await run_editing_job(test_db, job.id) is True
    await test_db.refresh(job)
    assert job.status == MemoirEditingJobStatus.SUCCEEDED.value
    assert job.result_json["assigned_source_count"] == 0
    assert job.result_json["skipped_source_count"] == 1

    event = await test_db.scalar(
        select(MemoirAutomationEvent).where(
            MemoirAutomationEvent.project_id == project_id,
            MemoirAutomationEvent.event_type == MemoirAutomationEventType.SOURCE_HELD.value,
        )
    )
    assert event is not None
    assert event.status == "needs_review"
    assert event.utterance_ids_json == [uncovered["utterance_id"]]
    assert event.metadata_json["route"]["material_type"] == "memoir_content"
    assert event.metadata_json["route"]["action"] == "suggest_new_chapter"


async def test_source_routing_job_ignores_non_memoir_source_without_review_event(client, test_db, monkeypatch):
    _register_source_routing_handler()
    first = await create_test_voice_transcript(client, test_db, "小时候在老家院子里玩。")
    project_id = first["session"]["project_id"]
    await create_test_book_plan(
        test_db,
        project_id=project_id,
        source_ref=memoir_source_ref(first, "老家院子"),
        chapter_title="老家院子",
    )
    ai_interaction = await create_test_voice_transcript(client, test_db, "你能不能别问了")
    _patch_source_router_ai(monkeypatch, _ignore_route())
    job = await enqueue_editing_job(
        test_db,
        project_id=project_id,
        job_type=MemoirEditingJobType.ROUTE_SOURCES,
        payload_json={"utterance_ids": [ai_interaction["utterance_id"]]},
        dedupe_key=f"route_source:{ai_interaction['utterance_id']}",
    )
    await test_db.commit()

    assert await run_editing_job(test_db, job.id) is True
    await test_db.refresh(job)
    assert job.status == MemoirEditingJobStatus.SUCCEEDED.value
    assert job.result_json["assigned_source_count"] == 0
    assert job.result_json["skipped_source_count"] == 1

    event = await test_db.scalar(
        select(MemoirAutomationEvent).where(
            MemoirAutomationEvent.project_id == project_id,
            MemoirAutomationEvent.event_type == MemoirAutomationEventType.SOURCE_HELD.value,
        )
    )
    assert event is None
