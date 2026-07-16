from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.domain.memoir.narrative_types import (
    MemoirChapterContractStatus,
    MemoirChapterNodeStatus,
    MemoirChapterNodeStoryRole,
    MemoirChapterNodeType,
    MemoirClaimTimePrecision,
    MemoirFollowupAnswerUse,
    MemoirFollowupTaskStatus,
    MemoirNarrativeActorType,
    MemoirNarrativeGapDetectedBy,
    MemoirNarrativeGapScopeType,
    MemoirNarrativeGapSeverity,
    MemoirNarrativeGapType,
    MemoirStoryCompletionStatus,
    MemoirStoryLifecycleStatus,
    MemoirStoryPublicationStatus,
    MemoirStorySlotStatus,
    MemoirStoryType,
    MemoirStoryVerificationStatus,
)
from app.domain.memoir.types import MemoirEditingJobType
from app.models.memoir import MemoirBookPlanChapter, MemoirEditingJob
from app.models.memoir_narrative import (
    MemoirChapterContract,
    MemoirChapterNode,
    MemoirChapterNodeStory,
    MemoirFollowupTask,
    MemoirNarrativeGap,
    MemoirStory,
    MemoirStorySlot,
)
from app.schemas.memoir_narrative import MemoirNarrativeGapCandidate
from app.services.memoir.editing_job_handlers import register_memoir_editing_job_handlers
from app.services.memoir.editing_jobs import register_editing_job_handler
from app.services.memoir.narrative import (
    book_completion,
    completion_refresh,
    gaps,
    story_slots,
)
from tests.memoir_helpers import (
    create_test_book_plan_with_chapters,
    create_test_voice_transcript,
    post_mobile_memoir_voice_session,
    run_test_editing_job,
)


async def _create_structured_chapter(
    client,
    test_db,
    *,
    complete: bool,
    confirm_contract: bool = True,
):
    created = await create_test_voice_transcript(client, test_db, text="那年我参加了学校比赛。")
    project_id = created["session"]["project_id"]
    user_id = created["user_id"]
    _, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["学校里的比赛"],
    )
    chapter = chapters[0]
    now = datetime.now(UTC).replace(tzinfo=None)
    story = MemoirStory(
        project_id=project_id,
        title="学校里的比赛",
        title_normalized="学校里的比赛",
        story_type=MemoirStoryType.EVENT.value,
        summary="讲述者参加了学校比赛。",
        time_precision=MemoirClaimTimePrecision.UNKNOWN.value,
        lifecycle_status=MemoirStoryLifecycleStatus.ACTIVE.value,
        completion_status=(
            MemoirStoryCompletionStatus.SUFFICIENT.value
            if complete
            else MemoirStoryCompletionStatus.INCOMPLETE.value
        ),
        verification_status=MemoirStoryVerificationStatus.UNREVIEWED.value,
        publication_status=MemoirStoryPublicationStatus.ELIGIBLE.value,
        slot_schema_version="1",
        created_by=MemoirNarrativeActorType.USER.value,
        created_by_user_id=user_id,
        updated_by=MemoirNarrativeActorType.USER.value,
        updated_by_user_id=user_id,
    )
    contract = MemoirChapterContract(
        project_id=project_id,
        chapter_id=chapter.id,
        version=1,
        objective="讲清楚这次比赛的经过和影响。",
        reader_takeaway="读者能理解这次比赛为什么重要。",
        status=MemoirChapterContractStatus.DRAFT.value,
        created_by=MemoirNarrativeActorType.USER.value,
        created_by_user_id=user_id,
    )
    test_db.add_all([story, contract])
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
    slots = await story_slots.ensure_story_slot_keys(
        test_db,
        story=story,
        slot_keys=set(
            story_slots.story_slot_schema(
                story.story_type,
                story.slot_schema_version,
            ).slot_keys
        ),
    )
    if complete:
        for slot in slots:
            slot.status = MemoirStorySlotStatus.SUFFICIENT.value
    await test_db.flush()
    if confirm_contract:
        contract.status = MemoirChapterContractStatus.CONFIRMED.value
        contract.confirmed_by_user_id = user_id
        contract.confirmed_at = now
    await test_db.commit()
    return project_id, chapter, story


async def _latest_job(test_db, project_id: str, job_type: MemoirEditingJobType):
    return await test_db.scalar(
        select(MemoirEditingJob)
        .where(
            MemoirEditingJob.project_id == project_id,
            MemoirEditingJob.job_type == job_type.value,
        )
        .order_by(MemoirEditingJob.created_at.desc())
        .execution_options(populate_existing=True)
    )


async def _run_completion_preparation(client, test_db, project_id: str, chapter_id: str):
    register_memoir_editing_job_handlers()
    started = await client.post(
        f"/api/memoir/projects/{project_id}/chapters/{chapter_id}/completion-refresh-jobs",
        json={},
    )
    assert started.status_code == 202, started.text

    refresh_job = await _latest_job(
        test_db,
        project_id,
        MemoirEditingJobType.REFRESH_CHAPTER_COMPLETION,
    )
    assert refresh_job is not None
    assert refresh_job.id == started.json()["id"]
    assert refresh_job.payload_json["include_semantic_audit"] is True

    class EmptySemanticClient:
        provider_name = "test"
        model_name = "test-chapter-audit"

        async def _call_llm(self, *_args, **_kwargs):
            return '{"schema_version":"2","gaps":[]}'

    async def completion_refresh_handler(db, job):
        return await completion_refresh.run_chapter_completion_refresh_job(
            db,
            job,
            semantic_ai_client=EmptySemanticClient(),
        )

    register_editing_job_handler(
        MemoirEditingJobType.REFRESH_CHAPTER_COMPLETION,
        completion_refresh_handler,
    )
    succeeded = await run_test_editing_job(test_db, refresh_job.id)
    await test_db.refresh(refresh_job)
    assert succeeded is True, (
        refresh_job.status,
        refresh_job.error_message,
        refresh_job.result_json,
    )


async def test_book_completion_overview_prepares_and_resumes_one_chapter(
    client,
    test_db,
):
    project_id, chapter, _ = await _create_structured_chapter(client, test_db, complete=False)

    initial = await client.get(f"/api/memoir/projects/{project_id}/book-completion/overview")
    assert initial.status_code == 200
    assert initial.json()["chapters"][0]["state"] == "needs_preparation"

    await _run_completion_preparation(client, test_db, project_id, chapter.id)

    ready = await client.get(f"/api/memoir/projects/{project_id}/book-completion/overview")
    chapter_state = ready.json()["chapters"][0]
    assert chapter_state["state"] == "ready"
    assert chapter_state["active_task"]["id"]
    assert len(chapter_state["visible_tasks"]) == 1
    assert "最关键的经过" in chapter_state["visible_tasks"][0]["question"]
    assert chapter_state["next_action"]["kind"] == "start_interview"
    assert chapter_state["detail"] == "围绕这个方向讲您记得的内容，不必逐项作答。"
    assert "remaining_count" not in chapter_state
    assert "item_id" not in chapter_state
    assert "item_kind" not in chapter_state
    assert not any(term in ready.text for term in ("Story", "Claim", "slot", "Contract"))
    workspace = await client.get(
        f"/api/memoir/projects/{project_id}/chapters/{chapter.id}/workspace"
    )
    assert workspace.status_code == 200, workspace.text
    assert workspace.json()["completion_summary"] == chapter_state

    voice = await post_mobile_memoir_voice_session(
        client,
        project_id=project_id,
        json={
            "guidance_mode": "guided_interview",
            "session_goal": "book_completion",
            "current_followup_task_id": chapter_state["active_task"]["id"],
            "provider": "volc_streaming_asr",
        },
    )
    assert voice.status_code == 201, voice.text

    resumed = await client.get(f"/api/memoir/projects/{project_id}/book-completion/overview")
    resumed_chapter = resumed.json()["chapters"][0]
    assert resumed_chapter["state"] == "in_progress"
    assert resumed_chapter["active_task"]["id"] == chapter_state["active_task"]["id"]
    assert resumed_chapter["active_task"]["active_session_id"] == voice.json()["session"]["id"]


async def test_book_completion_keeps_draft_chapter_direction_as_the_next_decision(
    client,
    test_db,
):
    project_id, chapter, _ = await _create_structured_chapter(
        client,
        test_db,
        complete=False,
        confirm_contract=False,
    )

    initial = await client.get(f"/api/memoir/projects/{project_id}/book-completion/overview")
    assert initial.status_code == 200, initial.text
    assert initial.json()["chapters"][0]["state"] == "needs_review"
    assert initial.json()["chapters"][0]["next_action"]["kind"] == "confirm_direction"

    await _run_completion_preparation(client, test_db, project_id, chapter.id)

    ready = await client.get(f"/api/memoir/projects/{project_id}/book-completion/overview")
    assert ready.status_code == 200, ready.text
    assert ready.json()["chapters"][0]["state"] == "needs_review"
    assert ready.json()["chapters"][0]["next_action"]["kind"] == "confirm_direction"
    assert ready.json()["chapters"][0]["visible_tasks"]


async def test_book_completion_voice_session_rejects_an_empty_task_boundary(client, test_db):
    created = await create_test_voice_transcript(client, test_db)
    response = await post_mobile_memoir_voice_session(
        client,
        project_id=created["session"]["project_id"],
        json={
            "guidance_mode": "guided_interview",
            "session_goal": "book_completion",
            "provider": "volc_streaming_asr",
        },
    )

    assert response.status_code == 409
    assert "问题尚未准备好" in response.json()["detail"]


async def test_book_completion_refresh_rejects_a_chapter_outside_the_project(
    client,
    test_db,
):
    project_id, _, _ = await _create_structured_chapter(client, test_db, complete=False)

    response = await client.post(
        f"/api/memoir/projects/{project_id}/chapters/missing/completion-refresh-jobs",
        json={},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "章节不存在"


async def test_book_completion_exposes_at_most_three_interview_directions(
    client,
    test_db,
):
    project_id, chapter, _ = await _create_structured_chapter(
        client,
        test_db,
        complete=True,
    )
    await _run_completion_preparation(client, test_db, project_id, chapter.id)
    for index in range(4):
        stored, created_count = await gaps.upsert_gaps(
            test_db,
            project_id=project_id,
            candidates=[
                MemoirNarrativeGapCandidate(
                    scope_type=MemoirNarrativeGapScopeType.CHAPTER,
                    primary_chapter_id=chapter.id,
                    gap_type=MemoirNarrativeGapType.CAUSALITY,
                    severity=MemoirNarrativeGapSeverity.MEDIUM,
                    target_slot=f"semantic:opening:{index}",
                    title=f"章节方向 {index + 1}",
                    description="需要补充一段章节层面的说明。",
                    detected_by=MemoirNarrativeGapDetectedBy.AI,
                )
            ],
        )
        assert created_count == 1
        gap = stored[0]
        await test_db.flush()
        test_db.add(
            MemoirFollowupTask(
                project_id=project_id,
                gap_id=gap.id,
                chapter_id=chapter.id,
                target_slot=gap.target_slot,
                answer_use=MemoirFollowupAnswerUse.CLARIFICATION.value,
                question=gap.title,
                purpose=gap.description,
                priority=4000 - index,
                status=MemoirFollowupTaskStatus.PENDING.value,
                created_by=MemoirNarrativeActorType.SYSTEM.value,
            )
        )
    await test_db.commit()

    overview = await client.get(
        f"/api/memoir/projects/{project_id}/book-completion/overview"
    )
    visible_tasks = overview.json()["chapters"][0]["visible_tasks"]
    assert len(visible_tasks) == 3
    assert [item["priority"] for item in visible_tasks] == [4000, 3999, 3998]
    assert "章节方向 4" not in {item["question"] for item in visible_tasks}


@pytest.mark.parametrize(
    ("recommended_action", "gap_type", "expected_next_action", "expected_gap_key"),
    (
        (
            "editorial",
            MemoirNarrativeGapType.REPETITION,
            "open_editorial_fix",
            "editorial_gaps",
        ),
        (
            "manual_review",
            MemoirNarrativeGapType.CONTRADICTION,
            "open_manual_review",
            "review_gaps",
        ),
    ),
)
async def test_book_completion_summary_routes_editorial_and_manual_review_actions(
    client,
    test_db,
    recommended_action,
    gap_type,
    expected_next_action,
    expected_gap_key,
):
    register_memoir_editing_job_handlers()
    project_id, chapter, _ = await _create_structured_chapter(
        client,
        test_db,
        complete=True,
    )
    await _run_completion_preparation(client, test_db, project_id, chapter.id)
    stored_gaps, created_count = await gaps.upsert_gaps(
        test_db,
        project_id=project_id,
        candidates=[
            MemoirNarrativeGapCandidate(
                scope_type=MemoirNarrativeGapScopeType.CHAPTER,
                primary_chapter_id=chapter.id,
                gap_type=gap_type,
                severity=MemoirNarrativeGapSeverity.HIGH,
                target_slot=f"summary-routing:{recommended_action}",
                title="需要处理的章节问题",
                description="验证章节补全摘要的确定性动作分流。",
                detected_by=MemoirNarrativeGapDetectedBy.AI,
            )
        ],
    )
    assert created_count == 1
    assert stored_gaps[0].recommended_action == recommended_action
    await test_db.commit()

    overview = await client.get(
        f"/api/memoir/projects/{project_id}/book-completion/overview"
    )
    assert overview.status_code == 200, overview.text
    summary = overview.json()["chapters"][0]
    assert summary["next_action"]["kind"] == expected_next_action
    assert summary["visible_tasks"] == []
    assert len(summary[expected_gap_key]) == 1


async def test_book_completion_overview_marks_audited_complete_chapter(
    client,
    test_db,
):
    project_id, chapter, _ = await _create_structured_chapter(client, test_db, complete=True)
    test_db.add(
        MemoirBookPlanChapter(
            plan_id=chapter.plan_id,
            project_id=project_id,
            title="保留但不收入本书的补记",
            sort_order=1,
            include_in_book=False,
            source_refs_json=[],
            metadata_json={},
        )
    )
    await test_db.commit()

    await _run_completion_preparation(client, test_db, project_id, chapter.id)

    status = await client.get(f"/api/memoir/projects/{project_id}/book-completion/overview")
    assert status.status_code == 200
    assert status.json()["total_chapters"] == 1
    assert status.json()["completed_chapters"] == 1
    assert [item["chapter_id"] for item in status.json()["chapters"]] == [chapter.id]
    assert status.json()["chapters"][0]["state"] == "complete"


async def test_book_completion_overview_can_resume_a_deferred_chapter(
    client,
    test_db,
):
    project_id, chapter, _ = await _create_structured_chapter(client, test_db, complete=False)
    await _run_completion_preparation(client, test_db, project_id, chapter.id)
    tasks = list(
        (
            await test_db.scalars(
                select(MemoirFollowupTask).where(
                    MemoirFollowupTask.project_id == project_id,
                    MemoirFollowupTask.status == MemoirFollowupTaskStatus.PENDING.value,
                )
            )
        ).all()
    )
    assert tasks
    for task in tasks:
        task.status = MemoirFollowupTaskStatus.DEFERRED.value
        task.defer_until = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
        task.defer_reason = "稍后再说"
    await test_db.commit()
    for task in tasks:
        await test_db.refresh(task)
        assert task.status == MemoirFollowupTaskStatus.DEFERRED.value

    overview = await client.get(f"/api/memoir/projects/{project_id}/book-completion/overview")
    chapter_state = overview.json()["chapters"][0]
    assert chapter_state["state"] == "ready"
    task_id = chapter_state["active_task"]["id"]
    assert task_id in {task.id for task in tasks}
    assert chapter_state["active_task"]["status"] == "deferred"

    restored = await post_mobile_memoir_voice_session(
        client,
        project_id=project_id,
        json={
            "guidance_mode": "guided_interview",
            "session_goal": "book_completion",
            "current_followup_task_id": task_id,
        },
    )
    assert restored.status_code == 201, restored.text
    restored_task = await test_db.get(MemoirFollowupTask, task_id)
    assert restored_task is not None
    assert restored_task.status == MemoirFollowupTaskStatus.IN_PROGRESS.value


async def test_book_completion_reaudits_changed_content_before_reusing_an_old_question(
    client,
    test_db,
):
    project_id, chapter, story = await _create_structured_chapter(client, test_db, complete=False)
    await _run_completion_preparation(client, test_db, project_id, chapter.id)
    first = await client.get(f"/api/memoir/projects/{project_id}/book-completion/overview")
    assert first.json()["chapters"][0]["state"] == "ready"
    old_task_id = first.json()["chapters"][0]["active_task"]["id"]

    slots = (
        await test_db.scalars(
            select(MemoirStorySlot).where(
                MemoirStorySlot.project_id == project_id,
                MemoirStorySlot.story_id == story.id,
            )
        )
    ).all()
    for slot in slots:
        slot.status = MemoirStorySlotStatus.SUFFICIENT.value
    story.completion_status = MemoirStoryCompletionStatus.SUFFICIENT.value
    story.version += 1
    await test_db.commit()

    stale = await client.get(f"/api/memoir/projects/{project_id}/book-completion/overview")
    stale_chapter = stale.json()["chapters"][0]
    assert stale_chapter["state"] == "needs_preparation"
    assert stale_chapter["next_action"]["kind"] == "refresh_completion"
    assert stale_chapter["headline"] == stale_chapter["next_action"]["title"]
    workspace = await client.get(
        f"/api/memoir/projects/{project_id}/chapters/{chapter.id}/workspace"
    )
    assert workspace.status_code == 200
    assert workspace.json()["latest_refresh_job"] is None

    await _run_completion_preparation(client, test_db, project_id, chapter.id)

    refreshed = await client.get(f"/api/memoir/projects/{project_id}/book-completion/overview")
    assert refreshed.json()["chapters"][0]["state"] == "complete"
    assert refreshed.json()["chapters"][0]["active_task"] is None
    assert refreshed.json()["chapters"][0]["visible_tasks"] == []
    old_task = await test_db.get(MemoirFollowupTask, old_task_id)
    assert old_task is not None
    assert old_task.status == MemoirFollowupTaskStatus.CANCELLED.value


async def test_book_completion_uses_a_ready_question_without_waiting_for_other_chapters(
    client,
    test_db,
):
    project_id, first_chapter, _ = await _create_structured_chapter(
        client,
        test_db,
        complete=False,
    )
    await _run_completion_preparation(client, test_db, project_id, first_chapter.id)

    second_chapter = MemoirBookPlanChapter(
        plan_id=first_chapter.plan_id,
        project_id=project_id,
        title="后来参加工作",
        sort_order=1,
    )
    test_db.add(second_chapter)
    await test_db.commit()

    status = await client.get(f"/api/memoir/projects/{project_id}/book-completion/overview")

    assert status.status_code == 200
    chapter_states = {
        item["chapter_id"]: item["state"] for item in status.json()["chapters"]
    }
    assert chapter_states[first_chapter.id] == "ready"
    assert chapter_states[second_chapter.id] == "needs_review"
    second_audit = await test_db.scalar(
        select(MemoirEditingJob).where(
            MemoirEditingJob.project_id == project_id,
            MemoirEditingJob.chapter_id == second_chapter.id,
            MemoirEditingJob.job_type
            == MemoirEditingJobType.REFRESH_CHAPTER_COMPLETION.value,
        )
    )
    assert second_audit is None


async def test_book_completion_reuses_a_current_semantic_completion_refresh(
    client,
    test_db,
):
    project_id, chapter, _ = await _create_structured_chapter(
        client,
        test_db,
        complete=False,
    )
    semantic_job = await completion_refresh.enqueue_chapter_completion_refresh(
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
        include_semantic_audit=True,
        ai_model="test-model",
        source="chapter_workspace",
    )
    await test_db.commit()

    class EmptySemanticClient:
        provider_name = "test"
        model_name = "test-model"

        async def _call_llm(self, *_args, **_kwargs):
            return '{"schema_version":"2","gaps":[]}'

    async def semantic_handler(db, job):
        return await completion_refresh.run_chapter_completion_refresh_job(
            db,
            job,
            semantic_ai_client=EmptySemanticClient(),
        )

    register_memoir_editing_job_handlers()
    register_editing_job_handler(
        MemoirEditingJobType.REFRESH_CHAPTER_COMPLETION,
        semantic_handler,
    )
    assert await run_test_editing_job(test_db, semantic_job.id) is True
    status = await client.get(
        f"/api/memoir/projects/{project_id}/book-completion/overview"
    )
    audit_jobs = (
        await test_db.scalars(
            select(MemoirEditingJob).where(
                MemoirEditingJob.project_id == project_id,
                MemoirEditingJob.chapter_id == chapter.id,
                MemoirEditingJob.job_type
                == MemoirEditingJobType.REFRESH_CHAPTER_COMPLETION.value,
            )
        )
    ).all()

    assert status.status_code == 200
    assert status.json()["chapters"][0]["state"] == "ready"
    assert [job.id for job in audit_jobs] == [semantic_job.id]


async def test_book_completion_uses_the_latest_current_refresh_regardless_of_mode(
    client,
    test_db,
):
    project_id, chapter, _ = await _create_structured_chapter(
        client,
        test_db,
        complete=False,
    )
    deterministic_job = await completion_refresh.enqueue_chapter_completion_refresh(
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
        include_semantic_audit=False,
        ai_model=None,
        source="book_completion_answer",
    )
    await test_db.commit()
    register_memoir_editing_job_handlers()
    assert await run_test_editing_job(test_db, deterministic_job.id) is True

    ready = await client.get(
        f"/api/memoir/projects/{project_id}/book-completion/overview"
    )
    assert ready.status_code == 200
    assert ready.json()["chapters"][0]["state"] == "ready"
    assert ready.json()["chapters"][0]["refresh_status"] == "succeeded"

    semantic_job = await completion_refresh.enqueue_chapter_completion_refresh(
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
        include_semantic_audit=True,
        ai_model="custom-refresh-model",
        source="chapter_workspace",
    )
    await test_db.commit()

    preparing = await client.get(
        f"/api/memoir/projects/{project_id}/book-completion/overview"
    )
    workspace = await client.get(
        f"/api/memoir/projects/{project_id}/chapters/{chapter.id}/workspace"
    )
    assert preparing.status_code == 200
    assert preparing.json()["chapters"][0]["state"] == "preparing"
    assert preparing.json()["chapters"][0]["refresh_status"] == "pending"
    assert workspace.status_code == 200
    assert workspace.json()["latest_refresh_job"]["id"] == semantic_job.id


async def test_completion_refresh_does_not_publish_partial_gaps_on_semantic_failure(
    client,
    test_db,
):
    project_id, chapter, _ = await _create_structured_chapter(
        client,
        test_db,
        complete=False,
    )
    refresh_job = await completion_refresh.enqueue_chapter_completion_refresh(
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
        include_semantic_audit=True,
        ai_model="failing-semantic-model",
        source="test_atomic_semantic_failure",
    )
    await test_db.commit()

    class FailingSemanticClient:
        provider_name = "test"
        model_name = "failing-semantic-model"

        async def _call_llm(self, *_args, **_kwargs):
            raise RuntimeError("semantic provider failed")

    async def handler(db, job):
        return await completion_refresh.run_chapter_completion_refresh_job(
            db,
            job,
            semantic_ai_client=FailingSemanticClient(),
        )

    register_memoir_editing_job_handlers()
    register_editing_job_handler(
        MemoirEditingJobType.REFRESH_CHAPTER_COMPLETION,
        handler,
    )
    assert await run_test_editing_job(test_db, refresh_job.id) is False
    await test_db.refresh(refresh_job)

    assert refresh_job.status == "failed"
    assert refresh_job.result_json["published"] is False
    assert refresh_job.result_json["failed_stage"] == "semantic_analysis"
    assert await test_db.scalar(
        select(func.count(MemoirNarrativeGap.id)).where(
            MemoirNarrativeGap.project_id == project_id
        )
    ) == 0
    assert await test_db.scalar(
        select(func.count(MemoirFollowupTask.id)).where(
            MemoirFollowupTask.project_id == project_id
        )
    ) == 0


async def test_completion_refresh_requeues_when_content_changed_before_analysis(
    client,
    test_db,
):
    project_id, chapter, story = await _create_structured_chapter(
        client,
        test_db,
        complete=False,
    )
    refresh_job = await completion_refresh.enqueue_chapter_completion_refresh(
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
        include_semantic_audit=False,
        ai_model=None,
        source="test_stale_before_analysis",
    )
    await test_db.commit()
    story.version += 1
    await test_db.commit()

    register_memoir_editing_job_handlers()
    assert await run_test_editing_job(test_db, refresh_job.id) is False
    await test_db.refresh(refresh_job)
    replacement_job = await test_db.get(
        MemoirEditingJob,
        refresh_job.result_json["replacement_job_id"],
    )

    assert refresh_job.status == "skipped"
    assert (
        refresh_job.result_json["skip_reason"]
        == "chapter_content_changed_before_analysis"
    )
    assert replacement_job is not None
    assert replacement_job.id != refresh_job.id
    assert replacement_job.status == "pending"
    assert await test_db.scalar(
        select(func.count(MemoirNarrativeGap.id)).where(
            MemoirNarrativeGap.project_id == project_id
        )
    ) == 0


async def test_completion_refresh_requeues_when_content_changes_during_analysis(
    client,
    test_db,
):
    project_id, chapter, story = await _create_structured_chapter(
        client,
        test_db,
        complete=False,
    )
    refresh_job = await completion_refresh.enqueue_chapter_completion_refresh(
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
        include_semantic_audit=True,
        ai_model="changing-semantic-model",
        source="test_stale_during_analysis",
    )
    await test_db.commit()
    session_maker = async_sessionmaker(test_db.bind, expire_on_commit=False)

    class ChangingSemanticClient:
        provider_name = "test"
        model_name = "changing-semantic-model"

        async def _call_llm(self, *_args, **_kwargs):
            async with session_maker() as other_db:
                await other_db.execute(
                    update(MemoirStory)
                    .where(MemoirStory.id == story.id)
                    .values(
                        version=MemoirStory.version + 1,
                        updated_at=datetime.now(UTC).replace(tzinfo=None),
                    )
                )
                await other_db.commit()
            return '{"schema_version":"2","gaps":[]}'

    async def handler(db, job):
        return await completion_refresh.run_chapter_completion_refresh_job(
            db,
            job,
            semantic_ai_client=ChangingSemanticClient(),
        )

    register_memoir_editing_job_handlers()
    register_editing_job_handler(
        MemoirEditingJobType.REFRESH_CHAPTER_COMPLETION,
        handler,
    )
    assert await run_test_editing_job(test_db, refresh_job.id) is False
    await test_db.refresh(refresh_job)
    replacement_job = await test_db.get(
        MemoirEditingJob,
        refresh_job.result_json["replacement_job_id"],
    )

    assert refresh_job.status == "skipped"
    assert (
        refresh_job.result_json["skip_reason"]
        == "chapter_content_changed_during_analysis"
    )
    assert replacement_job is not None
    assert replacement_job.id != refresh_job.id
    assert replacement_job.status == "pending"
    assert await test_db.scalar(
        select(func.count(MemoirNarrativeGap.id)).where(
            MemoirNarrativeGap.project_id == project_id
        )
    ) == 0


async def test_completion_refresh_rolls_back_gap_publication_when_planner_fails(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapter, _ = await _create_structured_chapter(
        client,
        test_db,
        complete=False,
    )
    refresh_job = await completion_refresh.enqueue_chapter_completion_refresh(
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
        include_semantic_audit=False,
        ai_model=None,
        source="test_atomic_planner_failure",
    )
    await test_db.commit()

    async def fail_planner(*_args, **_kwargs):
        raise RuntimeError("planner failed")

    monkeypatch.setattr(
        completion_refresh.followup_planner,
        "generate_followup_tasks",
        fail_planner,
    )
    register_memoir_editing_job_handlers()
    assert await run_test_editing_job(test_db, refresh_job.id) is False
    await test_db.refresh(refresh_job)

    assert refresh_job.status == "failed"
    assert "planner failed" in refresh_job.error_message
    assert await test_db.scalar(
        select(func.count(MemoirNarrativeGap.id)).where(
            MemoirNarrativeGap.project_id == project_id
        )
    ) == 0
    assert await test_db.scalar(
        select(func.count(MemoirFollowupTask.id)).where(
            MemoirFollowupTask.project_id == project_id
        )
    ) == 0


async def test_book_completion_overview_query_count_does_not_grow_per_chapter(
    client,
    test_db,
):
    project_id, first_chapter, _ = await _create_structured_chapter(
        client,
        test_db,
        complete=True,
    )
    engine = test_db.bind
    assert engine is not None

    statement_counts: list[int] = []

    async def count_overview_queries() -> None:
        count = 0

        def before_cursor_execute(*_args):
            nonlocal count
            count += 1

        event.listen(engine.sync_engine, "before_cursor_execute", before_cursor_execute)
        try:
            await book_completion.get_book_completion_overview(
                test_db,
                project_id=project_id,
            )
        finally:
            event.remove(
                engine.sync_engine,
                "before_cursor_execute",
                before_cursor_execute,
            )
        statement_counts.append(count)

    await count_overview_queries()
    test_db.add_all(
        MemoirBookPlanChapter(
            plan_id=first_chapter.plan_id,
            project_id=project_id,
            title=f"补充章节 {index}",
            sort_order=index,
            source_refs_json=[],
            metadata_json={},
        )
        for index in range(1, 6)
    )
    await test_db.commit()
    await count_overview_queries()

    assert statement_counts[1] <= statement_counts[0] + 1


async def test_book_completion_openapi_exposes_only_the_unified_refresh_api(client):
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    document = response.json()
    paths = document["paths"]
    schemas = document["components"]["schemas"]

    assert (
        "/api/memoir/projects/{project_id}/chapters/{chapter_id}/completion-refresh-jobs"
        in paths
    )
    assert (
        "/api/memoir/projects/{project_id}/chapters/{chapter_id}/workspace/audits"
        not in paths
    )
    assert not any(
        path.endswith("/followup-tasks/{task_id}/resume")
        or path.endswith("/followup-tasks/{task_id}/cancel")
        for path in paths
    )
    assert (
        "/api/memoir/projects/{project_id}/followup-tasks/generation-jobs"
        not in paths
    )
    assert (
        "/api/memoir/projects/{project_id}/book-completion/chapters/{chapter_id}/prepare"
        not in paths
    )
    assert not any("CompletionItem" in name for name in schemas)
    assert not any("FollowupTaskGenerationJob" in name for name in schemas)
