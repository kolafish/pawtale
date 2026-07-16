from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.domain.memoir.narrative_types import (
    MemoirClaimCertainty,
    MemoirClaimPredicate,
    MemoirClaimPublicationStatus,
    MemoirClaimTimePrecision,
    MemoirClaimType,
    MemoirClaimVerificationStatus,
    MemoirFollowupAnswerUse,
    MemoirFollowupTaskStatus,
    MemoirNarrativeActorType,
    MemoirNarrativeGapActionReasonCode,
    MemoirNarrativeGapDetectedBy,
    MemoirNarrativeGapRecommendedAction,
    MemoirNarrativeGapScopeType,
    MemoirNarrativeGapSeverity,
    MemoirNarrativeGapStatus,
    MemoirNarrativeGapType,
    MemoirStoryCompletionStatus,
    MemoirStoryLifecycleStatus,
    MemoirStoryPublicationStatus,
    MemoirStoryType,
    MemoirStoryVerificationStatus,
)
from app.models.memoir import MemoirBookPlanChapter, MemoirEditingJob
from app.models.memoir_narrative import (
    MemoirClaim,
    MemoirFollowupTask,
    MemoirNarrativeGap,
    MemoirNarrativeDecision,
    MemoirStory,
    MemoirStoryClaim,
)
from app.services.memoir.narrative import (
    completion_planner,
    completion_refresh,
    followup_planner,
    gap_semantic,
    gaps,
)
from app.services.memoir.editing_job_handlers import register_memoir_editing_job_handlers
from app.services.memoir.editing_jobs import claim_editing_job, run_editing_job
from app.services.memoir.narrative.gap_rules import (
    ChapterAuditContext,
    ChapterAuditDraftBlock,
    evaluate_deterministic_gaps,
    load_chapter_audit_context,
)
from app.schemas.memoir_narrative import MemoirNarrativeGapCandidate
from tests.memoir_helpers import (
    create_test_book_plan_with_chapters,
    create_test_voice_transcript,
)


class FakeAIClient:
    provider_name = "test-provider"
    model_name = "test-model"

    def __init__(self, content: str):
        self.content = content

    async def _call_llm(self, *_args, **_kwargs):
        return self.content


def _contract_request(key: str, objective: str) -> dict:
    return {
        "idempotency_key": key,
        "objective": objective,
        "reader_takeaway": "读者理解这段经历对叙述者的影响。",
        "core_questions": [{"key": "meaning", "question": "这段经历为什么重要？"}],
        "constraints": {
            "include_story_ids": [],
            "exclude_story_ids": [],
            "include_topics": [],
            "exclude_topics": [],
        },
    }


async def test_chapter_audit_uses_latest_draft_contract_over_older_confirmed_version(
    client,
    test_db,
):
    created = await create_test_voice_transcript(client, test_db)
    project_id = created["session"]["project_id"]
    _, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["求学"],
    )
    chapter_id = chapters[0].id
    first = await client.post(
        f"/api/memoir/projects/{project_id}/chapters/{chapter_id}/contract/revisions",
        json=_contract_request("contract-v1", "记录离家求学的经过。"),
    )
    assert first.status_code == 200, first.text
    node = await client.post(
        f"/api/memoir/projects/{project_id}/chapter-contracts/{first.json()['id']}/nodes",
        json={
            "idempotency_key": "opening-v1",
            "node_type": "opening",
            "title": "开场",
            "purpose": "交代离家前的处境。",
            "required": True,
            "status": "ready",
        },
    )
    assert node.status_code == 200, node.text
    confirmed = await client.post(
        f"/api/memoir/projects/{project_id}/chapter-contracts/{first.json()['id']}/confirm"
    )
    assert confirmed.status_code == 200, confirmed.text
    draft = await client.post(
        f"/api/memoir/projects/{project_id}/chapters/{chapter_id}/contract/revisions",
        json=_contract_request("contract-v2", "重点记录离家决定对家庭关系的影响。"),
    )
    assert draft.status_code == 200, draft.text

    context = await load_chapter_audit_context(
        test_db,
        project_id=project_id,
        chapter_id=chapter_id,
    )

    assert context.contract is not None
    assert context.contract.id == draft.json()["id"]
    assert context.contract.version == 2


async def test_repeated_gap_audit_preserves_user_resolution_without_duplicates(
    client,
    test_db,
):
    created = await create_test_voice_transcript(client, test_db)
    project_id = created["session"]["project_id"]
    _, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["童年"],
    )
    chapter_id = chapters[0].id
    context = await load_chapter_audit_context(
        test_db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    candidates = evaluate_deterministic_gaps(context)
    assert len(candidates) == 1

    first, created_count = await gaps.upsert_gaps(
        test_db,
        project_id=project_id,
        candidates=candidates,
    )
    await test_db.commit()
    assert created_count == 1
    gap_id = first[0].id

    accepted = await client.post(
        f"/api/memoir/projects/{project_id}/gaps/{gap_id}/accept-incomplete",
        json={
            "idempotency_key": "accept-missing-contract",
            "note": "目前素材有限，保留为未完成章节。",
        },
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == MemoirNarrativeGapStatus.ACCEPTED_INCOMPLETE.value

    repeated, repeated_created_count = await gaps.upsert_gaps(
        test_db,
        project_id=project_id,
        candidates=candidates,
    )
    await test_db.commit()
    assert repeated_created_count == 0
    assert repeated[0].id == gap_id
    assert repeated[0].status == MemoirNarrativeGapStatus.ACCEPTED_INCOMPLETE.value
    assert repeated[0].detection_count == 2
    assert await gaps.reconcile_project_rule_gaps(
        test_db,
        project_id=project_id,
        complete_candidates=[],
    ) == 0
    assert repeated[0].status == MemoirNarrativeGapStatus.ACCEPTED_INCOMPLETE.value
    assert await test_db.scalar(
        select(func.count(MemoirNarrativeGap.id)).where(
            MemoirNarrativeGap.project_id == project_id
        )
    ) == 1
    assert await test_db.scalar(
        select(func.count(MemoirNarrativeDecision.id)).where(
            MemoirNarrativeDecision.project_id == project_id,
            MemoirNarrativeDecision.target_id == gap_id,
        )
    ) == 1

    workspace = await client.get(
        f"/api/memoir/projects/{project_id}/chapters/{chapter_id}/workspace"
    )
    assert workspace.status_code == 200, workspace.text
    body = workspace.json()
    summary = body["completion_summary"]
    assert summary["must_handle_count"] == 0
    assert summary["accepted_incomplete_count"] == 1
    assert "percentage" not in str(body).lower()


async def test_chapter_workspace_uses_formalization_policy_for_gap_groups(
    client,
    test_db,
):
    created = await create_test_voice_transcript(client, test_db)
    project_id = created["session"]["project_id"]
    _, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["第一次离家"],
    )
    chapter_id = chapters[0].id
    created_gaps, _ = await gaps.upsert_gaps(
        test_db,
        project_id=project_id,
        candidates=[
            MemoirNarrativeGapCandidate(
                scope_type=MemoirNarrativeGapScopeType.CHAPTER,
                primary_chapter_id=chapter_id,
                gap_type=MemoirNarrativeGapType.MISSING_SLOT,
                severity=MemoirNarrativeGapSeverity.HIGH,
                target_slot="reflection",
                title="还可以补充离家后的感受",
                detected_by=MemoirNarrativeGapDetectedBy.RULES,
            ),
            MemoirNarrativeGapCandidate(
                scope_type=MemoirNarrativeGapScopeType.CHAPTER,
                primary_chapter_id=chapter_id,
                gap_type=MemoirNarrativeGapType.CONTRADICTION,
                severity=MemoirNarrativeGapSeverity.HIGH,
                target_slot="departure_age",
                title="离家年龄存在冲突",
                detected_by=MemoirNarrativeGapDetectedBy.RULES,
            ),
        ],
    )
    await test_db.commit()

    workspace = await client.get(
        f"/api/memoir/projects/{project_id}/chapters/{chapter_id}/workspace"
    )

    assert workspace.status_code == 200, workspace.text
    summary = workspace.json()["completion_summary"]
    gaps_by_type = {gap.gap_type: gap.id for gap in created_gaps}
    assert [item["id"] for item in summary["review_gaps"]] == [
        gaps_by_type[MemoirNarrativeGapType.CONTRADICTION.value]
    ]
    assert summary["suggested_count"] == 1


async def test_project_rule_audit_resolves_obsolete_gap_and_reopens_it_if_condition_returns(
    client,
    test_db,
):
    created = await create_test_voice_transcript(client, test_db)
    project_id = created["session"]["project_id"]
    _, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["童年"],
    )
    context = await load_chapter_audit_context(
        test_db,
        project_id=project_id,
        chapter_id=chapters[0].id,
    )
    candidates = evaluate_deterministic_gaps(context)
    created_gaps, _ = await gaps.upsert_gaps(
        test_db,
        project_id=project_id,
        candidates=candidates,
    )
    gap_id = created_gaps[0].id

    assert await gaps.reconcile_project_rule_gaps(
        test_db,
        project_id=project_id,
        complete_candidates=[],
    ) == 1
    await test_db.flush()
    resolved = await test_db.get(MemoirNarrativeGap, gap_id)
    assert resolved is not None
    assert resolved.status == MemoirNarrativeGapStatus.RESOLVED.value
    assert resolved.resolved_by == MemoirNarrativeActorType.SYSTEM.value

    reopened, created_count = await gaps.upsert_gaps(
        test_db,
        project_id=project_id,
        candidates=candidates,
    )
    await test_db.commit()
    assert created_count == 0
    assert reopened[0].status == MemoirNarrativeGapStatus.OPEN.value
    assert reopened[0].resolved_by is None
    assert reopened[0].resolution_note == ""
    assert await test_db.scalar(
        select(func.count(MemoirNarrativeDecision.id)).where(
            MemoirNarrativeDecision.project_id == project_id,
            MemoirNarrativeDecision.target_id == gap_id,
        )
    ) == 2


async def test_completion_refresh_is_queued_on_existing_editing_job_worker(
    client,
    test_db,
):
    created = await create_test_voice_transcript(client, test_db)
    project_id = created["session"]["project_id"]
    _, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["青年时期"],
    )

    response = await client.post(
        f"/api/memoir/projects/{project_id}/chapters/{chapters[0].id}/completion-refresh-jobs",
        json={"include_semantic_audit": False},
    )

    assert response.status_code == 202, response.text
    assert response.json()["status"] == "pending"
    assert response.json()["chapter_id"] == chapters[0].id

    register_memoir_editing_job_handlers()
    lease = await claim_editing_job(test_db, response.json()["id"])
    assert lease is not None
    assert await run_editing_job(test_db, lease) is True
    job = await test_db.get(MemoirEditingJob, response.json()["id"])
    assert job is not None
    assert job.status == "succeeded"
    assert job.result_json["deterministic_gap_count"] == 1
    assert await test_db.scalar(
        select(func.count(MemoirNarrativeGap.id)).where(
            MemoirNarrativeGap.project_id == project_id,
            MemoirNarrativeGap.primary_chapter_id == chapters[0].id,
        )
    ) == 1

    semantic_response = await client.post(
        f"/api/memoir/projects/{project_id}/chapters/{chapters[0].id}/completion-refresh-jobs",
        json={"include_semantic_audit": True, "ai_model": "test-semantic-model"},
    )
    assert semantic_response.status_code == 202, semantic_response.text
    workspace = await client.get(
        f"/api/memoir/projects/{project_id}/chapters/{chapters[0].id}/workspace"
    )
    assert workspace.status_code == 200
    assert (
        workspace.json()["latest_refresh_job"]["id"]
        == semantic_response.json()["id"]
    )


async def test_semantic_reaudit_resolves_then_reopens_gap_and_task_if_it_recurs(
    client,
    test_db,
):
    created = await create_test_voice_transcript(client, test_db)
    project_id = created["session"]["project_id"]
    _, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["青年时期"],
    )
    candidate = MemoirNarrativeGapCandidate(
        scope_type=MemoirNarrativeGapScopeType.CHAPTER,
        primary_chapter_id=chapters[0].id,
        gap_type=MemoirNarrativeGapType.CAUSALITY,
        severity=MemoirNarrativeGapSeverity.MEDIUM,
        target_slot="semantic:opening:old-opening-gap",
        title="开头缺少背景",
        description="读者不知道故事发生前的处境。",
        detected_by=MemoirNarrativeGapDetectedBy.AI,
    )
    stored, _ = await gaps.upsert_gaps(
        test_db,
        project_id=project_id,
        candidates=[candidate],
    )
    task = MemoirFollowupTask(
        project_id=project_id,
        gap_id=stored[0].id,
        chapter_id=chapters[0].id,
        target_slot=stored[0].target_slot,
        answer_use=MemoirFollowupAnswerUse.CLARIFICATION.value,
        question="当时是什么背景？",
        purpose="补充章节背景",
        priority=2800,
        status=MemoirFollowupTaskStatus.PENDING.value,
        created_by=MemoirNarrativeActorType.SYSTEM.value,
    )
    test_db.add(task)
    await test_db.flush()

    assert await gaps.reconcile_chapter_semantic_gaps(
        test_db,
        project_id=project_id,
        chapter_id=chapters[0].id,
        complete_candidates=[],
    ) == 1
    await test_db.flush()

    assert stored[0].status == MemoirNarrativeGapStatus.RESOLVED.value
    assert task.status == MemoirFollowupTaskStatus.CANCELLED.value

    reopened, created_count = await gaps.upsert_gaps(
        test_db,
        project_id=project_id,
        candidates=[candidate],
    )
    await test_db.flush()

    assert created_count == 0
    assert reopened[0].id == stored[0].id
    assert reopened[0].status == MemoirNarrativeGapStatus.OPEN.value
    assert reopened[0].resolved_by is None
    assert reopened[0].resolution_note == ""
    assert task.status == MemoirFollowupTaskStatus.PENDING.value


async def test_gap_reaudit_does_not_switch_an_in_progress_interview(
    client,
    test_db,
):
    created = await create_test_voice_transcript(client, test_db)
    project_id = created["session"]["project_id"]
    _, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["求学"],
    )
    candidate = MemoirNarrativeGapCandidate(
        scope_type=MemoirNarrativeGapScopeType.CHAPTER,
        primary_chapter_id=chapters[0].id,
        gap_type=MemoirNarrativeGapType.CAUSALITY,
        severity=MemoirNarrativeGapSeverity.MEDIUM,
        target_slot="semantic:chapter_context:stable-gap",
        title="离家前的背景还不清楚",
        description="需要补充讲述者掌握的背景。",
        detected_by=MemoirNarrativeGapDetectedBy.AI,
        action_reason_signal=MemoirNarrativeGapActionReasonCode.MISSING_NEW_EVIDENCE,
    )
    stored, _ = await gaps.upsert_gaps(
        test_db,
        project_id=project_id,
        candidates=[candidate],
    )
    task = MemoirFollowupTask(
        project_id=project_id,
        gap_id=stored[0].id,
        chapter_id=chapters[0].id,
        answer_use=MemoirFollowupAnswerUse.CLARIFICATION.value,
        question="离家前，当时家里是什么情况？",
        purpose="补充章节背景",
        priority=2000,
        status=MemoirFollowupTaskStatus.IN_PROGRESS.value,
        assigned_session_id=created["session"]["id"],
        started_at=datetime.now(UTC).replace(tzinfo=None),
        created_by=MemoirNarrativeActorType.SYSTEM.value,
    )
    test_db.add(task)
    await test_db.flush()

    reaudited, created_count = await gaps.upsert_gaps(
        test_db,
        project_id=project_id,
        candidates=[
            candidate.model_copy(
                update={
                    "action_reason_signal": (
                        MemoirNarrativeGapActionReasonCode.EXISTING_MATERIAL_AVAILABLE
                    )
                }
            )
        ],
    )

    assert created_count == 0
    assert (
        reaudited[0].recommended_action
        == MemoirNarrativeGapRecommendedAction.INTERVIEW.value
    )
    assert task.status == MemoirFollowupTaskStatus.IN_PROGRESS.value


async def test_gap_reaudit_cancels_a_pending_task_that_no_longer_needs_interview(
    client,
    test_db,
):
    created = await create_test_voice_transcript(client, test_db)
    project_id = created["session"]["project_id"]
    _, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["求学"],
    )
    candidate = MemoirNarrativeGapCandidate(
        scope_type=MemoirNarrativeGapScopeType.CHAPTER,
        primary_chapter_id=chapters[0].id,
        gap_type=MemoirNarrativeGapType.CAUSALITY,
        severity=MemoirNarrativeGapSeverity.MEDIUM,
        target_slot="semantic:opening:channel-change",
        title="开头缺少进入故事的背景",
        description="需要判断是否还要补充新素材。",
        detected_by=MemoirNarrativeGapDetectedBy.AI,
        action_reason_signal=MemoirNarrativeGapActionReasonCode.MISSING_NEW_EVIDENCE,
    )
    stored, _ = await gaps.upsert_gaps(
        test_db,
        project_id=project_id,
        candidates=[candidate],
    )
    task = MemoirFollowupTask(
        project_id=project_id,
        gap_id=stored[0].id,
        chapter_id=chapters[0].id,
        answer_use=MemoirFollowupAnswerUse.CLARIFICATION.value,
        question="故事开始前发生了什么？",
        purpose="补充章节背景",
        priority=2000,
        status=MemoirFollowupTaskStatus.PENDING.value,
        created_by=MemoirNarrativeActorType.SYSTEM.value,
    )
    test_db.add(task)
    await test_db.flush()

    reaudited, _ = await gaps.upsert_gaps(
        test_db,
        project_id=project_id,
        candidates=[
            candidate.model_copy(
                update={
                    "action_reason_signal": (
                        MemoirNarrativeGapActionReasonCode.EXISTING_MATERIAL_AVAILABLE
                    )
                }
            )
        ],
    )
    await followup_planner.generate_followup_tasks(
        test_db,
        project_id=project_id,
        chapter_id=chapters[0].id,
    )
    await test_db.flush()

    assert (
        reaudited[0].recommended_action
        == MemoirNarrativeGapRecommendedAction.EDITORIAL.value
    )
    assert task.status == MemoirFollowupTaskStatus.CANCELLED.value


def _semantic_context() -> ChapterAuditContext:
    now = datetime.now(UTC).replace(tzinfo=None)
    chapter = MemoirBookPlanChapter(
        id="chapter-1",
        project_id="project-1",
        plan_id="plan-1",
        title="离家求学",
        sort_order=0,
        created_at=now,
        updated_at=now,
    )
    story = MemoirStory(
        id="story-1",
        project_id="project-1",
        title="第一次离家",
        title_normalized="第一次离家",
        story_type=MemoirStoryType.EVENT.value,
        summary="十八岁时离开家乡。",
        time_precision=MemoirClaimTimePrecision.YEAR.value,
        lifecycle_status=MemoirStoryLifecycleStatus.ACTIVE.value,
        completion_status=MemoirStoryCompletionStatus.INCOMPLETE.value,
        verification_status=MemoirStoryVerificationStatus.UNREVIEWED.value,
        publication_status=MemoirStoryPublicationStatus.ELIGIBLE.value,
        slot_schema_version="1",
        version=1,
        created_by=MemoirNarrativeActorType.AI.value,
        updated_by=MemoirNarrativeActorType.AI.value,
        created_at=now,
        updated_at=now,
    )
    claim = MemoirClaim(
        id="claim-1",
        project_id="project-1",
        claim_type=MemoirClaimType.EVENT.value,
        statement="十八岁时离开家乡。",
        predicate_code=MemoirClaimPredicate.NARRATIVE_FACT.value,
        time_precision=MemoirClaimTimePrecision.YEAR.value,
        certainty=MemoirClaimCertainty.EXACT.value,
        verification_status=MemoirClaimVerificationStatus.UNREVIEWED.value,
        publication_status=MemoirClaimPublicationStatus.ELIGIBLE.value,
        created_by=MemoirNarrativeActorType.AI.value,
        updated_by=MemoirNarrativeActorType.AI.value,
        created_at=now,
        updated_at=now,
    )
    story_claim = MemoirStoryClaim(
        id="story-claim-1",
        project_id="project-1",
        story_id=story.id,
        claim_id=claim.id,
        narrative_role="action",
        sort_order=0,
        is_core=True,
        created_by=MemoirNarrativeActorType.AI.value,
        created_at=now,
    )
    return ChapterAuditContext(
        chapter=chapter,
        contract=None,
        nodes=[],
        links=[],
        stories={story.id: story},
        story_claims={story.id: [story_claim]},
        claims={claim.id: claim},
        evidence_counts={claim.id: 1},
        slots={},
    )


async def test_semantic_gap_audit_rejects_evidence_outside_chapter():
    context = _semantic_context()
    invalid_payload = """{
      "schema_version": "2",
      "gaps": [{
        "gap_type": "unsupported_fact",
        "severity": "high",
        "dimension": "unsupported_fact",
        "reason_signal": "publication_decision_required",
        "title": "事实缺少来源",
        "description": "需要核对这条事实。",
        "story_ids": ["story-1"],
        "claim_ids": ["claim-outside-chapter"]
      }]
    }"""

    with pytest.raises(ValueError, match="evidence schema"):
        await gap_semantic.evaluate_semantic_gaps(
            context,
            ai_client=FakeAIClient(invalid_payload),
        )

    assert context.claims["claim-1"].statement == "十八岁时离开家乡。"
    assert context.stories["story-1"].summary == "十八岁时离开家乡。"


async def test_semantic_gap_audit_rejects_migration_only_reason_signal():
    context = _semantic_context()
    invalid_payload = """{
      "schema_version": "2",
      "gaps": [{
        "gap_type": "causality",
        "severity": "medium",
        "dimension": "opening",
        "reason_signal": "migration_review_required",
        "title": "开头需要处理",
        "description": "模型不能把迁移兜底原因作为语义判断。",
        "story_ids": ["story-1"],
        "claim_ids": ["claim-1"]
      }]
    }"""

    with pytest.raises(ValueError, match="evidence schema"):
        await gap_semantic.evaluate_semantic_gaps(
            context,
            ai_client=FakeAIClient(invalid_payload),
        )


async def test_semantic_gap_audit_reads_draft_blocks_and_accepts_chapter_level_issue():
    draft_block = ChapterAuditDraftBlock(
        id="block-1",
        node_id="node-1",
        block_role="connective",
        sort_order=0,
        editorial_status="draft",
        freshness_status="current",
        current_version_id="block-version-1",
        content="第二天，他直接去了新的学校。此前的搬家背景没有交代。",
    )
    context = replace(_semantic_context(), draft_blocks=(draft_block,))
    prompt = gap_semantic.build_semantic_gap_prompt(context)

    assert draft_block.content in prompt
    assert "先看整章是否缺少进入本章所需的背景" in prompt
    assert "不能仅因为事件简短" in prompt
    assert "最多输出 3 个问题" in prompt
    assert "纯事件前因后果类 causality 最多一个" in prompt
    assert "禁止套用" in prompt

    payload = """{
      "schema_version": "2",
      "gaps": [{
        "gap_type": "causality",
        "severity": "medium",
        "dimension": "opening",
        "reason_signal": "draft_usage_problem",
        "title": "开头缺少搬家背景",
        "description": "当前开头直接进入新学校，读者不知道为什么搬家。",
        "story_ids": ["story-1"],
        "claim_ids": ["claim-1"]
      }]
    }"""
    candidates, _, _ = await gap_semantic.evaluate_semantic_gaps(
        context,
        ai_client=FakeAIClient(payload),
    )

    assert len(candidates) == 1
    assert candidates[0].target_slot.startswith("semantic:opening:")
    action = completion_planner.plan_gap_action(candidates[0])
    assert action.recommended_action == MemoirNarrativeGapRecommendedAction.EDITORIAL
    assert (
        action.reason_code
        == MemoirNarrativeGapActionReasonCode.DRAFT_USAGE_PROBLEM
    )
    changed_context = replace(
        context,
        draft_blocks=(replace(draft_block, content="先交代搬家原因，再写进入新学校。"),),
    )
    assert completion_refresh.chapter_completion_content_signature(
        context
    ) != completion_refresh.chapter_completion_content_signature(changed_context)


async def test_semantic_gap_routes_truly_missing_background_to_interview():
    context = _semantic_context()
    payload = """{
      "schema_version": "2",
      "gaps": [{
        "gap_type": "causality",
        "severity": "medium",
        "dimension": "chapter_context",
        "reason_signal": "missing_new_evidence",
        "title": "离家前的处境还不清楚",
        "description": "现有素材没有说明为什么必须在那时离家。",
        "story_ids": ["story-1"],
        "claim_ids": ["claim-1"]
      }]
    }"""

    candidates, _, _ = await gap_semantic.evaluate_semantic_gaps(
        context,
        ai_client=FakeAIClient(payload),
    )

    assert len(candidates) == 1
    action = completion_planner.plan_gap_action(candidates[0])
    assert action.recommended_action == MemoirNarrativeGapRecommendedAction.INTERVIEW
    assert (
        action.reason_code
        == MemoirNarrativeGapActionReasonCode.MISSING_NEW_EVIDENCE
    )


async def test_semantic_gap_audit_keeps_one_question_per_editorial_dimension():
    context = _semantic_context()
    payload = """{
      "schema_version": "2",
      "gaps": [
        {
          "gap_type": "causality",
          "severity": "medium",
          "dimension": "causality",
          "reason_signal": "missing_new_evidence",
          "title": "离家原因不清楚",
          "description": "读者不知道为什么离家。",
          "story_ids": ["story-1"],
          "claim_ids": ["claim-1"]
        },
        {
          "gap_type": "causality",
          "severity": "medium",
          "dimension": "causality",
          "reason_signal": "missing_new_evidence",
          "title": "到校经过不清楚",
          "description": "读者不知道怎样到校。",
          "story_ids": ["story-1"],
          "claim_ids": ["claim-1"]
        },
        {
          "gap_type": "causality",
          "severity": "medium",
          "dimension": "opening",
          "reason_signal": "missing_new_evidence",
          "title": "开头进入得太突然",
          "description": "章节直接写离家，没有进入场景。",
          "story_ids": ["story-1"],
          "claim_ids": ["claim-1"]
        },
        {
          "gap_type": "relationship",
          "severity": "medium",
          "dimension": "person_introduction",
          "reason_signal": "missing_new_evidence",
          "title": "陪同者没有介绍",
          "description": "关键人物首次出现时身份不清楚。",
          "story_ids": ["story-1"],
          "claim_ids": ["claim-1"]
        }
      ]
    }"""

    candidates, _, _ = await gap_semantic.evaluate_semantic_gaps(
        context,
        ai_client=FakeAIClient(payload),
    )

    assert [candidate.target_slot.split(":", 2)[1] for candidate in candidates] == [
        "causality",
        "opening",
        "person_introduction",
    ]
