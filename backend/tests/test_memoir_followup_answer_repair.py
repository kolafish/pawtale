from hashlib import sha256

import pytest
from sqlalchemy import select

from app.domain.memoir.narrative_types import (
    MemoirClaimCertainty,
    MemoirClaimEvidenceRole,
    MemoirClaimEvidenceSourceType,
    MemoirClaimPredicate,
    MemoirClaimPublicationStatus,
    MemoirClaimTimePrecision,
    MemoirClaimType,
    MemoirClaimVerificationStatus,
    MemoirFollowupAnswerKind,
    MemoirNarrativeActorType,
    MemoirStoryCompletionStatus,
    MemoirStoryLifecycleStatus,
    MemoirStoryPublicationStatus,
    MemoirStoryType,
    MemoirStoryVerificationStatus,
)
from app.domain.memoir.types import (
    MemoirAutomationEventStatus,
    MemoirAutomationEventType,
    MemoirBookChapterDraftStatus,
    MemoirChapterSourceLinkStatus,
)
from app.models.memoir import (
    MemoirAutomationEvent,
    MemoirBookChapterDraft,
    MemoirChapterSourceLink,
    MemoirSourceCard,
    MemoirVoiceUtterance,
)
from app.models.memoir_narrative import (
    MemoirClaim,
    MemoirClaimEvidence,
    MemoirFollowupTask,
    MemoirFollowupTaskUtterance,
    MemoirNarrativeGap,
    MemoirStory,
    MemoirStoryClaim,
    MemoirStorySlot,
    MemoirStorySlotClaim,
)
from app.services.memoir.followup_answer_repair import (
    ContextOnlyFollowupAnswerRepairSpec,
    FollowupAnswerRepairMismatch,
    apply_context_only_followup_answer_repair,
    inspect_context_only_followup_answer_repair,
)
from tests.memoir_helpers import (
    create_test_book_plan,
    create_test_chapter_draft,
    create_test_voice_transcript,
    memoir_source_ref,
)


async def _repair_fixture(client, test_db):
    original = await create_test_voice_transcript(client, test_db, "上午去看了爷爷，还给他买了西瓜。")
    project_id = original["session"]["project_id"]
    answer_text = "这句话是我当时想发给他的消息，但最后没有发出去。"
    answer = await create_test_voice_transcript(client, test_db, answer_text, project_id=project_id)
    plan, chapter = await create_test_book_plan(
        test_db,
        project_id=project_id,
        source_refs=[memoir_source_ref(original), memoir_source_ref(answer)],
        chapter_title="回成都后的疏离与克制",
    )
    await create_test_chapter_draft(
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
        content=f"上午去看了爷爷。{answer_text}",
    )

    gap = MemoirNarrativeGap(
        project_id=project_id,
        scope_type="chapter",
        primary_chapter_id=chapter.id,
        gap_type="causality",
        severity="medium",
        title="西瓜与章节主题的关系",
        description="需要解释这段内容为什么出现在章节中。",
        fingerprint=sha256(b"historical-followup-repair-gap").hexdigest(),
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
        question="这件事与本章是什么关系？",
        purpose="解释原文含义。",
        priority=100,
        status="deferred",
        defer_reason="历史链路未完成回答归类。",
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
        interpretation_status="pending",
    )
    test_db.add(relation)

    text_hash = sha256(answer_text.encode("utf-8")).hexdigest()
    source_card = MemoirSourceCard(
        project_id=project_id,
        utterance_id=answer["utterance_id"],
        transcript_version_id=answer["transcript_version_id"],
        source_text_hash=text_hash,
        engine="test-source-card",
        status="ready",
        card_json={"short_title": "未发出的消息"},
    )
    story = MemoirStory(
        project_id=project_id,
        title="未发出的消息",
        title_normalized="未发出的消息",
        story_type=MemoirStoryType.EVENT.value,
        summary="想发消息但没有发出去。",
        time_precision=MemoirClaimTimePrecision.UNKNOWN.value,
        lifecycle_status=MemoirStoryLifecycleStatus.ACTIVE.value,
        completion_status=MemoirStoryCompletionStatus.INCOMPLETE.value,
        verification_status=MemoirStoryVerificationStatus.NEEDS_VERIFICATION.value,
        publication_status=MemoirStoryPublicationStatus.ELIGIBLE.value,
        slot_schema_version="1",
        created_by=MemoirNarrativeActorType.AI.value,
        updated_by=MemoirNarrativeActorType.AI.value,
    )
    claim = MemoirClaim(
        project_id=project_id,
        claim_type=MemoirClaimType.EVENT.value,
        statement="叙述者想发消息但最终没有发出。",
        predicate_code=MemoirClaimPredicate.NARRATIVE_FACT.value,
        time_precision=MemoirClaimTimePrecision.UNKNOWN.value,
        certainty=MemoirClaimCertainty.EXACT.value,
        verification_status=MemoirClaimVerificationStatus.UNREVIEWED.value,
        publication_status=MemoirClaimPublicationStatus.ELIGIBLE.value,
        created_by=MemoirNarrativeActorType.AI.value,
        updated_by=MemoirNarrativeActorType.AI.value,
    )
    test_db.add_all([source_card, story, claim])
    await test_db.flush()
    evidence = MemoirClaimEvidence(
        project_id=project_id,
        claim_id=claim.id,
        evidence_role=MemoirClaimEvidenceRole.SUPPORTS.value,
        evidence_source_type=MemoirClaimEvidenceSourceType.TRANSCRIPT.value,
        utterance_id=answer["utterance_id"],
        transcript_version_id=answer["transcript_version_id"],
        audio_file_id=answer["audio_file_id"],
        quote=answer_text,
        source_text_hash=text_hash,
        created_by=MemoirNarrativeActorType.AI.value,
    )
    story_claim = MemoirStoryClaim(
        project_id=project_id,
        story_id=story.id,
        claim_id=claim.id,
        narrative_role="outcome",
        sort_order=0,
        is_core=True,
        created_by=MemoirNarrativeActorType.AI.value,
    )
    slot = MemoirStorySlot(
        project_id=project_id,
        story_id=story.id,
        schema_version="1",
        slot_key="outcome",
        status="partial",
        assessment_summary="supports=1",
    )
    test_db.add_all([evidence, story_claim, slot])
    await test_db.flush()
    test_db.add(
        MemoirStorySlotClaim(
            project_id=project_id,
            story_slot_id=slot.id,
            claim_id=claim.id,
            evidence_role=MemoirClaimEvidenceRole.SUPPORTS.value,
            created_by=MemoirNarrativeActorType.AI.value,
        )
    )
    event = MemoirAutomationEvent(
        project_id=project_id,
        chapter_id=chapter.id,
        event_type=MemoirAutomationEventType.SOURCE_ASSIGNED.value,
        status=MemoirAutomationEventStatus.COMPLETED.value,
        utterance_ids_json=[answer["utterance_id"]],
        summary_text="1 条讲述已自动归章。",
        metadata_json={"source": "auto_assignment"},
    )
    test_db.add(event)
    await test_db.flush()
    link = await test_db.scalar(
        select(MemoirChapterSourceLink).where(
            MemoirChapterSourceLink.project_id == project_id,
            MemoirChapterSourceLink.chapter_id == chapter.id,
            MemoirChapterSourceLink.utterance_id == answer["utterance_id"],
        )
    )
    link.source_card_id = source_card.id
    link.assigned_by = "system"
    link.assignment_reason = "legacy completion route"
    test_db.add(link)
    await test_db.commit()

    spec = ContextOnlyFollowupAnswerRepairSpec(
        project_id=project_id,
        task_id=task.id,
        utterance_id=answer["utterance_id"],
        chapter_id=chapter.id,
        source_link_id=link.id,
        source_card_id=source_card.id,
        source_assignment_event_id=event.id,
        expected_source_text=answer_text,
        answer_kind=MemoirFollowupAnswerKind.CLARIFICATION,
        expected_interpretation_status="pending",
        expected_task_status="deferred",
        expected_gap_status="open",
        derived_story_ids=(story.id,),
        derived_claim_ids=(claim.id,),
    )
    return spec, relation, task, gap, link, story, claim, source_card, event


async def test_context_only_followup_repair_removes_only_explicit_derived_graph(
    client,
    test_db,
):
    spec, relation, task, gap, link, story, claim, source_card, event = await _repair_fixture(
        client,
        test_db,
    )

    dry_run = await inspect_context_only_followup_answer_repair(test_db, spec=spec)
    assert dry_run.source_link_ids_to_remove == (link.id,)
    assert dry_run.story_ids_to_delete == (story.id,)
    assert dry_run.claim_ids_to_delete == (claim.id,)

    result = await apply_context_only_followup_answer_repair(test_db, spec=spec)
    await test_db.commit()

    await test_db.refresh(relation)
    await test_db.refresh(task)
    await test_db.refresh(gap)
    await test_db.refresh(link)
    await test_db.refresh(event)
    utterance = await test_db.get(MemoirVoiceUtterance, spec.utterance_id)
    assert relation.interpretation_status == "resolved"
    assert relation.answer_kind == "clarification"
    assert relation.clarification_text == spec.expected_source_text
    assert relation.result_role == "target_answer"
    assert task.status == "answered"
    assert gap.status == "resolved"
    assert utterance.source_route_action == "record_as_context"
    assert link.status == MemoirChapterSourceLinkStatus.REMOVED.value
    assert link.source_card_id is None
    assert await test_db.get(MemoirStory, story.id) is None
    assert await test_db.get(MemoirClaim, claim.id) is None
    assert await test_db.get(MemoirSourceCard, source_card.id) is None
    assert event.metadata_json["historical_repair"]["answer_kind"] == "clarification"
    assert event.metadata_json["acknowledged_at"]
    draft = await test_db.scalar(
        select(MemoirBookChapterDraft).where(
            MemoirBookChapterDraft.project_id == spec.project_id,
            MemoirBookChapterDraft.chapter_id == spec.chapter_id,
        )
    )
    assert draft is not None
    assert draft.status == MemoirBookChapterDraftStatus.DRAFT.value
    assert result.chapter_draft_invalidated is True


async def test_context_only_followup_repair_rejects_changed_source_text(
    client,
    test_db,
):
    spec, relation, task, gap, link, story, claim, source_card, _event = await _repair_fixture(
        client,
        test_db,
    )
    changed_spec = ContextOnlyFollowupAnswerRepairSpec(
        **{**spec.__dict__, "expected_source_text": "另一段原文"}
    )

    with pytest.raises(FollowupAnswerRepairMismatch, match="讲述原文与预期不一致"):
        await inspect_context_only_followup_answer_repair(test_db, spec=changed_spec)

    assert relation.interpretation_status == "pending"
    assert task.status == "deferred"
    assert gap.status == "open"
    assert link.status == MemoirChapterSourceLinkStatus.ACTIVE.value
    assert await test_db.get(MemoirStory, story.id) is not None
    assert await test_db.get(MemoirClaim, claim.id) is not None
    assert await test_db.get(MemoirSourceCard, source_card.id) is not None


async def test_context_only_followup_repair_rejects_mismatched_task_answer_use(
    client,
    test_db,
):
    spec, relation, task, gap, link, story, claim, source_card, _event = await _repair_fixture(
        client,
        test_db,
    )
    task.answer_use = "direct_narrative"
    test_db.add(task)
    await test_db.commit()

    with pytest.raises(FollowupAnswerRepairMismatch, match="预期用途与确认结果不一致"):
        await inspect_context_only_followup_answer_repair(test_db, spec=spec)

    assert relation.interpretation_status == "pending"
    assert task.status == "deferred"
    assert gap.status == "open"
    assert link.status == MemoirChapterSourceLinkStatus.ACTIVE.value
    assert await test_db.get(MemoirStory, story.id) is not None
    assert await test_db.get(MemoirClaim, claim.id) is not None
    assert await test_db.get(MemoirSourceCard, source_card.id) is not None


async def test_context_only_followup_repair_rejects_story_used_as_merge_target(
    client,
    test_db,
):
    spec, _relation, _task, _gap, _link, story, _claim, _source_card, _event = (
        await _repair_fixture(client, test_db)
    )
    merged_story = MemoirStory(
        project_id=spec.project_id,
        title="已合并的相关故事",
        title_normalized="已合并的相关故事",
        story_type=MemoirStoryType.EVENT.value,
        summary="依赖待删除 Story 的合并记录。",
        time_precision=MemoirClaimTimePrecision.UNKNOWN.value,
        lifecycle_status=MemoirStoryLifecycleStatus.MERGED.value,
        completion_status=MemoirStoryCompletionStatus.INCOMPLETE.value,
        verification_status=MemoirStoryVerificationStatus.NEEDS_VERIFICATION.value,
        publication_status=MemoirStoryPublicationStatus.ELIGIBLE.value,
        slot_schema_version="1",
        merged_into_story_id=story.id,
        created_by=MemoirNarrativeActorType.AI.value,
        updated_by=MemoirNarrativeActorType.AI.value,
    )
    test_db.add(merged_story)
    await test_db.commit()

    with pytest.raises(FollowupAnswerRepairMismatch, match="Story 已被其他业务数据引用"):
        await inspect_context_only_followup_answer_repair(test_db, spec=spec)

    assert await test_db.get(MemoirStory, story.id) is not None
    assert await test_db.get(MemoirStory, merged_story.id) is not None
