from __future__ import annotations

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
    MemoirNarrativeActorType,
    MemoirNarrativeGapStatus,
    MemoirStoryCompletionStatus,
    MemoirStoryLifecycleStatus,
    MemoirStoryPublicationStatus,
    MemoirStoryType,
    MemoirStoryVerificationStatus,
)
from app.models.memoir import MemoirBookPlanChapter, MemoirEditingJob
from app.models.memoir_narrative import (
    MemoirClaim,
    MemoirNarrativeGap,
    MemoirNarrativeDecision,
    MemoirStory,
    MemoirStoryClaim,
)
from app.services.memoir.narrative import gap_semantic, gaps
from app.services.memoir.editing_job_handlers import register_memoir_editing_job_handlers
from app.services.memoir.editing_jobs import claim_editing_job, run_editing_job
from app.services.memoir.narrative.gap_rules import (
    ChapterAuditContext,
    evaluate_deterministic_gaps,
    load_chapter_audit_context,
)
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
    assert body["gaps"]["must_handle"] == []
    assert [item["id"] for item in body["gaps"]["accepted_incomplete"]] == [gap_id]
    assert "percentage" not in str(body).lower()


async def test_gap_audit_is_queued_on_existing_editing_job_worker(client, test_db):
    created = await create_test_voice_transcript(client, test_db)
    project_id = created["session"]["project_id"]
    _, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["青年时期"],
    )

    response = await client.post(
        f"/api/memoir/projects/{project_id}/chapters/{chapters[0].id}/workspace/audits",
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
      "schema_version": "1",
      "gaps": [{
        "gap_type": "unsupported_fact",
        "severity": "high",
        "dimension": "unsupported_fact",
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
