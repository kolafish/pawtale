from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256

from sqlalchemy import func, select

from app.domain.memoir.narrative_types import (
    MEMOIR_STORY_NARRATION_TARGET,
    MemoirChapterContractStatus,
    MemoirChapterNodeStatus,
    MemoirChapterNodeStoryRole,
    MemoirChapterNodeType,
    MemoirClaimCertainty,
    MemoirClaimEvidenceRole,
    MemoirClaimEvidenceSourceType,
    MemoirClaimEvidenceSpeakerRole,
    MemoirClaimPredicate,
    MemoirClaimPublicationStatus,
    MemoirClaimTimePrecision,
    MemoirClaimType,
    MemoirClaimVerificationStatus,
    MemoirFollowupAnswerInterpretationStatus,
    MemoirFollowupAnswerUse,
    MemoirFollowupTaskStatus,
    MemoirNarrativeActorType,
    MemoirNarrativeGapDetectedBy,
    MemoirNarrativeGapRelationRole,
    MemoirNarrativeGapScopeType,
    MemoirNarrativeGapSeverity,
    MemoirNarrativeGapStatus,
    MemoirNarrativeGapType,
    MemoirStoryCompletionStatus,
    MemoirStoryLifecycleStatus,
    MemoirStoryNarrativeRole,
    MemoirStoryPublicationStatus,
    MemoirStorySlotStatus,
    MemoirStoryType,
    MemoirStoryVerificationStatus,
)
from app.domain.memoir.types import (
    MemoirChatContentStatus,
    MemoirChatMessageRole,
    MemoirCreatedBy,
    MemoirEditingJobType,
    MemoirTranscriptVersionType,
)
from app.models.memoir import (
    MemoirBookPlanChapter,
    MemoirChatMessage,
    MemoirEditingJob,
    MemoirTranscriptVersion,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.models.memoir_narrative import (
    MemoirChapterContract,
    MemoirChapterNode,
    MemoirChapterNodeStory,
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
from app.schemas.memoir_narrative import MemoirNarrativeGapCandidate
from app.services.memoir.editing_job_handlers import register_memoir_editing_job_handlers
from app.services.memoir.asr import StreamingASREvent
from app.services.memoir.narrative import (
    followup_completion,
    followup_planner,
    followup_tasks,
    gaps,
    story_slots,
)
from app.services.memoir.voice_final_persistence import VoiceFinalPersistenceService
from tests.memoir_helpers import (
    create_test_book_plan_with_chapters,
    create_test_voice_transcript,
    post_mobile_memoir_voice_session,
    run_test_editing_job,
)


async def _create_story_gap(client, test_db, *, generate: bool = True):
    created = await create_test_voice_transcript(
        client,
        test_db,
        text="那次比赛结束后，我们一起走回学校。",
    )
    project_id = created["session"]["project_id"]
    user_id = created["user_id"]
    _, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["学校里的比赛"],
    )
    chapter = chapters[0]
    story = MemoirStory(
        project_id=project_id,
        title="学校里的比赛",
        title_normalized="学校里的比赛",
        story_type=MemoirStoryType.EVENT.value,
        summary="讲述者参加了一次学校比赛，但结果还不完整。",
        time_precision=MemoirClaimTimePrecision.UNKNOWN.value,
        lifecycle_status=MemoirStoryLifecycleStatus.ACTIVE.value,
        completion_status=MemoirStoryCompletionStatus.INCOMPLETE.value,
        verification_status=MemoirStoryVerificationStatus.UNREVIEWED.value,
        publication_status=MemoirStoryPublicationStatus.ELIGIBLE.value,
        slot_schema_version="1",
        created_by=MemoirNarrativeActorType.USER.value,
        created_by_user_id=user_id,
        updated_by=MemoirNarrativeActorType.USER.value,
        updated_by_user_id=user_id,
    )
    claim = MemoirClaim(
        project_id=project_id,
        claim_type=MemoirClaimType.EVENT.value,
        statement="比赛结束后大家一起走回学校。",
        predicate_code=MemoirClaimPredicate.NARRATIVE_FACT.value,
        time_precision=MemoirClaimTimePrecision.UNKNOWN.value,
        certainty=MemoirClaimCertainty.EXACT.value,
        verification_status=MemoirClaimVerificationStatus.UNREVIEWED.value,
        publication_status=MemoirClaimPublicationStatus.ELIGIBLE.value,
        created_by=MemoirNarrativeActorType.USER.value,
        created_by_user_id=user_id,
        updated_by=MemoirNarrativeActorType.USER.value,
        updated_by_user_id=user_id,
    )
    test_db.add_all([story, claim])
    await test_db.flush()
    contract = MemoirChapterContract(
        project_id=project_id,
        chapter_id=chapter.id,
        version=1,
        objective="讲清楚这次比赛的完整经过和影响。",
        reader_takeaway="读者能理解这次比赛为什么重要。",
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
        title="比赛经过",
        purpose="写清楚比赛如何发生以及最后结果。",
        sort_order=0,
        required=True,
        status=MemoirChapterNodeStatus.READY.value,
        created_by=MemoirNarrativeActorType.USER.value,
        created_by_user_id=user_id,
        updated_by=MemoirNarrativeActorType.USER.value,
        updated_by_user_id=user_id,
    )
    test_db.add(node)
    await test_db.flush()
    test_db.add(
        MemoirChapterNodeStory(
            project_id=project_id,
            node_id=node.id,
            story_id=story.id,
            story_role=MemoirChapterNodeStoryRole.PRIMARY.value,
            sort_order=0,
            required=True,
            created_by=MemoirNarrativeActorType.USER.value,
            created_by_user_id=user_id,
        )
    )
    await test_db.flush()
    contract.status = MemoirChapterContractStatus.CONFIRMED.value
    contract.confirmed_by_user_id = user_id
    contract.confirmed_at = datetime.now(UTC).replace(tzinfo=None)
    await story_slots.ensure_story_slot_keys(
        test_db,
        story=story,
        slot_keys={"outcome"},
    )
    outcome_slot = await test_db.scalar(
        select(MemoirStorySlot).where(
            MemoirStorySlot.project_id == project_id,
            MemoirStorySlot.story_id == story.id,
            MemoirStorySlot.slot_key == "outcome",
        )
    )
    assert outcome_slot is not None
    outcome_slot.status = MemoirStorySlotStatus.PARTIAL.value
    outcome_slot.assessment_summary = "只知道比赛结束后回了学校，还不知道比赛结果。"
    test_db.add_all(
        [
            MemoirClaimEvidence(
                project_id=project_id,
                claim_id=claim.id,
                evidence_role=MemoirClaimEvidenceRole.SUPPORTS.value,
                evidence_source_type=MemoirClaimEvidenceSourceType.TRANSCRIPT.value,
                utterance_id=created["utterance_id"],
                transcript_version_id=created["transcript_version_id"],
                audio_file_id=created["audio_file_id"],
                speaker_role=MemoirClaimEvidenceSpeakerRole.NARRATOR.value,
                start_ms=0,
                end_ms=8000,
                quote="那次比赛结束后，我们一起走回学校。",
                source_text_hash=sha256(
                    "那次比赛结束后，我们一起走回学校。".encode("utf-8")
                ).hexdigest(),
                created_by=MemoirNarrativeActorType.USER.value,
                created_by_user_id=user_id,
            ),
            MemoirStoryClaim(
                project_id=project_id,
                story_id=story.id,
                claim_id=claim.id,
                narrative_role=MemoirStoryNarrativeRole.OUTCOME.value,
                sort_order=0,
                is_core=True,
                created_by=MemoirNarrativeActorType.USER.value,
                created_by_user_id=user_id,
            ),
            MemoirStorySlotClaim(
                project_id=project_id,
                story_slot_id=outcome_slot.id,
                claim_id=claim.id,
                evidence_role=MemoirClaimEvidenceRole.SUPPORTS.value,
                created_by=MemoirNarrativeActorType.USER.value,
                created_by_user_id=user_id,
            ),
        ]
    )
    gap_items, _ = await gaps.upsert_gaps(
        test_db,
        project_id=project_id,
        candidates=[
            MemoirNarrativeGapCandidate(
                scope_type=MemoirNarrativeGapScopeType.STORY,
                primary_story_id=story.id,
                gap_type=MemoirNarrativeGapType.MISSING_SLOT,
                severity=MemoirNarrativeGapSeverity.HIGH,
                target_slot="outcome",
                title="比赛结果还不完整",
                description="需要补充比赛最后的结果，以及这件事后来有什么影响。",
                detected_by=MemoirNarrativeGapDetectedBy.RULES,
                story_roles={story.id: MemoirNarrativeGapRelationRole.PRIMARY},
                chapter_roles={chapter.id: MemoirNarrativeGapRelationRole.INVOLVED},
            )
        ],
    )
    await test_db.commit()
    task = None
    if generate:
        result = await followup_planner.generate_followup_tasks(
            test_db,
            project_id=project_id,
            chapter_id=chapter.id,
        )
        await test_db.commit()
        assert len(result.created_task_ids) == 1
        task = await followup_tasks.get_task(
            test_db,
            project_id=project_id,
            task_id=result.created_task_ids[0],
        )
        assert task is not None
    return {
        "created": created,
        "project_id": project_id,
        "user_id": user_id,
        "chapter": chapter,
        "story": story,
        "claim": claim,
        "slot": outcome_slot,
        "gap": gap_items[0],
        "task": task,
    }


async def _add_narrative_evidence(
    test_db,
    fixture: dict,
    *,
    voice_session_id: str,
    completes_target: bool = True,
    speaker_role: MemoirClaimEvidenceSpeakerRole = MemoirClaimEvidenceSpeakerRole.NARRATOR,
):
    text = (
        "我们最后拿了全校第一名，老师当场给我们发了奖状。"
        if completes_target
        else "比赛那天太阳很大，我们都穿着学校发的白色运动服。"
    )
    version = MemoirTranscriptVersion(
        voice_session_id=voice_session_id,
        version_type=MemoirTranscriptVersionType.FINAL.value,
        content=text,
        provider="test",
        created_by=MemoirCreatedBy.SYSTEM.value,
    )
    test_db.add(version)
    await test_db.flush()
    message = MemoirChatMessage(
        project_id=fixture["project_id"],
        voice_session_id=voice_session_id,
        role=MemoirChatMessageRole.USER.value,
        content_text=text,
        content_status=MemoirChatContentStatus.FINAL.value,
        transcript_version_id=version.id,
        turn_index=1,
        metadata_json={"source": "test.followup"},
    )
    test_db.add(message)
    await test_db.flush()
    utterance = MemoirVoiceUtterance(
        voice_session_id=voice_session_id,
        chat_message_id=message.id,
        transcript_raw=text,
        transcript_edited=text,
        start_ms=0,
        end_ms=5000,
        provider="test",
        provider_payload_json={"source": "test.followup"},
    )
    test_db.add(utterance)
    await test_db.flush()
    version.source_artifact_id = utterance.id
    claim = MemoirClaim(
        project_id=fixture["project_id"],
        claim_type=MemoirClaimType.EVENT.value,
        statement=(
            "讲述者所在队伍获得全校第一名并拿到奖状。"
            if completes_target
            else "比赛当天是晴天，参赛者穿着学校发的白色运动服。"
        ),
        predicate_code=MemoirClaimPredicate.NARRATIVE_FACT.value,
        time_precision=MemoirClaimTimePrecision.UNKNOWN.value,
        certainty=MemoirClaimCertainty.EXACT.value,
        verification_status=MemoirClaimVerificationStatus.UNREVIEWED.value,
        publication_status=MemoirClaimPublicationStatus.ELIGIBLE.value,
        created_by=MemoirNarrativeActorType.AI.value,
        updated_by=MemoirNarrativeActorType.AI.value,
    )
    test_db.add(claim)
    await test_db.flush()
    relations = [
        MemoirClaimEvidence(
            project_id=fixture["project_id"],
            claim_id=claim.id,
            evidence_role=MemoirClaimEvidenceRole.SUPPORTS.value,
            evidence_source_type=MemoirClaimEvidenceSourceType.TRANSCRIPT.value,
            utterance_id=utterance.id,
            transcript_version_id=version.id,
            speaker_role=speaker_role.value,
            start_ms=0,
            end_ms=5000,
            quote=text,
            source_text_hash=sha256(text.encode("utf-8")).hexdigest(),
            created_by=MemoirNarrativeActorType.AI.value,
        ),
        MemoirStoryClaim(
            project_id=fixture["project_id"],
            story_id=fixture["story"].id,
            claim_id=claim.id,
            narrative_role=(
                MemoirStoryNarrativeRole.OUTCOME.value
                if completes_target
                else MemoirStoryNarrativeRole.BACKGROUND.value
            ),
            sort_order=1,
            is_core=True,
            created_by=MemoirNarrativeActorType.AI.value,
        ),
    ]
    if completes_target:
        relations.append(
            MemoirStorySlotClaim(
                project_id=fixture["project_id"],
                story_slot_id=fixture["slot"].id,
                claim_id=claim.id,
                evidence_role=MemoirClaimEvidenceRole.SUPPORTS.value,
                created_by=MemoirNarrativeActorType.AI.value,
            )
        )
    test_db.add_all(relations)
    await test_db.flush()
    return utterance, version


async def _add_story_chapter_gap(
    test_db,
    fixture: dict,
    *,
    title: str,
    node_type: MemoirChapterNodeType,
    node_required: bool,
    story_role: MemoirChapterNodeStoryRole,
    link_required: bool,
    target_slot: str,
):
    chapter = MemoirBookPlanChapter(
        plan_id=fixture["chapter"].plan_id,
        project_id=fixture["project_id"],
        title=title,
        sort_order=fixture["chapter"].sort_order + 1,
    )
    test_db.add(chapter)
    await test_db.flush()
    contract = MemoirChapterContract(
        project_id=fixture["project_id"],
        chapter_id=chapter.id,
        version=1,
        objective=f"说明“{title}”的章节重点。",
        reader_takeaway=f"读者能够理解“{title}”。",
        status=MemoirChapterContractStatus.DRAFT.value,
        created_by=MemoirNarrativeActorType.USER.value,
        created_by_user_id=fixture["user_id"],
    )
    test_db.add(contract)
    await test_db.flush()
    node = MemoirChapterNode(
        project_id=fixture["project_id"],
        chapter_id=chapter.id,
        contract_id=contract.id,
        node_type=node_type.value,
        title="回望" if node_type == MemoirChapterNodeType.REFLECTION else "比赛经过",
        purpose="说明这个故事在本章中的作用。",
        sort_order=0,
        required=node_required,
        status=MemoirChapterNodeStatus.PLANNED.value,
        created_by=MemoirNarrativeActorType.USER.value,
        created_by_user_id=fixture["user_id"],
        updated_by=MemoirNarrativeActorType.USER.value,
        updated_by_user_id=fixture["user_id"],
    )
    test_db.add(node)
    await test_db.flush()
    test_db.add(
        MemoirChapterNodeStory(
            project_id=fixture["project_id"],
            node_id=node.id,
            story_id=fixture["story"].id,
            story_role=story_role.value,
            sort_order=0,
            required=link_required,
            created_by=MemoirNarrativeActorType.USER.value,
            created_by_user_id=fixture["user_id"],
        )
    )
    await test_db.flush()
    contract.status = MemoirChapterContractStatus.CONFIRMED.value
    contract.confirmed_by_user_id = fixture["user_id"]
    contract.confirmed_at = datetime.now(UTC).replace(tzinfo=None)
    gap_items, _ = await gaps.upsert_gaps(
        test_db,
        project_id=fixture["project_id"],
        candidates=[
            MemoirNarrativeGapCandidate(
                scope_type=MemoirNarrativeGapScopeType.STORY,
                primary_story_id=fixture["story"].id,
                gap_type=MemoirNarrativeGapType.MISSING_SLOT,
                severity=MemoirNarrativeGapSeverity.MEDIUM,
                target_slot=target_slot,
                title=f"“{fixture['story'].title}”还缺少内容",
                description="需要继续补充。",
                detected_by=MemoirNarrativeGapDetectedBy.RULES,
                story_roles={
                    fixture["story"].id: MemoirNarrativeGapRelationRole.PRIMARY,
                },
                chapter_roles={chapter.id: MemoirNarrativeGapRelationRole.INVOLVED},
            )
        ],
    )
    await test_db.commit()
    return chapter, gap_items[0]


async def test_followup_generation_exposes_one_broad_question_for_one_core_story(
    client,
    test_db,
):
    fixture = await _create_story_gap(client, test_db, generate=False)
    other_story = MemoirStory(
        project_id=fixture["project_id"],
        title="顺路提到的天气",
        title_normalized="顺路提到的天气",
        story_type=MemoirStoryType.EVENT.value,
        summary="讲述者顺路提到比赛那天天气很热。",
        time_precision=MemoirClaimTimePrecision.UNKNOWN.value,
        lifecycle_status=MemoirStoryLifecycleStatus.ACTIVE.value,
        completion_status=MemoirStoryCompletionStatus.INCOMPLETE.value,
        verification_status=MemoirStoryVerificationStatus.UNREVIEWED.value,
        publication_status=MemoirStoryPublicationStatus.ELIGIBLE.value,
        slot_schema_version="1",
        created_by=MemoirNarrativeActorType.USER.value,
        created_by_user_id=fixture["user_id"],
        updated_by=MemoirNarrativeActorType.USER.value,
        updated_by_user_id=fixture["user_id"],
    )
    test_db.add(other_story)
    await test_db.flush()
    await gaps.upsert_gaps(
        test_db,
        project_id=fixture["project_id"],
        candidates=[
            MemoirNarrativeGapCandidate(
                scope_type=MemoirNarrativeGapScopeType.STORY,
                primary_story_id=fixture["story"].id,
                gap_type=MemoirNarrativeGapType.MISSING_SLOT,
                severity=MemoirNarrativeGapSeverity.HIGH,
                target_slot="trigger",
                title="比赛起因还不完整",
                description="还不知道为什么参加比赛。",
                detected_by=MemoirNarrativeGapDetectedBy.RULES,
                story_roles={
                    fixture["story"].id: MemoirNarrativeGapRelationRole.PRIMARY,
                },
                chapter_roles={
                    fixture["chapter"].id: MemoirNarrativeGapRelationRole.INVOLVED,
                },
            ),
            MemoirNarrativeGapCandidate(
                scope_type=MemoirNarrativeGapScopeType.STORY,
                primary_story_id=other_story.id,
                gap_type=MemoirNarrativeGapType.MISSING_SLOT,
                severity=MemoirNarrativeGapSeverity.LOW,
                target_slot="background",
                title="天气背景还不完整",
                description="还可以继续补充当天天气。",
                detected_by=MemoirNarrativeGapDetectedBy.RULES,
                story_roles={other_story.id: MemoirNarrativeGapRelationRole.PRIMARY},
                chapter_roles={
                    fixture["chapter"].id: MemoirNarrativeGapRelationRole.INVOLVED,
                },
            ),
        ],
    )
    await test_db.commit()

    result = await followup_planner.generate_followup_tasks(
        test_db,
        project_id=fixture["project_id"],
        chapter_id=fixture["chapter"].id,
    )
    await test_db.commit()

    assert len(result.created_task_ids) == 1
    tasks = list(
        (
            await test_db.scalars(
                select(MemoirFollowupTask).where(
                    MemoirFollowupTask.project_id == fixture["project_id"]
                )
            )
        ).all()
    )
    assert len(tasks) == 1
    assert tasks[0].story_id == fixture["story"].id
    assert tasks[0].target_slot == MEMOIR_STORY_NARRATION_TARGET
    assert tasks[0].answer_use == MemoirFollowupAnswerUse.DIRECT_NARRATIVE.value
    assert "最关键的经过" in tasks[0].question


def test_story_narration_questions_follow_story_type_and_editorial_value():
    relationship = MemoirStory(title="与姐姐的关系", story_type="relationship")
    life_stage = MemoirStory(title="下乡的日子", story_type="life_stage")
    theme = MemoirStory(title="一生坚持学习", story_type="theme")

    assert "共同经历" in followup_planner._story_narration_question(relationship)
    assert "主要承担什么" in followup_planner._story_narration_question(life_stage)
    assert "一两件最能说明这个主题" in followup_planner._story_narration_question(theme)

    opening_gap = MemoirNarrativeGap(
        severity=MemoirNarrativeGapSeverity.MEDIUM.value,
        target_slot="semantic:opening:opening-gap",
    )
    event_detail_gap = MemoirNarrativeGap(
        severity=MemoirNarrativeGapSeverity.MEDIUM.value,
        target_slot="background",
    )
    assert followup_planner._task_priority(
        opening_gap,
        story_required=False,
        chapter_order=0,
    ) > followup_planner._task_priority(
        event_detail_gap,
        story_required=True,
        chapter_order=0,
    )
    assert (
        followup_planner._answer_use(opening_gap)
        == MemoirFollowupAnswerUse.DIRECT_NARRATIVE
    )
    assert "具体场景开始这一章" in followup_planner._task_question(
        opening_gap,
        None,
        "",
    )


async def test_semantic_followup_task_preserves_gap_target_slot(client, test_db):
    fixture = await _create_story_gap(client, test_db, generate=False)
    semantic_gaps, _ = await gaps.upsert_gaps(
        test_db,
        project_id=fixture["project_id"],
        candidates=[
            MemoirNarrativeGapCandidate(
                scope_type=MemoirNarrativeGapScopeType.STORY,
                primary_story_id=fixture["story"].id,
                gap_type=MemoirNarrativeGapType.RELATIONSHIP,
                severity=MemoirNarrativeGapSeverity.MEDIUM,
                target_slot="semantic:opening:test-opening-gap",
                title="章节开头还可以更清楚",
                description="需要补充一个能自然带入本章的开场。",
                detected_by=MemoirNarrativeGapDetectedBy.AI,
                story_roles={
                    fixture["story"].id: MemoirNarrativeGapRelationRole.PRIMARY,
                },
                chapter_roles={
                    fixture["chapter"].id: MemoirNarrativeGapRelationRole.INVOLVED,
                },
            )
        ],
    )
    await test_db.commit()

    await followup_planner.generate_followup_tasks(
        test_db,
        project_id=fixture["project_id"],
        chapter_id=fixture["chapter"].id,
    )
    await test_db.commit()

    task = await test_db.scalar(
        select(MemoirFollowupTask).where(
            MemoirFollowupTask.project_id == fixture["project_id"],
            MemoirFollowupTask.gap_id == semantic_gaps[0].id,
        )
    )
    assert task is not None
    assert task.target_slot == "semantic:opening:test-opening-gap"
    assert task.answer_use == MemoirFollowupAnswerUse.DIRECT_NARRATIVE.value


async def test_required_framing_node_does_not_promote_its_supporting_story(
    client,
    test_db,
):
    fixture = await _create_story_gap(client, test_db, generate=False)
    supporting_chapter, supporting_gap = await _add_story_chapter_gap(
        test_db,
        fixture,
        title="比赛后的回望",
        node_type=MemoirChapterNodeType.REFLECTION,
        node_required=True,
        story_role=MemoirChapterNodeStoryRole.SUPPORTING,
        link_required=False,
        target_slot="trigger",
    )
    leaked_task = MemoirFollowupTask(
        project_id=fixture["project_id"],
        gap_id=supporting_gap.id,
        chapter_id=supporting_chapter.id,
        story_id=fixture["story"].id,
        target_slot=MEMOIR_STORY_NARRATION_TARGET,
        question="这道题不应该继续出现。",
        purpose="模拟旧规划器错误提升 supporting Story 后留下的任务。",
        priority=1,
        status=MemoirFollowupTaskStatus.PENDING.value,
        created_by=MemoirNarrativeActorType.SYSTEM.value,
    )
    test_db.add(leaked_task)
    await test_db.commit()

    core_result = await followup_planner.generate_followup_tasks(
        test_db,
        project_id=fixture["project_id"],
        chapter_id=fixture["chapter"].id,
    )
    supporting_result = await followup_planner.generate_followup_tasks(
        test_db,
        project_id=fixture["project_id"],
        chapter_id=supporting_chapter.id,
    )
    await test_db.commit()

    assert supporting_result.created_task_ids == ()
    assert len(core_result.created_task_ids) == 1
    await test_db.refresh(leaked_task)
    assert leaked_task.status == MemoirFollowupTaskStatus.CANCELLED.value
    tasks = list(
        (
            await test_db.scalars(
                select(MemoirFollowupTask).where(
                    MemoirFollowupTask.project_id == fixture["project_id"],
                    MemoirFollowupTask.status == MemoirFollowupTaskStatus.PENDING.value,
                )
            )
        ).all()
    )
    assert [(task.chapter_id, task.story_id) for task in tasks] == [
        (fixture["chapter"].id, fixture["story"].id)
    ]


async def test_required_story_shared_by_two_chapters_has_one_active_narration_task(
    client,
    test_db,
):
    fixture = await _create_story_gap(client, test_db, generate=False)
    second_chapter, _ = await _add_story_chapter_gap(
        test_db,
        fixture,
        title="另一章也引用这次比赛",
        node_type=MemoirChapterNodeType.STORY,
        node_required=True,
        story_role=MemoirChapterNodeStoryRole.PRIMARY,
        link_required=True,
        target_slot="trigger",
    )

    first = await followup_planner.generate_followup_tasks(
        test_db,
        project_id=fixture["project_id"],
        chapter_id=fixture["chapter"].id,
    )
    second = await followup_planner.generate_followup_tasks(
        test_db,
        project_id=fixture["project_id"],
        chapter_id=second_chapter.id,
    )
    await test_db.commit()

    assert len(first.created_task_ids) == 1
    assert second.created_task_ids == ()
    tasks = list(
        (
            await test_db.scalars(
                select(MemoirFollowupTask).where(
                    MemoirFollowupTask.project_id == fixture["project_id"],
                    MemoirFollowupTask.status.in_(
                        [status.value for status in followup_tasks.ACTIVE_TASK_STATUSES]
                    ),
                )
            )
        ).all()
    )
    assert len(tasks) == 1
    assert tasks[0].id == first.created_task_ids[0]
    assert tasks[0].story_id == fixture["story"].id


async def test_followup_generation_marks_chapter_causality_as_clarification(
    client,
    test_db,
):
    fixture = await _create_story_gap(client, test_db, generate=False)
    await gaps.upsert_gaps(
        test_db,
        project_id=fixture["project_id"],
        candidates=[
            MemoirNarrativeGapCandidate(
                scope_type=MemoirNarrativeGapScopeType.CHAPTER,
                primary_chapter_id=fixture["chapter"].id,
                gap_type=MemoirNarrativeGapType.CAUSALITY,
                severity=MemoirNarrativeGapSeverity.MEDIUM,
                title="比赛与本章主题的关系不清楚",
                description="需要确认比赛为什么会出现在这一章。",
                detected_by=MemoirNarrativeGapDetectedBy.AI,
                story_roles={
                    fixture["story"].id: MemoirNarrativeGapRelationRole.PRIMARY,
                },
                chapter_roles={
                    fixture["chapter"].id: MemoirNarrativeGapRelationRole.PRIMARY,
                },
            )
        ],
    )
    await test_db.commit()

    await followup_planner.generate_followup_tasks(
        test_db,
        project_id=fixture["project_id"],
        chapter_id=fixture["chapter"].id,
    )
    task = await test_db.scalar(
        select(MemoirFollowupTask)
        .join(MemoirNarrativeGap, MemoirNarrativeGap.id == MemoirFollowupTask.gap_id)
        .where(MemoirNarrativeGap.gap_type == MemoirNarrativeGapType.CAUSALITY.value)
    )

    assert task is not None
    assert task.answer_use == MemoirFollowupAnswerUse.CLARIFICATION.value
    assert "没有理解这件事和本章前后内容的关系" in task.question
    assert "为什么后来这样发展" not in task.question


async def test_followup_task_finishes_after_new_slot_evidence_without_confirmation(
    client,
    test_db,
):
    fixture = await _create_story_gap(client, test_db)
    repeated = await followup_planner.generate_followup_tasks(
        test_db,
        project_id=fixture["project_id"],
        chapter_id=fixture["chapter"].id,
    )
    await test_db.commit()
    assert repeated.created_task_ids == ()
    assert repeated.updated_task_ids == (fixture["task"].id,)
    assert await test_db.scalar(
        select(func.count(MemoirFollowupTask.id)).where(
            MemoirFollowupTask.project_id == fixture["project_id"]
        )
    ) == 1

    started = await post_mobile_memoir_voice_session(
        client,
        project_id=fixture["project_id"],
        json={
            "guidance_mode": "guided_interview",
            "session_goal": "book_completion",
            "current_followup_task_id": fixture["task"].id,
        },
    )
    assert started.status_code == 201, started.text
    first_session = started.json()["session"]
    assert first_session["current_followup_task_id"] == fixture["task"].id

    stopped = await client.post(
        f"/api/mobile/memoir/voice-sessions/{first_session['id']}/stop",
        json={},
    )
    assert stopped.status_code == 200, stopped.text
    task = await followup_tasks.get_task(
        test_db,
        project_id=fixture["project_id"],
        task_id=fixture["task"].id,
    )
    assert task is not None
    assert task.status == MemoirFollowupTaskStatus.DEFERRED.value
    assert task.completed_at is None
    assert task.utterances == []

    resumed = await post_mobile_memoir_voice_session(
        client,
        project_id=fixture["project_id"],
        json={
            "guidance_mode": "guided_interview",
            "session_goal": "book_completion",
            "current_followup_task_id": fixture["task"].id,
        },
    )
    assert resumed.status_code == 201, resumed.text
    second_session = resumed.json()["session"]
    assert second_session["current_followup_task_id"] == fixture["task"].id
    utterance, version = await _add_narrative_evidence(
        test_db,
        fixture,
        voice_session_id=second_session["id"],
    )

    completion = await followup_completion.evaluate_after_narrative_ingestion(
        test_db,
        project_id=fixture["project_id"],
        utterance=utterance,
        transcript_version=version,
    )
    await test_db.commit()

    assert completion.status == MemoirFollowupTaskStatus.ANSWERED
    task = await followup_tasks.get_task(
        test_db,
        project_id=fixture["project_id"],
        task_id=fixture["task"].id,
    )
    assert task is not None
    assert task.status == MemoirFollowupTaskStatus.ANSWERED.value
    assert [(item.utterance_id, item.result_role) for item in task.utterances] == [
        (utterance.id, "target_answer")
    ]
    await test_db.refresh(fixture["slot"])
    assert fixture["slot"].status == MemoirStorySlotStatus.PARTIAL.value
    gap = await test_db.get(MemoirNarrativeGap, fixture["gap"].id)
    assert gap is not None
    assert gap.status == MemoirNarrativeGapStatus.RESOLVED.value

    other_chapter = MemoirBookPlanChapter(
        plan_id=fixture["chapter"].plan_id,
        project_id=fixture["project_id"],
        title="后来参加工作",
        sort_order=1,
    )
    test_db.add(other_chapter)
    await test_db.flush()
    other_gap = MemoirNarrativeGap(
        project_id=fixture["project_id"],
        scope_type=MemoirNarrativeGapScopeType.CHAPTER.value,
        primary_chapter_id=other_chapter.id,
        gap_type=MemoirNarrativeGapType.CAUSALITY.value,
        severity=MemoirNarrativeGapSeverity.MEDIUM.value,
        title="工作选择的原因还不清楚",
        description="需要补充为什么选择这份工作。",
        fingerprint=sha256(f"other:{fixture['project_id']}".encode()).hexdigest(),
        detected_by=MemoirNarrativeGapDetectedBy.RULES.value,
    )
    test_db.add(other_gap)
    await test_db.flush()
    other_task = MemoirFollowupTask(
        project_id=fixture["project_id"],
        gap_id=other_gap.id,
        chapter_id=other_chapter.id,
        question="后来为什么选择了这份工作？",
        purpose="补充工作选择的原因。",
        priority=100,
        status=MemoirFollowupTaskStatus.PENDING.value,
        created_by=MemoirNarrativeActorType.SYSTEM.value,
    )
    test_db.add(other_task)
    await test_db.commit()

    continued = await client.post(
        f"/api/memoir/projects/{fixture['project_id']}/voice-sessions/{second_session['id']}/followup-task/switch",
        json={
            "current_task_id": fixture["task"].id,
            "idempotency_key": "continue-after-answer",
            "note": "继续下一项补访。",
        },
    )
    assert continued.status_code == 200, continued.text
    assert continued.json() is None
    await test_db.refresh(other_task)
    assert other_task.status == MemoirFollowupTaskStatus.PENDING.value


async def test_voice_persistence_snapshots_current_followup_task(client, test_db):
    fixture = await _create_story_gap(client, test_db)
    started = await post_mobile_memoir_voice_session(
        client,
        project_id=fixture["project_id"],
        json={
            "guidance_mode": "guided_interview",
            "session_goal": "book_completion",
            "current_followup_task_id": fixture["task"].id,
        },
    )
    assert started.status_code == 201, started.text
    persisted = await VoiceFinalPersistenceService(
        db=test_db,
        voice_session_id=started.json()["session"]["id"],
        user_id=fixture["user_id"],
    ).persist_final(
        StreamingASREvent(
            type="final",
            text="我们最后拿了全校第一名。",
            start_ms=0,
            end_ms=1800,
            raw={"source": "test.followup"},
        )
    )

    relation = await test_db.scalar(
        select(MemoirFollowupTaskUtterance).where(
            MemoirFollowupTaskUtterance.task_id == fixture["task"].id,
            MemoirFollowupTaskUtterance.utterance_id == persisted.utterance_id,
        )
    )
    assert relation is not None
    assert relation.result_role == "related_context"
    assert relation.is_assignment_snapshot is True


async def test_saved_answer_completes_task_after_session_stops_before_ingestion(client, test_db):
    fixture = await _create_story_gap(client, test_db)
    started = await post_mobile_memoir_voice_session(
        client,
        project_id=fixture["project_id"],
        json={
            "guidance_mode": "guided_interview",
            "session_goal": "book_completion",
            "current_followup_task_id": fixture["task"].id,
        },
    )
    assert started.status_code == 201, started.text
    session = await test_db.get(MemoirVoiceSession, started.json()["session"]["id"])
    assert session is not None
    utterance, version = await _add_narrative_evidence(
        test_db,
        fixture,
        voice_session_id=session.id,
    )
    linked_task_id = await followup_completion.link_utterance_to_session_task(
        test_db,
        voice_session=session,
        utterance=utterance,
    )
    await test_db.commit()
    assert linked_task_id == fixture["task"].id

    stopped = await client.post(
        f"/api/mobile/memoir/voice-sessions/{session.id}/stop",
        json={},
    )
    assert stopped.status_code == 200, stopped.text
    task = await followup_tasks.get_task(
        test_db,
        project_id=fixture["project_id"],
        task_id=fixture["task"].id,
    )
    assert task is not None
    assert task.status == MemoirFollowupTaskStatus.DEFERRED.value
    session = await test_db.get(MemoirVoiceSession, session.id)
    assert session is not None
    session.current_followup_task_id = None
    test_db.add(session)
    await test_db.commit()

    completion = await followup_completion.evaluate_after_narrative_ingestion(
        test_db,
        project_id=fixture["project_id"],
        utterance=utterance,
        transcript_version=version,
    )
    await test_db.commit()

    assert completion.status == MemoirFollowupTaskStatus.ANSWERED
    assert completion.answered_task_ids == (fixture["task"].id,)
    task = await followup_tasks.get_task(
        test_db,
        project_id=fixture["project_id"],
        task_id=fixture["task"].id,
    )
    assert task is not None
    assert task.status == MemoirFollowupTaskStatus.ANSWERED.value
    assert [(item.utterance_id, item.result_role) for item in task.utterances] == [
        (utterance.id, "target_answer")
    ]
    retry_context = await followup_completion.ingestion_context_for_utterance(
        test_db,
        project_id=fixture["project_id"],
        utterance=utterance,
    )
    assert retry_context is not None
    assert retry_context.task_id == fixture["task"].id


async def test_story_narration_task_completes_after_one_valid_answer(client, test_db):
    fixture = await _create_story_gap(client, test_db)
    started = await post_mobile_memoir_voice_session(
        client,
        project_id=fixture["project_id"],
        json={
            "guidance_mode": "guided_interview",
            "session_goal": "book_completion",
            "current_followup_task_id": fixture["task"].id,
        },
    )
    assert started.status_code == 201, started.text
    session = started.json()["session"]
    utterance, version = await _add_narrative_evidence(
        test_db,
        fixture,
        voice_session_id=session["id"],
        completes_target=False,
    )

    completion = await followup_completion.evaluate_after_narrative_ingestion(
        test_db,
        project_id=fixture["project_id"],
        utterance=utterance,
        transcript_version=version,
    )
    await test_db.commit()

    assert completion.status == MemoirFollowupTaskStatus.ANSWERED
    assert completion.relation_role is not None
    assert completion.relation_role.value == "target_answer"
    task = await followup_tasks.get_task(
        test_db,
        project_id=fixture["project_id"],
        task_id=fixture["task"].id,
    )
    assert task is not None
    assert task.status == MemoirFollowupTaskStatus.ANSWERED.value
    assert [(item.utterance_id, item.result_role) for item in task.utterances] == [
        (utterance.id, "target_answer")
    ]
    gap = await test_db.get(MemoirNarrativeGap, fixture["gap"].id)
    assert gap is not None
    assert gap.status == MemoirNarrativeGapStatus.RESOLVED.value
    audit_job = await test_db.scalar(
        select(MemoirEditingJob).where(
            MemoirEditingJob.project_id == fixture["project_id"],
            MemoirEditingJob.chapter_id == fixture["chapter"].id,
            MemoirEditingJob.job_type
            == MemoirEditingJobType.RECOMPUTE_CHAPTER_STRUCTURE_AUDIT.value,
        )
    )
    assert audit_job is not None
    assert audit_job.payload_json["source"] == "book_completion_answer"


async def test_free_narration_resolves_existing_task_with_new_narrator_evidence(
    client,
    test_db,
):
    fixture = await _create_story_gap(client, test_db)
    started = await post_mobile_memoir_voice_session(
        client,
        project_id=fixture["project_id"],
        json={"guidance_mode": "free_narration"},
    )
    assert started.status_code == 201, started.text
    session = started.json()["session"]
    assert session["current_followup_task_id"] is None
    utterance, version = await _add_narrative_evidence(
        test_db,
        fixture,
        voice_session_id=session["id"],
    )

    completion = await followup_completion.evaluate_after_narrative_ingestion(
        test_db,
        project_id=fixture["project_id"],
        utterance=utterance,
        transcript_version=version,
    )
    await test_db.commit()

    assert completion.status == MemoirFollowupTaskStatus.ANSWERED
    assert completion.answered_task_ids == (fixture["task"].id,)
    task = await followup_tasks.get_task(
        test_db,
        project_id=fixture["project_id"],
        task_id=fixture["task"].id,
    )
    assert task is not None
    assert task.status == MemoirFollowupTaskStatus.ANSWERED.value
    gap = await test_db.get(MemoirNarrativeGap, fixture["gap"].id)
    assert gap is not None
    assert gap.status == MemoirNarrativeGapStatus.RESOLVED.value


async def test_interviewer_evidence_cannot_complete_narrator_followup(client, test_db):
    fixture = await _create_story_gap(client, test_db)
    started = await post_mobile_memoir_voice_session(
        client,
        project_id=fixture["project_id"],
        json={
            "guidance_mode": "guided_interview",
            "interview_type": "human_interview",
            "session_goal": "book_completion",
            "current_followup_task_id": fixture["task"].id,
        },
    )
    assert started.status_code == 201, started.text
    session = started.json()["session"]
    utterance, version = await _add_narrative_evidence(
        test_db,
        fixture,
        voice_session_id=session["id"],
        speaker_role=MemoirClaimEvidenceSpeakerRole.INTERVIEWER,
    )

    completion = await followup_completion.evaluate_after_narrative_ingestion(
        test_db,
        project_id=fixture["project_id"],
        utterance=utterance,
        transcript_version=version,
    )
    await test_db.commit()

    assert completion.status == MemoirFollowupTaskStatus.IN_PROGRESS
    assert completion.answered_task_ids == ()
    task = await followup_tasks.get_task(
        test_db,
        project_id=fixture["project_id"],
        task_id=fixture["task"].id,
    )
    assert task is not None
    assert task.status == MemoirFollowupTaskStatus.IN_PROGRESS.value
    gap = await test_db.get(MemoirNarrativeGap, fixture["gap"].id)
    assert gap is not None
    assert gap.status == MemoirNarrativeGapStatus.OPEN.value


async def test_concurrent_answer_does_not_rollback_new_narrative_evidence(
    client,
    test_db,
    monkeypatch,
):
    fixture = await _create_story_gap(client, test_db)
    started = await post_mobile_memoir_voice_session(
        client,
        project_id=fixture["project_id"],
        json={
            "guidance_mode": "guided_interview",
            "session_goal": "book_completion",
            "current_followup_task_id": fixture["task"].id,
        },
    )
    assert started.status_code == 201, started.text
    utterance, version = await _add_narrative_evidence(
        test_db,
        fixture,
        voice_session_id=started.json()["session"]["id"],
    )
    original_transition = followup_tasks.transition_task
    raced = False

    async def transition_with_competing_answer(db, **kwargs):
        nonlocal raced
        if kwargs["status"] == MemoirFollowupTaskStatus.ANSWERED and not raced:
            raced = True
            task = await followup_tasks.get_task(
                db,
                project_id=kwargs["project_id"],
                task_id=kwargs["task_id"],
            )
            assert task is not None
            task.status = MemoirFollowupTaskStatus.ANSWERED.value
            task.completed_at = datetime.now(UTC).replace(tzinfo=None)
            db.add(task)
            await db.flush()
            raise followup_tasks.FollowupTaskConflictError("simulated competing answer")
        return await original_transition(db, **kwargs)

    monkeypatch.setattr(followup_tasks, "transition_task", transition_with_competing_answer)
    completion = await followup_completion.evaluate_after_narrative_ingestion(
        test_db,
        project_id=fixture["project_id"],
        utterance=utterance,
        transcript_version=version,
    )
    await test_db.commit()

    assert raced is True
    assert completion.status == MemoirFollowupTaskStatus.ANSWERED
    assert completion.answered_task_ids == (fixture["task"].id,)
    relation = await test_db.scalar(
        select(MemoirFollowupTaskUtterance).where(
            MemoirFollowupTaskUtterance.task_id == fixture["task"].id,
            MemoirFollowupTaskUtterance.utterance_id == utterance.id,
        )
    )
    assert relation is not None
    gap = await test_db.get(MemoirNarrativeGap, fixture["gap"].id)
    assert gap is not None
    assert gap.status == MemoirNarrativeGapStatus.RESOLVED.value


async def test_followup_generation_runs_on_existing_background_worker(client, test_db):
    fixture = await _create_story_gap(client, test_db, generate=False)
    register_memoir_editing_job_handlers()
    response = await client.post(
        f"/api/memoir/projects/{fixture['project_id']}/followup-tasks/generation-jobs",
        json={"chapter_id": fixture["chapter"].id},
    )
    assert response.status_code == 202, response.text
    succeeded = await run_test_editing_job(test_db, response.json()["id"])
    job = await test_db.get(MemoirEditingJob, response.json()["id"])
    assert job is not None
    assert succeeded is True, job.error_message
    assert job.status == "succeeded"
    assert len(job.result_json["created_task_ids"]) == 1


async def test_cannot_recall_accepts_incomplete_and_prevents_reselection(client, test_db):
    fixture = await _create_story_gap(client, test_db)
    started = await post_mobile_memoir_voice_session(
        client,
        project_id=fixture["project_id"],
        json={
            "guidance_mode": "guided_interview",
            "session_goal": "book_completion",
            "current_followup_task_id": fixture["task"].id,
        },
    )
    session = started.json()["session"]
    response = await client.post(
        f"/api/memoir/projects/{fixture['project_id']}/followup-tasks/{fixture['task'].id}/cannot-recall",
        json={
            "idempotency_key": "cannot-recall-accept",
            "note": "确实想不起来，接受这部分暂时不完整。",
            "resolution": "accept_incomplete",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == MemoirFollowupTaskStatus.CANNOT_RECALL.value
    gap = await test_db.get(MemoirNarrativeGap, fixture["gap"].id)
    assert gap is not None
    assert gap.status == MemoirNarrativeGapStatus.ACCEPTED_INCOMPLETE.value

    next_session = await post_mobile_memoir_voice_session(
        client,
        project_id=fixture["project_id"],
        json={
            "guidance_mode": "guided_interview",
            "session_goal": "book_completion",
        },
    )
    assert next_session.status_code == 409, next_session.text
    assert "问题尚未准备好" in next_session.json()["detail"]
    assert session["current_followup_task_id"] == fixture["task"].id


async def test_ask_family_later_keeps_gap_open_and_task_resumable(client, test_db):
    fixture = await _create_story_gap(client, test_db)
    started = await post_mobile_memoir_voice_session(
        client,
        project_id=fixture["project_id"],
        json={
            "guidance_mode": "guided_interview",
            "session_goal": "book_completion",
            "current_followup_task_id": fixture["task"].id,
        },
    )
    assert started.status_code == 201, started.text
    deferred = await client.post(
        f"/api/memoir/projects/{fixture['project_id']}/followup-tasks/{fixture['task'].id}/cannot-recall",
        json={
            "idempotency_key": "cannot-recall-family",
            "note": "先问问当时一起参赛的家人。",
            "resolution": "ask_family_later",
        },
    )
    assert deferred.status_code == 200, deferred.text
    assert deferred.json()["status"] == MemoirFollowupTaskStatus.DEFERRED.value
    gap = await test_db.get(MemoirNarrativeGap, fixture["gap"].id)
    assert gap is not None
    assert gap.status == MemoirNarrativeGapStatus.OPEN.value

    resumed = await client.post(
        f"/api/memoir/projects/{fixture['project_id']}/followup-tasks/{fixture['task'].id}/resume",
        json={"idempotency_key": "resume-after-family", "note": "已经问过家人了。"},
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == MemoirFollowupTaskStatus.PENDING.value


async def test_excluding_followup_excludes_related_story_claim_and_gap(client, test_db):
    fixture = await _create_story_gap(client, test_db)
    started = await post_mobile_memoir_voice_session(
        client,
        project_id=fixture["project_id"],
        json={
            "guidance_mode": "guided_interview",
            "session_goal": "book_completion",
            "current_followup_task_id": fixture["task"].id,
        },
    )
    assert started.status_code == 201, started.text
    response = await client.post(
        f"/api/memoir/projects/{fixture['project_id']}/followup-tasks/{fixture['task'].id}/exclude",
        json={
            "idempotency_key": "exclude-followup",
            "note": "这件事不放进回忆录。",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == MemoirFollowupTaskStatus.EXCLUDED.value
    await test_db.refresh(fixture["story"])
    await test_db.refresh(fixture["claim"])
    gap = await test_db.get(MemoirNarrativeGap, fixture["gap"].id)
    assert fixture["story"].publication_status == MemoirStoryPublicationStatus.EXCLUDED.value
    assert fixture["claim"].publication_status == MemoirClaimPublicationStatus.EXCLUDED.value
    assert gap is not None
    assert gap.status == MemoirNarrativeGapStatus.NOT_APPLICABLE.value


async def test_followup_prompt_drops_task_after_claim_becomes_private(client, test_db):
    fixture = await _create_story_gap(client, test_db)
    started = await post_mobile_memoir_voice_session(
        client,
        project_id=fixture["project_id"],
        json={
            "guidance_mode": "guided_interview",
            "session_goal": "book_completion",
            "current_followup_task_id": fixture["task"].id,
        },
    )
    assert started.status_code == 201, started.text
    voice_session = await test_db.get(
        MemoirVoiceSession,
        started.json()["session"]["id"],
    )
    assert voice_session is not None
    context = await followup_planner.prompt_context_for_session(
        test_db,
        voice_session=voice_session,
    )
    assert context is not None
    assert fixture["claim"].statement in context.prompt_text

    fixture["claim"].publication_status = MemoirClaimPublicationStatus.PRIVATE.value
    fixture["claim"].publication_decided_by_user_id = fixture["user_id"]
    fixture["claim"].updated_by = MemoirNarrativeActorType.USER.value
    fixture["claim"].updated_by_user_id = fixture["user_id"]
    test_db.add(fixture["claim"])
    await test_db.commit()
    assert await followup_planner.prompt_context_for_session(
        test_db,
        voice_session=voice_session,
    ) is None
    assert await followup_planner.select_next_task(
        test_db,
        project_id=fixture["project_id"],
    ) is None


async def test_followup_prompt_uses_the_pending_fact_confirmation_question(client, test_db):
    fixture = await _create_story_gap(client, test_db)
    confirmation_question = "您说的是比赛获奖了，还是只记得比赛结束了？"
    test_db.add(
        MemoirFollowupTaskUtterance(
            project_id=fixture["project_id"],
            task_id=fixture["task"].id,
            utterance_id=fixture["created"]["utterance_id"],
            result_role="related_context",
            is_assignment_snapshot=True,
            interpretation_status=(
                MemoirFollowupAnswerInterpretationStatus.NEEDS_CONFIRMATION.value
            ),
            confirmation_question=confirmation_question,
        )
    )
    await test_db.commit()
    started = await post_mobile_memoir_voice_session(
        client,
        project_id=fixture["project_id"],
        json={
            "guidance_mode": "guided_interview",
            "session_goal": "book_completion",
            "current_followup_task_id": fixture["task"].id,
        },
    )
    assert started.status_code == 201, started.text
    voice_session = await test_db.get(
        MemoirVoiceSession,
        started.json()["session"]["id"],
    )
    assert voice_session is not None

    context = await followup_planner.prompt_context_for_session(
        test_db,
        voice_session=voice_session,
    )

    assert context is not None
    assert context.question == confirmation_question
    assert f"本轮具体问题：{confirmation_question}" in context.prompt_text
