from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha256

import pytest
from sqlalchemy import func, select

from app.domain.memoir.narrative_types import (
    MemoirClaimCertainty,
    MemoirClaimEvidenceRole,
    MemoirClaimEvidenceSourceType,
    MemoirClaimPredicate,
    MemoirClaimPublicationStatus,
    MemoirClaimTimePrecision,
    MemoirClaimType,
    MemoirClaimVerificationStatus,
    MemoirCompletionItemStatus,
    MemoirFrameworkMatchSource,
    MemoirNarrativeActorType,
    MemoirNarrativeDecisionAction,
    MemoirNarrativeDecisionTargetType,
    MemoirNarrativeGapDetectedBy,
    MemoirNarrativeGapRecommendedAction,
    MemoirNarrativeGapScopeType,
    MemoirNarrativeGapSeverity,
    MemoirNarrativeGapType,
    MemoirNarrativeEntityType,
    MemoirNarrativeEntityVerificationStatus,
    MemoirStoryLifecycleStatus,
    MemoirStoryNarrativeRole,
    MemoirStoryPublicationStatus,
    MemoirStoryType,
    MemoirStoryVerificationStatus,
)
from app.domain.memoir.types import (
    MemoirAutomationEventStatus,
    MemoirAutomationEventType,
    MemoirBookChapterDraftStatus,
    MemoirEditingJobStatus,
    MemoirEditingJobType,
    MemoirVoiceSessionStatus,
)
from app.models.memoir import (
    MemoirAutomationEvent,
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirBookQualityAuditSnapshot,
    MemoirChapterCard,
    MemoirEditingJob,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.models.memoir_narrative import (
    MemoirClaim,
    MemoirClaimEvidence,
    MemoirCompletionItem,
    MemoirFrameworkItemClaim,
    MemoirNarrativeDecision,
    MemoirNarrativeEntity,
    MemoirStory,
    MemoirStoryClaim,
)
from app.services.memoir.narrative import frameworks
from app.services.memoir.narrative.project_rebuild import (
    NarrativeProjectRebuildError,
    finalize_project_narrative_rebuild,
    get_project_narrative_rebuild_status,
    preview_project_narrative_rebuild,
    start_project_narrative_rebuild,
)
from tests.memoir_helpers import (
    create_test_book_plan_with_chapters,
    create_test_voice_transcript,
    ensure_mobile_memoir_project,
    memoir_source_ref,
)


async def _count(test_db, model, *filters) -> int:
    return int(
        await test_db.scalar(
            select(func.count()).select_from(model).where(*filters)
        )
        or 0
    )


async def _stop_source_session(test_db, source: dict) -> MemoirVoiceSession:
    session = await test_db.get(MemoirVoiceSession, source["session"]["id"])
    assert session is not None
    session.status = MemoirVoiceSessionStatus.STOPPED.value
    session.stopped_at = datetime.utcnow()
    test_db.add(session)
    await test_db.commit()
    return session


async def _seed_ai_derived_state(
    test_db,
    *,
    project_id: str,
    source: dict,
    chapter,
    plan,
) -> dict:
    entity = MemoirNarrativeEntity(
        project_id=project_id,
        entity_type=MemoirNarrativeEntityType.PERSON.value,
        canonical_name="外婆",
        aliases_json=[],
        description="",
        verification_status=MemoirNarrativeEntityVerificationStatus.UNREVIEWED.value,
        created_by=MemoirNarrativeActorType.AI.value,
        updated_by=MemoirNarrativeActorType.AI.value,
    )
    story = MemoirStory(
        project_id=project_id,
        title="外婆家的童年",
        title_normalized="外婆家的童年",
        story_type=MemoirStoryType.EVENT.value,
        summary="小时候住在外婆家。",
        time_precision=MemoirClaimTimePrecision.UNKNOWN.value,
        lifecycle_status=MemoirStoryLifecycleStatus.ACTIVE.value,
        verification_status=MemoirStoryVerificationStatus.UNREVIEWED.value,
        publication_status=MemoirStoryPublicationStatus.ELIGIBLE.value,
        created_by=MemoirNarrativeActorType.AI.value,
        updated_by=MemoirNarrativeActorType.AI.value,
    )
    claim = MemoirClaim(
        project_id=project_id,
        claim_type=MemoirClaimType.EVENT.value,
        statement="小时候住在外婆家。",
        predicate_code=MemoirClaimPredicate.NARRATIVE_FACT.value,
        time_precision=MemoirClaimTimePrecision.UNKNOWN.value,
        certainty=MemoirClaimCertainty.EXACT.value,
        verification_status=MemoirClaimVerificationStatus.UNREVIEWED.value,
        publication_status=MemoirClaimPublicationStatus.ELIGIBLE.value,
        created_by=MemoirNarrativeActorType.AI.value,
        updated_by=MemoirNarrativeActorType.AI.value,
    )
    test_db.add_all([entity, story, claim])
    await test_db.flush()
    utterance = await test_db.get(MemoirVoiceUtterance, source["utterance_id"])
    assert utterance is not None
    quote = utterance.transcript_edited
    evidence = MemoirClaimEvidence(
        project_id=project_id,
        claim_id=claim.id,
        evidence_role=MemoirClaimEvidenceRole.SUPPORTS.value,
        evidence_source_type=MemoirClaimEvidenceSourceType.TRANSCRIPT.value,
        utterance_id=utterance.id,
        transcript_version_id=source["transcript_version_id"],
        audio_file_id=source["audio_file_id"],
        quote=quote,
        source_text_hash=sha256(quote.encode("utf-8")).hexdigest(),
        created_by=MemoirNarrativeActorType.AI.value,
    )
    story_claim = MemoirStoryClaim(
        project_id=project_id,
        story_id=story.id,
        claim_id=claim.id,
        narrative_role=MemoirStoryNarrativeRole.ACTION.value,
        sort_order=0,
        is_core=True,
        created_by=MemoirNarrativeActorType.AI.value,
    )
    decision = MemoirNarrativeDecision(
        project_id=project_id,
        target_type=MemoirNarrativeDecisionTargetType.STORY.value,
        target_id=story.id,
        action=MemoirNarrativeDecisionAction.NEEDS_REVIEW.value,
        actor_type=MemoirNarrativeActorType.AI.value,
        before_state_json={},
        after_state_json={"verification_status": "needs_verification"},
        reason="AI 抽取",
        idempotency_key=f"test:{story.id}",
    )
    active_item = MemoirCompletionItem(
        project_id=project_id,
        scope_type=MemoirNarrativeGapScopeType.CHAPTER.value,
        chapter_id=chapter.id,
        story_id=story.id,
        claim_id=claim.id,
        item_type=MemoirNarrativeGapType.MISSING_SLOT.value,
        severity=MemoirNarrativeGapSeverity.MEDIUM.value,
        dimension="opening",
        title="补充开场",
        description="",
        fingerprint="a" * 64,
        action=MemoirNarrativeGapRecommendedAction.INTERVIEW.value,
        question="最先想到哪一幕？",
        status=MemoirCompletionItemStatus.OPEN.value,
        detected_by=MemoirNarrativeGapDetectedBy.AI.value,
    )
    answered_item = MemoirCompletionItem(
        project_id=project_id,
        scope_type=MemoirNarrativeGapScopeType.CHAPTER.value,
        chapter_id=chapter.id,
        story_id=story.id,
        claim_id=claim.id,
        item_type=MemoirNarrativeGapType.MISSING_SLOT.value,
        severity=MemoirNarrativeGapSeverity.MEDIUM.value,
        dimension="chapter_context",
        title="补充背景",
        description="",
        fingerprint="b" * 64,
        action=MemoirNarrativeGapRecommendedAction.INTERVIEW.value,
        question="还需要知道哪些背景？",
        status=MemoirCompletionItemStatus.RESOLVED.value,
        outcome_note="用户已经补充",
        detected_by=MemoirNarrativeGapDetectedBy.AI.value,
        completed_at=datetime.utcnow(),
    )
    test_db.add_all(
        [
            evidence,
            story_claim,
            decision,
            active_item,
            answered_item,
        ]
    )
    await test_db.flush()
    utterance.completion_item_id = answered_item.id
    test_db.add(utterance)

    project_framework = await frameworks.initialize_project_framework(
        test_db,
        project_id=project_id,
    )
    framework_link = MemoirFrameworkItemClaim(
        framework_item_id=project_framework.items[0].id,
        claim_id=claim.id,
        evidence_role=MemoirClaimEvidenceRole.SUPPORTS.value,
        match_source=MemoirFrameworkMatchSource.EVALUATOR.value,
        matcher_version="test",
        confidence=0.9,
    )
    chapter_card = MemoirChapterCard(
        project_id=project_id,
        plan_id=plan.id,
        chapter_id=chapter.id,
        input_signature="old-chapter-signature",
        engine="test",
        card_json={},
    )
    audit_snapshot = MemoirBookQualityAuditSnapshot(
        project_id=project_id,
        engine="test",
        input_signature="old-book-signature",
    )
    event = MemoirAutomationEvent(
        project_id=project_id,
        chapter_id=chapter.id,
        event_type=MemoirAutomationEventType.AUDIT_COMPLETED.value,
        status=MemoirAutomationEventStatus.COMPLETED.value,
        summary_text="旧补全检查",
        metadata_json={"source": "chapter_completion_refresh"},
    )
    test_db.add_all([framework_link, chapter_card, audit_snapshot, event])
    await test_db.commit()
    return {
        "entity_id": entity.id,
        "story_id": story.id,
        "claim_id": claim.id,
        "active_item_id": active_item.id,
        "answered_item_id": answered_item.id,
        "event_id": event.id,
    }


@pytest.mark.asyncio
async def test_rebuild_resets_only_ai_derived_state_and_enqueues_sources(
    client,
    test_db,
):
    project_id = await ensure_mobile_memoir_project(client, title="写给他的话")
    source = await create_test_voice_transcript(
        client,
        test_db,
        project_id=project_id,
        text="小时候我住在外婆家，那是我最安心的一段日子。",
    )
    await _stop_source_session(test_db, source)
    plan, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["外婆家的童年"],
        source_refs_by_chapter=[[memoir_source_ref(source)]],
        draft_contents=["原有正文必须保留。"],
    )
    seeded = await _seed_ai_derived_state(
        test_db,
        project_id=project_id,
        source=source,
        chapter=chapters[0],
        plan=plan,
    )

    preview = await preview_project_narrative_rebuild(
        test_db,
        project_id=project_id,
    )
    assert preview.derived_counts["claims"] == 1
    assert preview.source_utterance_count == 1
    assert preview.rebuildable_utterance_count == 1
    assert preview.blocker_count == 0

    result = await start_project_narrative_rebuild(
        test_db,
        project_id=project_id,
        confirm_project_title="写给他的话",
    )
    await test_db.commit()

    assert len(result.ingestion_job_ids) == 1
    job = await test_db.get(MemoirEditingJob, result.ingestion_job_ids[0])
    assert job is not None
    assert job.status == MemoirEditingJobStatus.PENDING.value
    assert job.payload_json["rebuild_run_id"] == result.run_id
    assert job.dedupe_key.startswith(f"rebuild_narrative:{result.run_id}:")

    assert await _count(
        test_db, MemoirClaim, MemoirClaim.project_id == project_id
    ) == 0
    assert await _count(
        test_db, MemoirClaimEvidence, MemoirClaimEvidence.project_id == project_id
    ) == 0
    assert await _count(
        test_db, MemoirStory, MemoirStory.project_id == project_id
    ) == 0
    assert await _count(
        test_db,
        MemoirNarrativeEntity,
        MemoirNarrativeEntity.project_id == project_id,
    ) == 0
    assert await _count(
        test_db,
        MemoirFrameworkItemClaim,
    ) == 0
    assert await _count(
        test_db, MemoirChapterCard, MemoirChapterCard.project_id == project_id
    ) == 0
    assert await _count(
        test_db,
        MemoirBookQualityAuditSnapshot,
        MemoirBookQualityAuditSnapshot.project_id == project_id,
    ) == 0

    assert await test_db.get(MemoirCompletionItem, seeded["active_item_id"]) is None
    answered = await test_db.get(
        MemoirCompletionItem, seeded["answered_item_id"]
    )
    assert answered is not None
    assert answered.story_id is None
    assert answered.claim_id is None
    assert answered.outcome_note == "用户已经补充"
    preserved_utterance = await test_db.get(
        MemoirVoiceUtterance, source["utterance_id"]
    )
    assert preserved_utterance is not None
    assert preserved_utterance.completion_item_id == answered.id

    draft = await test_db.scalar(
        select(MemoirBookChapterDraft).where(
            MemoirBookChapterDraft.chapter_id == chapters[0].id
        )
    )
    assert draft is not None
    assert draft.status == MemoirBookChapterDraftStatus.DRAFT.value
    assert draft.metadata_json["status_reason"] == "narrative_state_rebuilt"
    assert await _count(
        test_db,
        MemoirBookChapterDraftVersion,
        MemoirBookChapterDraftVersion.chapter_id == chapters[0].id,
    ) == 1

    event = await test_db.get(MemoirAutomationEvent, seeded["event_id"])
    assert event is not None
    assert event.metadata_json["resolution"] == "confirmed"
    assert event.metadata_json["resolution_reason"] == "narrative_state_rebuilt"


@pytest.mark.asyncio
async def test_rebuild_rejects_user_semantic_decisions(client, test_db):
    project_id = await ensure_mobile_memoir_project(client, title="有人工确认")
    source = await create_test_voice_transcript(
        client,
        test_db,
        project_id=project_id,
        text="这是用户确认过的一段经历。",
    )
    await _stop_source_session(test_db, source)
    user_id = (await client.get("/api/auth/me")).json()["id"]
    claim = MemoirClaim(
        project_id=project_id,
        claim_type=MemoirClaimType.EVENT.value,
        statement="这是用户确认过的一段经历。",
        predicate_code=MemoirClaimPredicate.NARRATIVE_FACT.value,
        time_precision=MemoirClaimTimePrecision.UNKNOWN.value,
        certainty=MemoirClaimCertainty.EXACT.value,
        verification_status=MemoirClaimVerificationStatus.CONFIRMED.value,
        publication_status=MemoirClaimPublicationStatus.ELIGIBLE.value,
        created_by=MemoirNarrativeActorType.AI.value,
        updated_by=MemoirNarrativeActorType.USER.value,
        updated_by_user_id=user_id,
    )
    test_db.add(claim)
    await test_db.commit()

    preview = await preview_project_narrative_rebuild(
        test_db,
        project_id=project_id,
    )
    assert preview.user_decision_counts["claims"] == 1
    with pytest.raises(
        NarrativeProjectRebuildError,
        match="不能自动覆盖的用户决定",
    ):
        await start_project_narrative_rebuild(
            test_db,
            project_id=project_id,
            confirm_project_title="有人工确认",
        )
    assert await test_db.get(MemoirClaim, claim.id) is not None


@pytest.mark.asyncio
async def test_rebuild_requires_explicit_close_for_stale_active_session(
    client,
    test_db,
):
    project_id = await ensure_mobile_memoir_project(client, title="过期会话")
    source = await create_test_voice_transcript(
        client,
        test_db,
        project_id=project_id,
        text="这个会话已经很久没有活动了。",
    )
    session = await test_db.get(MemoirVoiceSession, source["session"]["id"])
    assert session is not None
    session.updated_at = datetime.utcnow() - timedelta(hours=2)
    test_db.add(session)
    await test_db.commit()

    with pytest.raises(NarrativeProjectRebuildError, match="活跃语音会话"):
        await start_project_narrative_rebuild(
            test_db,
            project_id=project_id,
            confirm_project_title="过期会话",
        )

    result = await start_project_narrative_rebuild(
        test_db,
        project_id=project_id,
        confirm_project_title="过期会话",
        close_active_sessions=True,
    )
    await test_db.commit()
    assert result.closed_session_ids == (session.id,)
    await test_db.refresh(session)
    assert session.status == MemoirVoiceSessionStatus.STOPPED.value
    assert session.metadata_json["narrative_rebuild_run_id"] == result.run_id


@pytest.mark.asyncio
async def test_finalize_waits_for_ingestion_and_tracks_completion_jobs(
    client,
    test_db,
):
    project_id = await ensure_mobile_memoir_project(client, title="重建收尾")
    source = await create_test_voice_transcript(
        client,
        test_db,
        project_id=project_id,
        text="这是一段可以重新抽取的讲述。",
    )
    await _stop_source_session(test_db, source)
    _, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["第一章"],
        source_refs_by_chapter=[[memoir_source_ref(source)]],
    )
    started = await start_project_narrative_rebuild(
        test_db,
        project_id=project_id,
        confirm_project_title="重建收尾",
    )
    await test_db.commit()

    with pytest.raises(NarrativeProjectRebuildError, match="尚未全部成功"):
        await finalize_project_narrative_rebuild(
            test_db,
            project_id=project_id,
            run_id=started.run_id,
        )

    ingestion_job = await test_db.get(
        MemoirEditingJob, started.ingestion_job_ids[0]
    )
    assert ingestion_job is not None
    ingestion_job.status = MemoirEditingJobStatus.SUCCEEDED.value
    test_db.add(ingestion_job)
    await test_db.commit()

    completion_job_ids = await finalize_project_narrative_rebuild(
        test_db,
        project_id=project_id,
        run_id=started.run_id,
    )
    await test_db.commit()
    assert len(completion_job_ids) == len(chapters)
    completion_job = await test_db.get(MemoirEditingJob, completion_job_ids[0])
    assert completion_job is not None
    assert (
        completion_job.job_type
        == MemoirEditingJobType.REFRESH_CHAPTER_COMPLETION.value
    )
    assert completion_job.payload_json["rebuild_run_id"] == started.run_id

    status = await get_project_narrative_rebuild_status(
        test_db,
        project_id=project_id,
        run_id=started.run_id,
    )
    assert status.ready_to_finalize is True
    assert status.completion_refresh["pending"] == 1
    assert status.complete is False
