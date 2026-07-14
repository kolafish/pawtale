import json
from hashlib import sha256

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.domain.memoir.narrative_types import (
    MemoirClaimType,
    MemoirNarrativeActorType,
    MemoirNarrativeEntityType,
)
from app.domain.memoir.types import (
    MemoirAutomationEventStatus,
    MemoirAutomationEventType,
    MemoirChapterSourceAssignedBy,
    MemoirChapterSourceLinkStatus,
    MemoirCreatedBy,
    MemoirEditingJobStatus,
    MemoirEditingJobType,
    MemoirTranscriptVersionType,
)
from app.models.memoir import (
    MemoirAutomationEvent,
    MemoirChapterSourceLink,
    MemoirEditingJob,
    MemoirTranscriptVersion,
    MemoirVoiceUtterance,
)
from app.models.memoir_narrative import (
    MemoirClaim,
    MemoirClaimEvidence,
    MemoirFollowupTaskClaim,
    MemoirFollowupTask,
    MemoirFollowupTaskUtterance,
    MemoirNarrativeDecision,
    MemoirNarrativeEntity,
    MemoirNarrativeGap,
    MemoirNarrativeGapClaim,
    MemoirStory,
)
from app.repositories.memoir_narrative import (
    MemoirClaimRepository,
    MemoirNarrativeEntityRepository,
)
from app.schemas.memoir_narrative import MemoirClaimCreate, MemoirNarrativeEntityCreate
from app.services.memoir.editing_job_handlers import register_memoir_editing_job_handlers
from app.services.memoir.editing_jobs import clear_editing_job_handlers, enqueue_editing_job
from app.services.memoir.source_auto_assignment import run_source_auto_assignment_for_utterances
from app.services.memoir.utterance_editing_pipeline import (
    schedule_source_routing_for_ready_utterance,
    source_text_hash,
)
from tests.memoir_helpers import (
    create_test_book_plan,
    create_test_chapter_draft,
    create_test_voice_transcript,
    memoir_source_ref,
    run_test_editing_job,
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


class FakeSourceCardAI:
    provider_name = "fake-ai"
    model_name = "fake-source-card"

    async def _call_llm(self, system_prompt: str, user_prompt: str, **kwargs):
        return json.dumps(
            {
                "short_title": "外婆家院子",
                "summary": "回忆外婆家院子里夏天摘菜和剥青豆。",
                "people": ["外婆", "我"],
                "places": ["外婆家院子"],
                "time_hints": ["夏天"],
                "event_tags": ["摘菜", "剥青豆"],
                "emotion_tags": ["怀念"],
                "key_facts": [
                    {"text": "夏天一起摘菜和剥青豆", "evidence_quote": "夏天一起摘菜和剥青豆"}
                ],
                "open_questions": ["那次剥青豆是怎么开始的？"],
            },
            ensure_ascii=False,
        )


class FakeFactResolutionAI:
    provider_name = "fake-ai"
    model_name = "fake-fact-resolution"

    def __init__(self, payload: dict[str, object]):
        self.payload = payload
        self.calls: list[dict[str, object]] = []

    async def _call_llm(self, system_prompt: str, user_prompt: str, **kwargs):
        self.calls.append({"system_prompt": system_prompt, "user_prompt": user_prompt, **kwargs})
        return json.dumps(self.payload, ensure_ascii=False)


def _patch_source_router_ai(monkeypatch, *payloads: dict[str, object]) -> None:
    fake = FakeSourceRouterAI(*payloads)
    monkeypatch.setattr("app.services.memoir.source_cards.get_ai_service", lambda ai_model=None: FakeSourceCardAI())
    monkeypatch.setattr("app.services.memoir.source_router.get_ai_service", lambda ai_model=None: fake)


def _patch_fact_resolution_ai(
    monkeypatch,
    payload: dict[str, object],
) -> FakeFactResolutionAI:
    fake = FakeFactResolutionAI(payload)
    monkeypatch.setattr(
        "app.services.memoir.narrative.fact_resolution.get_ai_service",
        lambda ai_model=None: fake,
    )
    return fake


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


def _completion_route(
    *,
    answer_kind: str | None,
    direct_narrative_text: str = "",
    clarification_text: str = "",
    fact_resolution_text: str = "",
    confirmation_question: str = "",
) -> dict[str, object]:
    status = "needs_confirmation" if answer_kind is None else "resolved"
    return {
        "material_type": "memoir_content",
        "action": "hold_for_review" if answer_kind is None else "suggest_new_chapter",
        "target_chapter_id": None,
        "suggested_title": None,
        "confidence": 0.88,
        "reason": "这是对当前补全问题的回答",
        "evidence": [
            {
                "quote": (
                    direct_narrative_text
                    or clarification_text
                    or fact_resolution_text
                    or "我不确定"
                ),
                "reason": "来自回答原文",
            }
        ],
        "followup_answer": {
            "status": status,
            "answer_kind": answer_kind,
            "direct_narrative_text": direct_narrative_text,
            "clarification_text": clarification_text,
            "fact_resolution_text": fact_resolution_text,
            "confirmation_question": confirmation_question,
            "reason": "按回答在章节中的实际用途分类",
        },
    }


def _register_source_routing_handler() -> None:
    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()


async def _load_utterance_for_routing(test_db, utterance_id: str) -> MemoirVoiceUtterance:
    utterance = await test_db.scalar(
        select(MemoirVoiceUtterance)
        .where(MemoirVoiceUtterance.id == utterance_id)
        .options(selectinload(MemoirVoiceUtterance.chat_message))
    )
    assert utterance is not None
    return utterance


async def _schedule_guarded_route_job(
    test_db,
    *,
    project_id: str,
    utterance_id: str,
    ai_model: str | None = None,
) -> MemoirEditingJob:
    job = await schedule_source_routing_for_ready_utterance(
        test_db,
        project_id=project_id,
        utterance=await _load_utterance_for_routing(test_db, utterance_id),
        source="test",
        ai_model=ai_model,
    )
    assert job is not None
    return job


async def _create_fact_resolution_case(client, test_db):
    first = await create_test_voice_transcript(
        client,
        test_db,
        "我工作时有一位经理，后来也有人邀请我加入创业公司。",
    )
    project_id = first["session"]["project_id"]
    _, chapter = await create_test_book_plan(
        test_db,
        project_id=project_id,
        source_ref=memoir_source_ref(first, "工作时的经理"),
        chapter_title="工作这些年",
    )
    answer_text = (
        "他们俩这都是一个人呀。首先他是我工作时候的经理，后来我们都离开"
        "那家公司了，所以他创业的时候也邀请我加入了，都是他。"
    )
    answer = await create_test_voice_transcript(client, test_db, answer_text)

    entity_repository = MemoirNarrativeEntityRepository(test_db)
    retained = await entity_repository.create(
        MemoirNarrativeEntityCreate(
            project_id=project_id,
            entity_type=MemoirNarrativeEntityType.PERSON,
            canonical_name="工作时的经理",
            aliases=["经理"],
            created_by=MemoirNarrativeActorType.AI,
        )
    )
    duplicate = await entity_repository.create(
        MemoirNarrativeEntityCreate(
            project_id=project_id,
            entity_type=MemoirNarrativeEntityType.PERSON,
            canonical_name="后来邀请创业的人",
            aliases=["前同事"],
            created_by=MemoirNarrativeActorType.AI,
        )
    )
    claim_repository = MemoirClaimRepository(test_db)
    confirmed_claim = await claim_repository.create(
        MemoirClaimCreate(
            project_id=project_id,
            claim_type=MemoirClaimType.EVENT,
            statement="工作时的经理后来邀请我加入创业公司。",
            subject_entity_id=duplicate.id,
            created_by=MemoirNarrativeActorType.AI,
        )
    )
    rejected_claim = await claim_repository.create(
        MemoirClaimCreate(
            project_id=project_id,
            claim_type=MemoirClaimType.RELATIONSHIP,
            statement="工作时的经理和邀请创业的人是两个人。",
            subject_entity_id=retained.id,
            object_entity_id=duplicate.id,
            created_by=MemoirNarrativeActorType.AI,
        )
    )
    gap = MemoirNarrativeGap(
        project_id=project_id,
        scope_type="chapter",
        primary_chapter_id=chapter.id,
        gap_type="contradiction",
        severity="high",
        title="经理和邀请创业的人是否为同一人",
        description="两处讲述使用了不同人物称呼。",
        fingerprint=sha256(f"fact-resolution:{answer['utterance_id']}".encode()).hexdigest(),
        status="open",
        detected_by="ai",
    )
    test_db.add(gap)
    await test_db.flush()
    for claim, role in ((confirmed_claim, "supporting"), (rejected_claim, "conflicting")):
        test_db.add(
            MemoirNarrativeGapClaim(
                project_id=project_id,
                gap_id=gap.id,
                claim_id=claim.id,
                role=role,
            )
        )
    task = MemoirFollowupTask(
        project_id=project_id,
        gap_id=gap.id,
        chapter_id=chapter.id,
        answer_use="fact_resolution",
        question="工作时的经理和后来邀请您创业的人是同一个人吗？",
        purpose="确认两处人物称呼是否指向同一人。",
        known_context_summary="书中把经理和邀请创业的人写成了两个称呼。",
        priority=100,
        status="pending",
        created_by="system",
    )
    test_db.add(task)
    await test_db.flush()
    for claim in (confirmed_claim, rejected_claim):
        test_db.add(
            MemoirFollowupTaskClaim(
                project_id=project_id,
                task_id=task.id,
                claim_id=claim.id,
                context_role="known",
            )
        )
    relation = MemoirFollowupTaskUtterance(
        project_id=project_id,
        task_id=task.id,
        utterance_id=answer["utterance_id"],
        result_role="related_context",
        is_assignment_snapshot=True,
    )
    test_db.add(relation)
    await test_db.commit()
    return {
        "project_id": project_id,
        "user_id": first["user_id"],
        "chapter": chapter,
        "answer": answer,
        "answer_text": answer_text,
        "retained": retained,
        "duplicate": duplicate,
        "confirmed_claim": confirmed_claim,
        "rejected_claim": rejected_claim,
        "gap": gap,
        "task": task,
        "relation": relation,
    }


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
    assert event.metadata_json["event_signature"] == f"source_assigned:{chapter.id}:{uncovered['utterance_id']}"
    assert event.metadata_json["confidence_threshold"] == 0.53
    link = await test_db.scalar(
        select(MemoirChapterSourceLink).where(
            MemoirChapterSourceLink.project_id == project_id,
            MemoirChapterSourceLink.chapter_id == chapter.id,
            MemoirChapterSourceLink.utterance_id == uncovered["utterance_id"],
        )
    )
    assert link is not None
    assert link.status == MemoirChapterSourceLinkStatus.ACTIVE.value
    assert link.assigned_by == MemoirChapterSourceAssignedBy.AI.value
    assert link.assignment_confidence == 0.91
    assert link.assignment_reason == "讲述延续同一个章节主题"
    existing_link = await test_db.scalar(
        select(MemoirChapterSourceLink).where(
            MemoirChapterSourceLink.project_id == project_id,
            MemoirChapterSourceLink.chapter_id == chapter.id,
            MemoirChapterSourceLink.utterance_id == first["utterance_id"],
        )
    )
    assert existing_link is not None
    assert existing_link.assigned_by == MemoirChapterSourceAssignedBy.SYSTEM.value
    assert existing_link.assignment_reason == "sync_existing_source_ref"

    job = await test_db.scalar(
        select(MemoirEditingJob).where(MemoirEditingJob.chapter_id == chapter.id)
    )
    assert job is not None
    assert job.job_type == MemoirEditingJobType.REVISE_CHAPTER.value
    assert job.status == MemoirEditingJobStatus.PENDING.value
    assert job.payload_json["pending_source_utterance_ids"] == [uncovered["utterance_id"]]
    assert job.payload_json["ai_model"] == "deepseek-v4-pro"
    source_card_job = await test_db.scalar(
        select(MemoirEditingJob).where(
            MemoirEditingJob.project_id == project_id,
            MemoirEditingJob.job_type == MemoirEditingJobType.GENERATE_SOURCE_CARD.value,
            MemoirEditingJob.dedupe_key == f"generate_source_card:{uncovered['utterance_id']}",
        )
    )
    assert source_card_job is not None
    assert source_card_job.status == MemoirEditingJobStatus.PENDING.value
    assert source_card_job.payload_json["utterance_id"] == uncovered["utterance_id"]
    assert source_card_job.payload_json["reason"] == "source_auto_assigned"
    assert source_card_job.payload_json["ai_model"] == "deepseek-v4-pro"


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
    job = await _schedule_guarded_route_job(
        test_db,
        utterance_id=uncovered["utterance_id"],
        project_id=project_id,
        ai_model="deepseek-v4-pro",
    )
    await test_db.commit()

    assert await run_test_editing_job(test_db, job.id) is True
    await test_db.refresh(job)
    assert job.status == MemoirEditingJobStatus.SUCCEEDED.value
    assert job.result_json["assigned_source_count"] == 1
    assert len(job.result_json["narrative_ingestion_job_ids"]) == 1
    narrative_job = await test_db.get(
        MemoirEditingJob,
        job.result_json["narrative_ingestion_job_ids"][0],
    )
    assert narrative_job is not None
    assert narrative_job.job_type == MemoirEditingJobType.INGEST_NARRATIVE_STATE.value
    assert narrative_job.payload_json["transcript_version_id"] == uncovered["transcript_version_id"]
    assert narrative_job.payload_json["ai_model"] == "deepseek-v4-pro"

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
    assert event.metadata_json["event_signature"] == f"source_assigned:{chapter.id}:{uncovered['utterance_id']}"
    route_metadata = event.metadata_json["routes"][0]
    assert route_metadata["editing_job_id"] == job.id
    assert route_metadata["pipeline_source"] == "test"
    assert route_metadata["transcript_version_id"] == uncovered["transcript_version_id"]
    assert route_metadata["source_text_hash"] == source_text_hash(
        "后来想起外婆家的院子，最记得夏天一起摘菜和剥青豆。"
    )
    assert route_metadata["source_text_length"] == len("后来想起外婆家的院子，最记得夏天一起摘菜和剥青豆。")

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
    source_card_job = await test_db.scalar(
        select(MemoirEditingJob).where(
            MemoirEditingJob.project_id == project_id,
            MemoirEditingJob.job_type == MemoirEditingJobType.GENERATE_SOURCE_CARD.value,
            MemoirEditingJob.dedupe_key == f"generate_source_card:{uncovered['utterance_id']}",
        )
    )
    assert source_card_job is not None
    assert source_card_job.status == MemoirEditingJobStatus.PENDING.value
    assert source_card_job.payload_json["reason"] == "source_auto_assigned"
    assert source_card_job.payload_json["ai_model"] == "deepseek-v4-pro"


async def test_confirming_book_plan_requeues_sources_skipped_before_plan(client, test_db, monkeypatch):
    _register_source_routing_handler()
    first = await create_test_voice_transcript(
        client,
        test_db,
        "小时候在外婆家的院子里摘菜，夏天还会剥青豆。",
    )
    project_id = first["session"]["project_id"]
    uncovered = await create_test_voice_transcript(
        client,
        test_db,
        "后来想起外婆家的院子，最记得夏天一起摘菜和剥青豆。",
        project_id=project_id,
    )
    job = await _schedule_guarded_route_job(
        test_db,
        project_id=project_id,
        utterance_id=uncovered["utterance_id"],
    )
    await test_db.commit()

    assert await run_test_editing_job(test_db, job.id) is False
    await test_db.refresh(job)
    assert job.status == MemoirEditingJobStatus.SKIPPED.value
    assert job.error_message == "章节规划不存在"

    confirm_response = await client.put(
        f"/api/mobile/memoir/projects/{project_id}/book-plan",
        json={
            "title": "外婆家的童年",
            "chapters": [
                {
                    "title": "院子里的童年",
                    "source_refs": [memoir_source_ref(first, "外婆家的院子")],
                }
            ],
        },
    )

    assert confirm_response.status_code == 200, confirm_response.json()
    chapter_id = confirm_response.json()["chapters"][0]["id"]
    await test_db.refresh(job)
    assert job.status == MemoirEditingJobStatus.PENDING.value
    assert job.attempts == 0
    assert job.payload_json["source"] == "book_plan_confirmed"
    assert job.payload_json["utterance_ids"] == [uncovered["utterance_id"]]
    assert job.result_json == {}
    assert job.error_message == ""

    _patch_source_router_ai(monkeypatch, _assign_route(chapter_id))

    assert await run_test_editing_job(test_db, job.id) is True
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
    assert event.chapter_id == chapter_id
    assert event.utterance_ids_json == [uncovered["utterance_id"]]
    assert event.metadata_json["routes"][0]["pipeline_source"] == "book_plan_confirmed"


async def test_source_routing_job_skips_when_transcript_guard_is_missing(client, test_db):
    created = await create_test_voice_transcript(client, test_db, "后来还记得老家院子里的枣树。")
    project_id = created["session"]["project_id"]
    job = await enqueue_editing_job(
        test_db,
        project_id=project_id,
        job_type=MemoirEditingJobType.ROUTE_SOURCES,
        payload_json={"utterance_ids": [created["utterance_id"]]},
        dedupe_key=f"route_source:{created['utterance_id']}",
    )
    await test_db.commit()

    assert await run_test_editing_job(test_db, job.id) is False
    await test_db.refresh(job)
    assert job.status == MemoirEditingJobStatus.SKIPPED.value
    assert job.error_message == "source routing job missing transcript guard"
    assert job.result_json["stale_utterance_ids"] == []


async def test_source_routing_job_skips_when_transcript_changed_after_enqueue(client, test_db):
    created = await create_test_voice_transcript(client, test_db, "后来还记得老家院子里的枣树。")
    project_id = created["session"]["project_id"]
    job = await _schedule_guarded_route_job(
        test_db,
        project_id=project_id,
        utterance_id=created["utterance_id"],
    )
    utterance = await _load_utterance_for_routing(test_db, created["utterance_id"])
    version = MemoirTranscriptVersion(
        voice_session_id=utterance.voice_session_id,
        source_artifact_id=utterance.id,
        version_type=MemoirTranscriptVersionType.USER_EDITED.value,
        content="用户重新整理后的讲述。",
        created_by=MemoirCreatedBy.USER.value,
    )
    test_db.add(version)
    await test_db.flush()
    utterance.transcript_edited = "用户重新整理后的讲述。"
    assert utterance.chat_message is not None
    utterance.chat_message.content_text = "用户重新整理后的讲述。"
    utterance.chat_message.transcript_version_id = version.id
    test_db.add_all([utterance, utterance.chat_message])
    await test_db.commit()

    assert await run_test_editing_job(test_db, job.id) is False
    await test_db.refresh(job)
    assert job.status == MemoirEditingJobStatus.SKIPPED.value
    assert job.error_message == "transcript changed before source routing"
    assert job.result_json["stale_utterance_ids"] == [created["utterance_id"]]
    event = await test_db.scalar(
        select(MemoirAutomationEvent).where(MemoirAutomationEvent.project_id == project_id)
    )
    assert event is None


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


async def test_completion_mixed_answer_assigns_narrative_part_to_task_chapter(
    client,
    test_db,
    monkeypatch,
):
    first = await create_test_voice_transcript(client, test_db, "小时候常在外婆家的院子里玩。")
    project_id = first["session"]["project_id"]
    _, chapter = await create_test_book_plan(
        test_db,
        project_id=project_id,
        source_ref=memoir_source_ref(first, "外婆家的院子"),
        chapter_title="院子里的童年",
    )
    direct_text = "外婆会带着我摘菜，夏天我们还会坐在屋檐下剥青豆。"
    clarification_text = "这句话只是解释为什么要补这一段。"
    answer = await create_test_voice_transcript(
        client,
        test_db,
        direct_text + clarification_text,
    )
    gap = MemoirNarrativeGap(
        project_id=project_id,
        scope_type="chapter",
        primary_chapter_id=chapter.id,
        gap_type="causality",
        severity="medium",
        title="补充院子里的生活",
        description="讲清楚外婆和孩子在院子里做过什么。",
        fingerprint=sha256(b"completion-capture-target-chapter").hexdigest(),
        status="open",
        detected_by="ai",
    )
    test_db.add(gap)
    await test_db.flush()
    task = MemoirFollowupTask(
        project_id=project_id,
        gap_id=gap.id,
        chapter_id=chapter.id,
        question="你和外婆在院子里做过哪些事？",
        purpose="补充院子生活的具体经历。",
        priority=100,
        status="pending",
        created_by="system",
    )
    test_db.add(task)
    await test_db.flush()
    relation = MemoirFollowupTaskUtterance(
        project_id=project_id,
        task_id=task.id,
        utterance_id=answer["utterance_id"],
        result_role="related_context",
        is_assignment_snapshot=True,
    )
    test_db.add(relation)
    await test_db.commit()
    _patch_source_router_ai(
        monkeypatch,
        _completion_route(
            answer_kind="mixed",
            direct_narrative_text=direct_text,
            clarification_text=clarification_text,
        ),
    )

    result = await run_source_auto_assignment_for_utterances(
        test_db,
        user_id=first["user_id"],
        project_id=project_id,
        utterance_ids=[answer["utterance_id"]],
    )

    assert result.assigned_source_count == 1
    assert result.items[0].action == "assign_to_chapter"
    assert result.items[0].target_chapter_id == chapter.id
    assert result.items[0].confidence == 0.95
    assert "补全访谈" in result.items[0].reason
    link = await test_db.scalar(
        select(MemoirChapterSourceLink).where(
            MemoirChapterSourceLink.project_id == project_id,
            MemoirChapterSourceLink.chapter_id == chapter.id,
            MemoirChapterSourceLink.utterance_id == answer["utterance_id"],
        )
    )
    assert link is not None
    assert link.assigned_by == MemoirChapterSourceAssignedBy.SYSTEM.value
    await test_db.refresh(relation)
    assert relation.answer_kind == "mixed"
    assert relation.direct_narrative_text == direct_text
    assert relation.clarification_text == clarification_text


async def test_fact_resolution_applies_semantic_decisions_before_closing_task(
    client,
    test_db,
    monkeypatch,
):
    case = await _create_fact_resolution_case(client, test_db)
    route_payload = _completion_route(
        answer_kind="fact_resolution",
        fact_resolution_text=case["answer_text"],
    )
    _patch_source_router_ai(monkeypatch, route_payload, route_payload)
    fact_ai = _patch_fact_resolution_ai(
        monkeypatch,
        {
            "status": "resolved",
            "operations": [
                {
                    "operation": "merge_entities",
                    "source_entity_id": case["duplicate"].id,
                    "retained_entity_id": case["retained"].id,
                    "reason": "用户明确说两个称呼都是同一个人。",
                },
                {
                    "operation": "confirm_claim",
                    "claim_id": case["confirmed_claim"].id,
                    "reason": "用户确认经理后来邀请自己加入创业公司。",
                },
                {
                    "operation": "reject_claim",
                    "claim_id": case["rejected_claim"].id,
                    "reason": "用户明确否定这是两个人。",
                },
            ],
            "confirmation_question": "",
            "reason": "回答明确解决了人物身份矛盾。",
        },
    )

    result = await run_source_auto_assignment_for_utterances(
        test_db,
        user_id=case["user_id"],
        project_id=case["project_id"],
        utterance_ids=[case["answer"]["utterance_id"]],
    )
    repeated = await run_source_auto_assignment_for_utterances(
        test_db,
        user_id=case["user_id"],
        project_id=case["project_id"],
        utterance_ids=[case["answer"]["utterance_id"]],
    )

    await test_db.refresh(case["duplicate"])
    await test_db.refresh(case["confirmed_claim"])
    await test_db.refresh(case["rejected_claim"])
    await test_db.refresh(case["task"])
    await test_db.refresh(case["gap"])
    await test_db.refresh(case["relation"])
    assert result.items[0].action == "record_as_context"
    assert repeated.items[0].action == "record_as_context"
    assert result.assigned_source_count == 0
    assert case["duplicate"].merged_into_entity_id == case["retained"].id
    assert case["confirmed_claim"].subject_entity_id == case["retained"].id
    assert case["confirmed_claim"].verification_status == "confirmed"
    assert case["rejected_claim"].verification_status == "rejected"
    assert case["task"].status == "answered"
    assert case["gap"].status == "resolved"
    assert case["relation"].result_role == "target_answer"

    decisions_for_answer = list(
        (
            await test_db.scalars(
                select(MemoirNarrativeDecision).where(
                    MemoirNarrativeDecision.project_id == case["project_id"],
                    MemoirNarrativeDecision.source_followup_task_utterance_id
                    == case["relation"].id,
                )
            )
        ).all()
    )
    assert {decision.action for decision in decisions_for_answer} >= {
        "merge",
        "confirm",
        "reject",
    }
    assert {
        decision.source_transcript_version_id for decision in decisions_for_answer
    } == {case["answer"]["transcript_version_id"]}
    answer_evidence = list(
        (
            await test_db.scalars(
                select(MemoirClaimEvidence).where(
                    MemoirClaimEvidence.project_id == case["project_id"],
                    MemoirClaimEvidence.transcript_version_id
                    == case["answer"]["transcript_version_id"],
                )
            )
        ).all()
    )
    assert {evidence.evidence_role for evidence in answer_evidence} == {
        "supports",
        "contradicts",
    }
    assert {evidence.audio_file_id for evidence in answer_evidence} == {
        case["answer"]["audio_file_id"]
    }
    assert {(evidence.start_ms, evidence.end_ms) for evidence in answer_evidence} == {
        (0, 8000)
    }
    chapter_audit_job = await test_db.scalar(
        select(MemoirEditingJob).where(
            MemoirEditingJob.project_id == case["project_id"],
            MemoirEditingJob.chapter_id == case["chapter"].id,
            MemoirEditingJob.job_type
            == MemoirEditingJobType.RECOMPUTE_CHAPTER_STRUCTURE_AUDIT.value,
        )
    )
    assert chapter_audit_job is not None
    assert chapter_audit_job.payload_json["source"] == "book_completion_semantic_answer"
    link = await test_db.scalar(
        select(MemoirChapterSourceLink).where(
            MemoirChapterSourceLink.project_id == case["project_id"],
            MemoirChapterSourceLink.utterance_id == case["answer"]["utterance_id"],
        )
    )
    assert link is None
    assert len(fact_ai.calls) == 1
    prompt = str(fact_ai.calls[0]["user_prompt"])
    assert case["confirmed_claim"].id in prompt
    assert case["rejected_claim"].id in prompt
    assert case["retained"].id in prompt
    assert case["duplicate"].id in prompt


async def test_ambiguous_fact_resolution_keeps_task_open_without_semantic_changes(
    client,
    test_db,
    monkeypatch,
):
    case = await _create_fact_resolution_case(client, test_db)
    _patch_source_router_ai(
        monkeypatch,
        _completion_route(
            answer_kind="fact_resolution",
            fact_resolution_text=case["answer_text"],
        ),
    )
    confirmation_question = "你说的同一个人，是工作时的经理和后来邀请你创业的人吗？"
    _patch_fact_resolution_ai(
        monkeypatch,
        {
            "status": "needs_confirmation",
            "operations": [],
            "confirmation_question": confirmation_question,
            "reason": "现有回答还不能唯一对应到书中的人物。",
        },
    )

    result = await run_source_auto_assignment_for_utterances(
        test_db,
        user_id=case["user_id"],
        project_id=case["project_id"],
        utterance_ids=[case["answer"]["utterance_id"]],
    )

    await test_db.refresh(case["duplicate"])
    await test_db.refresh(case["confirmed_claim"])
    await test_db.refresh(case["rejected_claim"])
    await test_db.refresh(case["task"])
    await test_db.refresh(case["gap"])
    await test_db.refresh(case["relation"])
    assert result.items[0].action == "hold_for_review"
    assert result.assigned_source_count == 0
    assert case["duplicate"].merged_into_entity_id is None
    assert case["confirmed_claim"].verification_status == "unreviewed"
    assert case["rejected_claim"].verification_status == "unreviewed"
    assert case["task"].status == "pending"
    assert case["gap"].status == "open"
    assert case["relation"].interpretation_status == "needs_confirmation"
    assert case["relation"].answer_kind is None
    assert case["relation"].confirmation_question == confirmation_question
    decision_count = await test_db.scalar(
        select(func.count(MemoirNarrativeDecision.id)).where(
            MemoirNarrativeDecision.project_id == case["project_id"],
        )
    )
    assert decision_count == 0
    review_event = await test_db.scalar(
        select(MemoirAutomationEvent).where(
            MemoirAutomationEvent.project_id == case["project_id"],
            MemoirAutomationEvent.event_type == MemoirAutomationEventType.SOURCE_HELD.value,
        )
    )
    assert review_event is not None
    assert review_event.summary_text == confirmation_question
    confirmed = await client.post(
        f"/api/mobile/memoir/projects/{case['project_id']}"
        f"/book-source-review-items/{review_event.id}/confirm",
        json={
            "followup_answer_kind": "fact_resolution",
            "fact_resolution_text": case["answer_text"],
        },
    )
    assert confirmed.status_code == 200, confirmed.json()
    assert confirmed.json()["item"]["status"] == "needs_review"
    assert (
        confirmed.json()["item"]["followup_answer"]["status"]
        == "needs_confirmation"
    )
    await test_db.refresh(case["task"])
    await test_db.refresh(case["gap"])
    assert case["task"].status == "pending"
    assert case["gap"].status == "open"


async def test_completion_clarification_updates_task_without_narrative_ingestion(
    client,
    test_db,
    monkeypatch,
):
    first = await create_test_voice_transcript(
        client,
        test_db,
        "我去看望爷爷，还给他买了西瓜。",
    )
    project_id = first["session"]["project_id"]
    _, chapter = await create_test_book_plan(
        test_db,
        project_id=project_id,
        source_ref=memoir_source_ref(first, "看望爷爷并买西瓜"),
        chapter_title="回成都后的疏离与克制",
    )
    answer_text = "我想说的其实是我想发给他的一句话。我想告诉他我今天去看了我爷爷的事情，但是我没有发给他，我忍住了。"
    answer = await create_test_voice_transcript(client, test_db, answer_text)
    gap = MemoirNarrativeGap(
        project_id=project_id,
        scope_type="chapter",
        primary_chapter_id=chapter.id,
        gap_type="causality",
        severity="medium",
        title="看望爷爷与章节主题的关联不明确",
        description="需要解释这段内容为什么属于本章。",
        fingerprint=sha256(b"watermelon-clarification-regression").hexdigest(),
        status="open",
        detected_by="ai",
    )
    test_db.add(gap)
    await test_db.flush()
    task = MemoirFollowupTask(
        project_id=project_id,
        gap_id=gap.id,
        chapter_id=chapter.id,
        answer_use="clarification",
        question="买西瓜这件事与本章想表达的克制有什么关系？",
        purpose="解释已有事件与章节主题的关系。",
        known_context_summary="已有素材写到看望爷爷并买西瓜。",
        priority=100,
        status="pending",
        created_by="system",
    )
    test_db.add(task)
    await test_db.flush()
    relation = MemoirFollowupTaskUtterance(
        project_id=project_id,
        task_id=task.id,
        utterance_id=answer["utterance_id"],
        result_role="related_context",
        is_assignment_snapshot=True,
    )
    test_db.add(relation)
    await test_db.commit()
    stories_before = await test_db.scalar(
        select(func.count(MemoirStory.id)).where(MemoirStory.project_id == project_id)
    )
    _patch_source_router_ai(
        monkeypatch,
        _completion_route(
            answer_kind="clarification",
            clarification_text=answer_text,
        ),
    )

    result = await run_source_auto_assignment_for_utterances(
        test_db,
        user_id=first["user_id"],
        project_id=project_id,
        utterance_ids=[answer["utterance_id"]],
    )

    assert result.assigned_source_count == 0
    assert result.items[0].action == "record_as_context"
    assert result.items[0].skip_reason == "recorded_as_completion_context"
    assert result.narrative_ingestion_job_ids == []
    await test_db.refresh(task)
    await test_db.refresh(gap)
    await test_db.refresh(relation)
    assert task.status == "answered"
    assert gap.status == "resolved"
    assert relation.result_role == "target_answer"
    assert relation.interpretation_status == "resolved"
    assert relation.answer_kind == "clarification"
    assert relation.clarification_text == answer_text
    assert relation.direct_narrative_text == ""
    link = await test_db.scalar(
        select(MemoirChapterSourceLink).where(
            MemoirChapterSourceLink.project_id == project_id,
            MemoirChapterSourceLink.utterance_id == answer["utterance_id"],
        )
    )
    assert link is None
    coverage = await client.get(
        f"/api/mobile/memoir/projects/{project_id}/book-source-coverage"
    )
    assert coverage.status_code == 200
    assert coverage.json()["uncovered_source_count"] == 0
    stories_after = await test_db.scalar(
        select(func.count(MemoirStory.id)).where(MemoirStory.project_id == project_id)
    )
    assert stories_after == stories_before


async def test_uncertain_completion_answer_waits_for_user_without_assignment_or_ingestion(
    client,
    test_db,
    monkeypatch,
):
    first = await create_test_voice_transcript(client, test_db, "我去看望爷爷，还给他买了西瓜。")
    project_id = first["session"]["project_id"]
    _, chapter = await create_test_book_plan(
        test_db,
        project_id=project_id,
        source_ref=memoir_source_ref(first, "看望爷爷并买西瓜"),
        chapter_title="回成都后的疏离与克制",
    )
    answer_text = "我不确定这算解释，还是想补进书里的话。"
    answer = await create_test_voice_transcript(client, test_db, answer_text)
    gap = MemoirNarrativeGap(
        project_id=project_id,
        scope_type="chapter",
        primary_chapter_id=chapter.id,
        gap_type="causality",
        severity="medium",
        title="回答用途不明确",
        description="需要用户确认回答用途。",
        fingerprint=sha256(b"completion-needs-confirmation").hexdigest(),
        status="open",
        detected_by="ai",
    )
    test_db.add(gap)
    await test_db.flush()
    task = MemoirFollowupTask(
        project_id=project_id,
        gap_id=gap.id,
        chapter_id=chapter.id,
        answer_use="clarification",
        question="这件事与本章的关系是什么？",
        purpose="确认章节关系。",
        priority=100,
        status="pending",
        created_by="system",
    )
    test_db.add(task)
    await test_db.flush()
    relation = MemoirFollowupTaskUtterance(
        project_id=project_id,
        task_id=task.id,
        utterance_id=answer["utterance_id"],
        result_role="related_context",
        is_assignment_snapshot=True,
    )
    test_db.add(relation)
    await test_db.commit()
    confirmation_question = "这段话是在解释原有内容，还是希望作为新的回忆内容写进书里？"
    _patch_source_router_ai(
        monkeypatch,
        _completion_route(
            answer_kind=None,
            confirmation_question=confirmation_question,
        ),
    )

    result = await run_source_auto_assignment_for_utterances(
        test_db,
        user_id=first["user_id"],
        project_id=project_id,
        utterance_ids=[answer["utterance_id"]],
    )

    assert result.assigned_source_count == 0
    assert result.narrative_ingestion_job_ids == []
    assert result.items[0].action == "hold_for_review"
    await test_db.refresh(task)
    await test_db.refresh(gap)
    await test_db.refresh(relation)
    assert task.status == "pending"
    assert gap.status == "open"
    assert relation.interpretation_status == "needs_confirmation"
    assert relation.answer_kind is None
    assert relation.confirmation_question == confirmation_question
    link = await test_db.scalar(
        select(MemoirChapterSourceLink).where(
            MemoirChapterSourceLink.project_id == project_id,
            MemoirChapterSourceLink.utterance_id == answer["utterance_id"],
        )
    )
    assert link is None
    review_event = await test_db.scalar(
        select(MemoirAutomationEvent).where(
            MemoirAutomationEvent.project_id == project_id,
            MemoirAutomationEvent.event_type == MemoirAutomationEventType.SOURCE_HELD.value,
            MemoirAutomationEvent.status == MemoirAutomationEventStatus.NEEDS_REVIEW.value,
        )
    )
    assert review_event is not None
    assert review_event.summary_text == confirmation_question
    assert review_event.metadata_json["route"]["followup_answer"]["status"] == "needs_confirmation"

    pending_review = await client.get(
        f"/api/mobile/memoir/projects/{project_id}/book-source-review-items"
    )
    assert pending_review.status_code == 200
    assert pending_review.json()["items"][0]["source_text"] == answer_text

    confirmed = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-source-review-items/{review_event.id}/confirm",
        json={
            "followup_answer_kind": "clarification",
            "clarification_text": answer_text,
        },
    )

    assert confirmed.status_code == 200
    assert confirmed.json()["item"]["status"] == "completed"
    assert confirmed.json()["item"]["route_action"] == "record_as_context"
    assert (
        confirmed.json()["item"]["followup_answer"]["status"]
        == "resolved"
    )
    await test_db.refresh(task)
    await test_db.refresh(gap)
    await test_db.refresh(relation)
    assert task.status == "answered"
    assert gap.status == "resolved"
    assert relation.interpretation_status == "resolved"
    assert relation.answer_kind == "clarification"
    assert relation.clarification_text == answer_text


async def test_source_review_does_not_expose_utterance_text_from_another_project(
    client,
    test_db,
):
    project_source = await create_test_voice_transcript(client, test_db, "当前项目的讲述。")
    other_project = await client.post(
        "/api/mobile/memoir/projects",
        json={"title": "另一个项目"},
    )
    assert other_project.status_code == 201
    other_source = await create_test_voice_transcript(
        client,
        test_db,
        "另一个项目的私密讲述。",
        project_id=other_project.json()["id"],
    )
    event = MemoirAutomationEvent(
        project_id=project_source["session"]["project_id"],
        event_type=MemoirAutomationEventType.SOURCE_HELD.value,
        status=MemoirAutomationEventStatus.NEEDS_REVIEW.value,
        utterance_ids_json=[other_source["utterance_id"]],
        summary_text="需要确认讲述用途。",
        metadata_json={
            "route_action": "hold_for_review",
            "material_type": "memoir_content",
            "utterance_id": other_source["utterance_id"],
        },
    )
    test_db.add(event)
    await test_db.commit()

    response = await client.get(
        f"/api/mobile/memoir/projects/{project_source['session']['project_id']}"
        "/book-source-review-items"
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["id"] == event.id
    assert response.json()["items"][0]["source_text"] == ""


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
    job = await _schedule_guarded_route_job(
        test_db,
        utterance_id=uncovered["utterance_id"],
        project_id=project_id,
    )
    await test_db.commit()

    assert await run_test_editing_job(test_db, job.id) is True
    await test_db.refresh(job)
    assert job.status == MemoirEditingJobStatus.SUCCEEDED.value
    assert job.result_json["assigned_source_count"] == 0
    assert job.result_json["skipped_source_count"] == 1
    assert len(job.result_json["narrative_ingestion_job_ids"]) == 1

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
    assert event.metadata_json["route"]["editing_job_id"] == job.id
    assert event.metadata_json["route"]["transcript_version_id"] == uncovered["transcript_version_id"]
    assert event.metadata_json["route"]["source_text_hash"] == source_text_hash(
        "参加工作以后，我在供销社负责盘点货物，第一次拿到奖金特别高兴。"
    )


async def test_source_auto_assignment_updates_existing_pending_review_event(client, test_db, monkeypatch):
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
    uncovered = await create_test_voice_transcript(
        client,
        test_db,
        "参加工作以后，我在供销社负责盘点货物，第一次拿到奖金特别高兴。",
    )
    _patch_source_router_ai(
        monkeypatch,
        _new_chapter_route("供销社工作初记"),
        _new_chapter_route("供销社盘点往事"),
        _assign_route(chapter.id),
    )

    first_response = await run_source_auto_assignment_for_utterances(
        test_db,
        user_id=first["user_id"],
        project_id=project_id,
        utterance_ids=[uncovered["utterance_id"]],
    )
    second_response = await run_source_auto_assignment_for_utterances(
        test_db,
        user_id=first["user_id"],
        project_id=project_id,
        utterance_ids=[uncovered["utterance_id"]],
    )
    third_response = await run_source_auto_assignment_for_utterances(
        test_db,
        user_id=first["user_id"],
        project_id=project_id,
        utterance_ids=[uncovered["utterance_id"]],
    )

    assert first_response.events[0].id == second_response.events[0].id
    assert third_response.assigned_source_count == 1
    assert len(first_response.narrative_ingestion_job_ids) == 1
    assert second_response.narrative_ingestion_job_ids == first_response.narrative_ingestion_job_ids
    events = (
        await test_db.execute(
            select(MemoirAutomationEvent).where(
                MemoirAutomationEvent.project_id == project_id,
                MemoirAutomationEvent.event_type == MemoirAutomationEventType.SOURCE_HELD.value,
            )
        )
    ).scalars().all()
    assert len(events) == 1
    assert events[0].summary_text == "讲述已整理进「院子里的童年」，自动处理记录已隐藏。"
    assert events[0].metadata_json["route"]["suggested_title"] == "供销社盘点往事"
    assert events[0].status == MemoirAutomationEventStatus.COMPLETED.value
    assert events[0].chapter_id == chapter.id
    assert events[0].metadata_json["resolution"] == "confirmed"


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
    job = await _schedule_guarded_route_job(
        test_db,
        utterance_id=ai_interaction["utterance_id"],
        project_id=project_id,
    )
    await test_db.commit()

    assert await run_test_editing_job(test_db, job.id) is True
    await test_db.refresh(job)
    assert job.status == MemoirEditingJobStatus.SUCCEEDED.value
    assert job.result_json["assigned_source_count"] == 0
    assert job.result_json["skipped_source_count"] == 1
    assert job.result_json["narrative_ingestion_job_ids"] == []

    event = await test_db.scalar(
        select(MemoirAutomationEvent).where(
            MemoirAutomationEvent.project_id == project_id,
            MemoirAutomationEvent.event_type == MemoirAutomationEventType.SOURCE_HELD.value,
        )
    )
    assert event is None

    audit_job = await test_db.scalar(
        select(MemoirEditingJob).where(
            MemoirEditingJob.project_id == project_id,
            MemoirEditingJob.job_type == MemoirEditingJobType.QUALITY_AUDIT.value,
        )
    )
    assert audit_job is None

    ignored_utterance = await test_db.get(MemoirVoiceUtterance, ai_interaction["utterance_id"])
    assert ignored_utterance is not None
    assert ignored_utterance.source_material_type == "ai_interaction"
    assert ignored_utterance.source_route_action == "ignore"
    assert ignored_utterance.source_route_confidence == 0.94
    assert ignored_utterance.source_route_reason == "这是和 AI 的对话，不是回忆素材"
    assert ignored_utterance.source_route_updated_at is not None

    snapshot_response = await client.get(f"/api/mobile/memoir/projects/{project_id}/progress-snapshot")
    assert snapshot_response.status_code == 200
    snapshot = snapshot_response.json()
    assert snapshot["valid_utterance_count"] == 1
    assert snapshot["assigned_utterance_count"] == 1
    assert snapshot["pending_utterance_count"] == 0

    coverage_response = await client.get(f"/api/mobile/memoir/projects/{project_id}/book-source-coverage")
    assert coverage_response.status_code == 200
    coverage = coverage_response.json()
    assert coverage["total_source_count"] == 1
    assert coverage["covered_source_count"] == 1
    assert coverage["uncovered_source_count"] == 0
    assert [item["utterance_id"] for item in coverage["sources"]] == [first["utterance_id"]]
