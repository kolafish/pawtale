from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256

from sqlalchemy import func, select

from app.domain.memoir.narrative_types import (
    MemoirClaimCertainty,
    MemoirClaimEvidenceRole,
    MemoirClaimEvidenceSourceType,
    MemoirClaimEvidenceSpeakerRole,
    MemoirClaimPredicate,
    MemoirClaimPublicationStatus,
    MemoirClaimTimePrecision,
    MemoirClaimType,
    MemoirClaimVerificationStatus,
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
    MemoirTranscriptVersionType,
)
from app.models.memoir import (
    MemoirChatMessage,
    MemoirEditingJob,
    MemoirTranscriptVersion,
    MemoirVoiceSession,
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
from app.schemas.memoir_narrative import MemoirNarrativeGapCandidate
from app.services.memoir.editing_job_handlers import register_memoir_editing_job_handlers
from app.services.memoir.narrative import (
    followup_completion,
    followup_planner,
    followup_tasks,
    gaps,
    story_slots,
)
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
    await story_slots.ensure_story_slots(test_db, story=story)
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
        fixture["slot"].status = MemoirStorySlotStatus.SUFFICIENT.value
        fixture["slot"].assessment_summary = "新口述明确说明了比赛结果。"
        test_db.add(fixture["slot"])
    test_db.add_all(relations)
    await test_db.flush()
    return utterance, version


async def test_followup_task_finishes_only_after_new_slot_evidence(client, test_db):
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
    gap = await test_db.get(MemoirNarrativeGap, fixture["gap"].id)
    assert gap is not None
    assert gap.status == MemoirNarrativeGapStatus.RESOLVED.value

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


async def test_related_but_off_target_evidence_does_not_complete_task(client, test_db):
    fixture = await _create_story_gap(client, test_db)
    started = await post_mobile_memoir_voice_session(
        client,
        project_id=fixture["project_id"],
        json={
            "guidance_mode": "guided_interview",
            "session_goal": "book_completion",
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

    assert completion.status == MemoirFollowupTaskStatus.IN_PROGRESS
    assert completion.relation_role is not None
    assert completion.relation_role.value == "related_context"
    task = await followup_tasks.get_task(
        test_db,
        project_id=fixture["project_id"],
        task_id=fixture["task"].id,
    )
    assert task is not None
    assert task.status == MemoirFollowupTaskStatus.IN_PROGRESS.value
    assert [(item.utterance_id, item.result_role) for item in task.utterances] == [
        (utterance.id, "related_context")
    ]
    gap = await test_db.get(MemoirNarrativeGap, fixture["gap"].id)
    assert gap is not None
    assert gap.status == MemoirNarrativeGapStatus.OPEN.value


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
    assert next_session.status_code == 201, next_session.text
    assert next_session.json()["session"]["current_followup_task_id"] is None
    assert session["current_followup_task_id"] == fixture["task"].id


async def test_ask_family_later_keeps_gap_open_and_task_resumable(client, test_db):
    fixture = await _create_story_gap(client, test_db)
    started = await post_mobile_memoir_voice_session(
        client,
        project_id=fixture["project_id"],
        json={
            "guidance_mode": "guided_interview",
            "session_goal": "book_completion",
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
