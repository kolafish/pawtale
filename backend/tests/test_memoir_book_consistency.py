from __future__ import annotations

from datetime import datetime
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.domain.memoir.narrative_types import (
    MemoirChapterContractStatus,
    MemoirChapterNodeStatus,
    MemoirChapterNodeStoryRole,
    MemoirChapterNodeType,
    MemoirClaimCertainty,
    MemoirClaimEvidenceRole,
    MemoirClaimEvidenceSourceType,
    MemoirClaimPredicate,
    MemoirClaimPublicationStatus,
    MemoirClaimTimePrecision,
    MemoirClaimType,
    MemoirClaimVerificationStatus,
    MemoirDraftBlockClaimUsageRole,
    MemoirDraftBlockRole,
    MemoirDraftBlockStoryUsageRole,
    MemoirNarrativeActorType,
    MemoirNarrativeEntityType,
    MemoirNarrativeEntityVerificationStatus,
    MemoirStoryLifecycleStatus,
    MemoirStoryNarrativeRole,
    MemoirStoryPublicationStatus,
    MemoirStoryRevisionChangeType,
    MemoirStoryType,
    MemoirStoryVerificationStatus,
)
from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirBookPlanStatus,
    MemoirOutputStatus,
)
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirBookOutput,
    MemoirBookPlanChapter,
)
from app.models.memoir_narrative import (
    MemoirChapterContract,
    MemoirChapterNode,
    MemoirChapterNodeStory,
    MemoirClaim,
    MemoirClaimEvidence,
    MemoirStory,
    MemoirStoryClaim,
    MemoirStoryRevision,
)
from app.schemas.memoir_narrative import (
    MemoirDraftBlockClaimSource,
    MemoirDraftBlockStorySource,
)
from app.services.memoir.editing_job_handlers import register_memoir_editing_job_handlers
from app.services.memoir.narrative import chapter_contracts, draft_blocks
from app.services.memoir.narrative.book_consistency_semantic import (
    evaluate_book_semantic_gaps,
)
from app.services.memoir.narrative.book_consistency_context import (
    BookConsistencyContext,
    book_consistency_signature,
)
from app.services.memoir.narrative.book_consistency_rules import (
    evaluate_book_consistency_rules,
)
from tests.memoir_helpers import (
    create_test_book_plan_with_chapters,
    ensure_mobile_memoir_project,
    run_test_editing_job,
)


class EmptySemanticAuditAI:
    provider_name = "test-provider"
    model_name = "test-model"

    async def _call_llm(self, *_args, **_kwargs):
        return json.dumps({"schema_version": "1", "gaps": []})


class InvalidSemanticAuditAI(EmptySemanticAuditAI):
    async def _call_llm(self, *_args, **_kwargs):
        return json.dumps(
            {
                "schema_version": "1",
                "gaps": [
                    {
                        "gap_type": "repetition",
                        "severity": "medium",
                        "dimension": "repetition",
                        "title": "可能重复",
                        "description": "两个片段可能重复。",
                        "story_ids": ["story-outside-project"],
                        "claim_ids": [],
                        "chapter_ids": ["chapter-0"],
                    }
                ],
            }
        )


class StorySemanticAuditAI(EmptySemanticAuditAI):
    async def _call_llm(self, *_args, **_kwargs):
        return json.dumps(
            {
                "schema_version": "1",
                "gaps": [
                    {
                        "gap_type": "repetition",
                        "severity": "medium",
                        "dimension": "repetition",
                        "title": "可能重复",
                        "description": "这个故事在本章中可能重复出现。",
                        "story_ids": ["story-1"],
                        "claim_ids": [],
                        "chapter_ids": ["chapter-0"],
                    }
                ],
            }
        )


class OutOfScopeWindowSemanticAuditAI(EmptySemanticAuditAI):
    def __init__(self):
        self.call_count = 0

    async def _call_llm(self, *_args, **_kwargs):
        self.call_count += 1
        if self.call_count == 1:
            return json.dumps({"schema_version": "1", "gaps": []})
        return json.dumps(
            {
                "schema_version": "1",
                "gaps": [
                    {
                        "gap_type": "repetition",
                        "severity": "medium",
                        "dimension": "repetition",
                        "title": "越界引用",
                        "description": "引用了当前分片没有提供的故事。",
                        "story_ids": ["story-4"],
                        "claim_ids": [],
                        "chapter_ids": ["chapter-4"],
                    }
                ],
            }
        )


async def _create_ready_structured_book(
    client,
    test_db,
    *,
    plan_status: MemoirBookPlanStatus = MemoirBookPlanStatus.CONFIRMED,
    include_hidden_chapter: bool = False,
):
    project_id = await ensure_mobile_memoir_project(client)
    user_id = (await client.get("/api/auth/me")).json()["id"]
    plan, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["第一次离家"],
        status=plan_status,
    )
    chapter = chapters[0]
    if include_hidden_chapter:
        test_db.add(
            MemoirBookPlanChapter(
                plan_id=plan.id,
                project_id=project_id,
                title="保留但不收入本书的补记",
                sort_order=1,
                include_in_book=False,
                source_refs_json=[],
                metadata_json={},
            )
        )
        await test_db.flush()
    contract = MemoirChapterContract(
        project_id=project_id,
        chapter_id=chapter.id,
        version=1,
        objective="写清第一次离家的经过与影响。",
        reader_takeaway="理解这次经历如何改变了讲述者。",
        core_questions_json=[],
        constraints_json={
            "include_story_ids": [],
            "exclude_story_ids": [],
            "include_topics": [],
            "exclude_topics": [],
        },
        status=MemoirChapterContractStatus.DRAFT.value,
        created_by=MemoirNarrativeActorType.USER.value,
        created_by_user_id=user_id,
    )
    test_db.add(contract)
    await test_db.flush()
    node = MemoirChapterNode(
        project_id=project_id,
        chapter_id=chapter.id,
        contract_id=contract.id,
        node_type=MemoirChapterNodeType.STORY.value,
        title="第一次离家",
        purpose="按事件过程展开。",
        sort_order=0,
        required=True,
        status=MemoirChapterNodeStatus.READY.value,
        created_by=MemoirNarrativeActorType.USER.value,
        created_by_user_id=user_id,
        updated_by=MemoirNarrativeActorType.USER.value,
        updated_by_user_id=user_id,
    )
    story = MemoirStory(
        project_id=project_id,
        title="第一次离家",
        title_normalized="第一次离家",
        story_type=MemoirStoryType.EVENT.value,
        summary="十八岁时去成都读书。",
        time_start=datetime(1978, 9, 1),
        time_end=datetime(1978, 9, 2),
        time_precision=MemoirClaimTimePrecision.DAY.value,
        lifecycle_status=MemoirStoryLifecycleStatus.ACTIVE.value,
        verification_status=MemoirStoryVerificationStatus.CONFIRMED.value,
        verification_decided_by_user_id=user_id,
        publication_status=MemoirStoryPublicationStatus.ELIGIBLE.value,
        created_by=MemoirNarrativeActorType.USER.value,
        created_by_user_id=user_id,
        updated_by=MemoirNarrativeActorType.USER.value,
        updated_by_user_id=user_id,
    )
    claim = MemoirClaim(
        project_id=project_id,
        claim_type=MemoirClaimType.EVENT.value,
        statement="十八岁那年，我去成都读书。",
        predicate_code=MemoirClaimPredicate.NARRATIVE_FACT.value,
        time_start=datetime(1978, 9, 1),
        time_end=datetime(1978, 9, 2),
        time_precision=MemoirClaimTimePrecision.DAY.value,
        certainty=MemoirClaimCertainty.EXACT.value,
        verification_status=MemoirClaimVerificationStatus.CONFIRMED.value,
        publication_status=MemoirClaimPublicationStatus.ELIGIBLE.value,
        created_by=MemoirNarrativeActorType.USER.value,
        created_by_user_id=user_id,
        updated_by=MemoirNarrativeActorType.USER.value,
        updated_by_user_id=user_id,
    )
    test_db.add_all([node, story, claim])
    await test_db.flush()
    revision = MemoirStoryRevision(
        project_id=project_id,
        story_id=story.id,
        story_version=1,
        change_type=MemoirStoryRevisionChangeType.CREATED.value,
        before_state_json={},
        after_state_json={"summary": story.summary},
        reason="test fixture",
        created_by=MemoirNarrativeActorType.USER.value,
        created_by_user_id=user_id,
    )
    test_db.add_all(
        [
            MemoirChapterNodeStory(
                project_id=project_id,
                node_id=node.id,
                story_id=story.id,
                story_role=MemoirChapterNodeStoryRole.PRIMARY.value,
                sort_order=0,
                required=True,
                created_by=MemoirNarrativeActorType.USER.value,
                created_by_user_id=user_id,
            ),
            MemoirStoryClaim(
                project_id=project_id,
                story_id=story.id,
                claim_id=claim.id,
                narrative_role=MemoirStoryNarrativeRole.ACTION.value,
                sort_order=0,
                is_core=True,
                created_by=MemoirNarrativeActorType.USER.value,
                created_by_user_id=user_id,
            ),
            MemoirClaimEvidence(
                project_id=project_id,
                claim_id=claim.id,
                evidence_role=MemoirClaimEvidenceRole.SUPPORTS.value,
                evidence_source_type=MemoirClaimEvidenceSourceType.MANUAL.value,
                evidence_note="讲述者本人核对确认。",
                created_by=MemoirNarrativeActorType.USER.value,
                created_by_user_id=user_id,
            ),
            revision,
        ]
    )
    await test_db.flush()
    await chapter_contracts.confirm_contract(
        test_db,
        project_id=project_id,
        contract_id=contract.id,
        actor_user_id=user_id,
    )
    block = await draft_blocks.create_block(
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
        node_id=node.id,
        block_role=MemoirDraftBlockRole.FACTUAL,
        sort_order=0,
        content="十八岁那年，我第一次离开家乡，到成都读书。",
        created_by=MemoirNarrativeActorType.USER,
        claims=[
            MemoirDraftBlockClaimSource(
                claim_id=claim.id,
                usage_role=MemoirDraftBlockClaimUsageRole.CORE,
            )
        ],
        stories=[
            MemoirDraftBlockStorySource(
                story_id=story.id,
                story_revision_id=revision.id,
                usage_role=MemoirDraftBlockStoryUsageRole.PRIMARY,
            )
        ],
    )
    await test_db.commit()
    assembled_chapter = await client.post(
        f"/api/memoir/projects/{project_id}/chapters/{chapter.id}/draft-blocks/assemble"
    )
    assert assembled_chapter.status_code == 200, assembled_chapter.text
    draft = await test_db.scalar(
        select(MemoirBookChapterDraft).where(
            MemoirBookChapterDraft.chapter_id == chapter.id
        )
    )
    assert draft is not None
    draft.status = MemoirBookChapterDraftStatus.CONFIRMED.value
    test_db.add(draft)
    await test_db.commit()
    assembled_book = await client.post(
        f"/api/memoir/projects/{project_id}/book-output/assemble-from-draft-blocks"
    )
    if plan_status != MemoirBookPlanStatus.CONFIRMED:
        assert assembled_book.status_code == 409, assembled_book.text
        assert assembled_book.json()["detail"] == "请先确认全书章节目录"
        return project_id, user_id, block
    assert assembled_book.status_code == 200, assembled_book.text
    return project_id, user_id, block


async def test_structured_book_requires_a_confirmed_plan(client, test_db):
    project_id, _, _ = await _create_ready_structured_book(
        client,
        test_db,
        plan_status=MemoirBookPlanStatus.DRAFT,
    )

    output = await test_db.scalar(
        select(MemoirBookOutput).where(MemoirBookOutput.project_id == project_id)
    )
    assert output is None


def test_explicit_story_time_reversal_is_a_high_book_gap():
    earlier = SimpleNamespace(
        id="story-later-in-time",
        title="改革开放以后",
        time_start=datetime(1990, 1, 1),
        time_end=datetime(1990, 12, 31),
        time_precision=MemoirClaimTimePrecision.YEAR.value,
        lifecycle_status=MemoirStoryLifecycleStatus.ACTIVE.value,
        publication_status=MemoirStoryPublicationStatus.ELIGIBLE.value,
        verification_status=MemoirStoryVerificationStatus.CONFIRMED.value,
    )
    later = SimpleNamespace(
        id="story-earlier-in-time",
        title="少年时期",
        time_start=datetime(1960, 1, 1),
        time_end=datetime(1960, 12, 31),
        time_precision=MemoirClaimTimePrecision.YEAR.value,
        lifecycle_status=MemoirStoryLifecycleStatus.ACTIVE.value,
        publication_status=MemoirStoryPublicationStatus.ELIGIBLE.value,
        verification_status=MemoirStoryVerificationStatus.CONFIRMED.value,
    )
    chapters = []
    for index, story in enumerate((earlier, later)):
        node = SimpleNamespace(id=f"node-{index}", sort_order=0)
        link = SimpleNamespace(
            node_id=node.id,
            story_id=story.id,
            story_role=MemoirChapterNodeStoryRole.PRIMARY.value,
            sort_order=0,
        )
        chapters.append(
            SimpleNamespace(
                chapter=SimpleNamespace(id=f"chapter-{index}"),
                nodes=[node],
                links=[link],
                stories={story.id: story},
                story_claims={},
            )
        )
    context = BookConsistencyContext(
        plan=None,
        chapters=tuple(chapters),
        blocks=(),
        output=None,
        output_version=None,
        claims={},
        evidence_counts={},
        entities={},
    )

    candidates = evaluate_book_consistency_rules(context)

    chronology = [item for item in candidates if item.gap_type.value == "chronology"]
    assert len(chronology) == 1
    assert chronology[0].severity.value == "high"
    assert chronology[0].involved_chapter_ids == {
        "chapter-0",
        "chapter-1",
    }


async def test_semantic_book_audit_rejects_ids_outside_its_evidence_context():
    context = BookConsistencyContext(
        plan=None,
        chapters=(
            SimpleNamespace(
                chapter=SimpleNamespace(id="chapter-0", title="童年"),
                contract=None,
                nodes=[],
                links=[],
                stories={},
                story_claims={},
            ),
        ),
        blocks=(),
        output=None,
        output_version=None,
        claims={},
        evidence_counts={},
        entities={},
    )

    with pytest.raises(ValueError, match="evidence schema"):
        await evaluate_book_semantic_gaps(context, ai_client=InvalidSemanticAuditAI())


async def test_semantic_book_audit_accepts_story_chapter_relation_without_claims():
    story = SimpleNamespace(
        id="story-1",
        title="第一次离家",
        summary="第一次离家去成都读书。",
        time_start=None,
        time_end=None,
    )
    context = BookConsistencyContext(
        plan=None,
        chapters=(
            SimpleNamespace(
                chapter=SimpleNamespace(id="chapter-0", title="求学"),
                contract=None,
                nodes=[],
                links=[],
                stories={story.id: story},
                story_claims={},
            ),
        ),
        blocks=(),
        output=None,
        output_version=None,
        claims={},
        evidence_counts={},
        entities={},
    )

    candidates, _, _ = await evaluate_book_semantic_gaps(
        context,
        ai_client=StorySemanticAuditAI(),
    )

    assert len(candidates) == 1
    assert candidates[0].involved_story_ids == {"story-1"}
    assert candidates[0].involved_chapter_ids == {"chapter-0"}


async def test_semantic_book_audit_rejects_evidence_not_sent_in_current_window():
    chapters = []
    for index in range(5):
        story = SimpleNamespace(
            id=f"story-{index}",
            title=f"故事 {index}",
            summary="章节故事。",
            time_start=None,
            time_end=None,
        )
        chapters.append(
            SimpleNamespace(
                chapter=SimpleNamespace(id=f"chapter-{index}", title=f"章节 {index}"),
                contract=None,
                nodes=[],
                links=[],
                stories={story.id: story},
                story_claims={},
            )
        )
    context = BookConsistencyContext(
        plan=None,
        chapters=tuple(chapters),
        blocks=(),
        output=None,
        output_version=None,
        claims={},
        evidence_counts={},
        entities={},
    )

    with pytest.raises(ValueError, match="evidence schema"):
        await evaluate_book_semantic_gaps(
            context,
            ai_client=OutOfScopeWindowSemanticAuditAI(),
        )


def test_entity_changes_invalidate_book_consistency_signature():
    entity = SimpleNamespace(
        id="entity-1",
        entity_type=MemoirNarrativeEntityType.PERSON.value,
        canonical_name="大舅",
        verification_status=MemoirNarrativeEntityVerificationStatus.UNREVIEWED.value,
        merged_into_entity_id=None,
        updated_at=datetime(2026, 7, 13),
    )
    context = BookConsistencyContext(
        plan=None,
        chapters=(),
        blocks=(),
        output=None,
        output_version=None,
        claims={},
        evidence_counts={},
        entities={entity.id: entity},
    )
    before = book_consistency_signature(context)

    entity.verification_status = MemoirNarrativeEntityVerificationStatus.CONFIRMED.value

    assert book_consistency_signature(context) != before
