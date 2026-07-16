from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.domain.memoir.narrative_types import (
    MemoirChapterContractStatus,
    MemoirChapterNodeStatus,
    MemoirChapterNodeStoryRole,
    MemoirChapterNodeType,
    MemoirClaimTimePrecision,
    MemoirCompletionItemAction,
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
from app.services.memoir.editing_job_handlers import register_memoir_editing_job_handlers
from app.services.memoir.editing_jobs import register_editing_job_handler
from app.services.memoir.narrative import completion_items, gap_audit, story_slots
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
        f"/api/memoir/projects/{project_id}/book-completion/chapters/{chapter_id}/prepare"
    )
    assert started.status_code == 200, started.text
    assert started.json()["state"] == "preparing"

    audit_job = await _latest_job(
        test_db,
        project_id,
        MemoirEditingJobType.RECOMPUTE_CHAPTER_STRUCTURE_AUDIT,
    )
    assert audit_job is not None
    assert audit_job.payload_json["include_semantic_audit"] is True

    class EmptySemanticClient:
        provider_name = "test"
        model_name = "test-chapter-audit"

        async def _call_llm(self, *_args, **_kwargs):
            return '{"schema_version":"2","gaps":[]}'

    async def semantic_audit_handler(db, job):
        return await gap_audit.run_chapter_gap_audit_job(
            db,
            job,
            semantic_ai_client=EmptySemanticClient(),
        )

    register_editing_job_handler(
        MemoirEditingJobType.RECOMPUTE_CHAPTER_STRUCTURE_AUDIT,
        semantic_audit_handler,
    )
    assert await run_test_editing_job(test_db, audit_job.id) is True

    generation_job = await _latest_job(
        test_db,
        project_id,
        MemoirEditingJobType.GENERATE_FOLLOWUP_TASKS,
    )
    assert generation_job is not None
    assert await run_test_editing_job(test_db, generation_job.id) is True


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
    assert chapter_state["task_id"]
    assert len(chapter_state["focus_areas"]) == 1
    assert "最关键的经过" in chapter_state["focus_areas"][0]
    assert chapter_state["headline"] == "还有一个重要方向值得补充"
    assert chapter_state["detail"] == "围绕这个方向讲您记得的内容，不必逐项作答。"
    assert "remaining_count" not in chapter_state
    assert "item_id" not in chapter_state
    assert "item_kind" not in chapter_state
    assert not any(term in ready.text for term in ("Story", "Claim", "slot", "Contract"))

    voice = await post_mobile_memoir_voice_session(
        client,
        project_id=project_id,
        json={
            "guidance_mode": "guided_interview",
            "session_goal": "book_completion",
            "current_followup_task_id": chapter_state["task_id"],
            "provider": "volc_streaming_asr",
        },
    )
    assert voice.status_code == 201, voice.text

    resumed = await client.get(f"/api/memoir/projects/{project_id}/book-completion/overview")
    resumed_chapter = resumed.json()["chapters"][0]
    assert resumed_chapter["state"] == "in_progress"
    assert resumed_chapter["task_id"] == chapter_state["task_id"]
    assert resumed_chapter["active_session_id"] == voice.json()["session"]["id"]


async def test_book_completion_can_prepare_from_a_draft_chapter_direction(
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
    assert initial.json()["chapters"][0]["state"] == "needs_preparation"

    await _run_completion_preparation(client, test_db, project_id, chapter.id)

    ready = await client.get(f"/api/memoir/projects/{project_id}/book-completion/overview")
    assert ready.status_code == 200, ready.text
    assert ready.json()["chapters"][0]["state"] == "ready"
    assert ready.json()["chapters"][0]["task_id"]


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


async def test_book_completion_prepare_rejects_a_chapter_outside_the_project(
    client,
    test_db,
):
    project_id, _, _ = await _create_structured_chapter(client, test_db, complete=False)

    response = await client.post(
        f"/api/memoir/projects/{project_id}/book-completion/chapters/missing/prepare"
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
    for index in range(4):
        gap = MemoirNarrativeGap(
            project_id=project_id,
            scope_type=MemoirNarrativeGapScopeType.CHAPTER.value,
            primary_chapter_id=chapter.id,
            gap_type=MemoirNarrativeGapType.CAUSALITY.value,
            severity=MemoirNarrativeGapSeverity.MEDIUM.value,
            target_slot=f"semantic:opening:{index}",
            title=f"章节方向 {index + 1}",
                description="需要补充一段章节层面的说明。",
                fingerprint=f"{index + 1:064d}",
                recommended_action="interview",
                action_reason_code="missing_new_evidence",
                action_decision_source="deterministic_policy",
                action_policy_version="1",
                detected_by=MemoirNarrativeGapDetectedBy.AI.value,
            )
        test_db.add(gap)
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

    items = await completion_items.chapter_completion_items(
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
    )
    interview_items = [
        item for item in items if item.action == MemoirCompletionItemAction.INTERVIEW
    ]

    assert len(interview_items) == 3
    assert [item.priority for item in interview_items] == [4000, 3999, 3998]
    assert "章节方向 4" not in {item.title for item in items}


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
    assert chapter_state["task_id"] in {task.id for task in tasks}
    assert "留到以后" in chapter_state["headline"]

    restored = await post_mobile_memoir_voice_session(
        client,
        project_id=project_id,
        json={
            "guidance_mode": "guided_interview",
            "session_goal": "book_completion",
            "current_followup_task_id": chapter_state["task_id"],
        },
    )
    assert restored.status_code == 201, restored.text
    restored_task = await test_db.get(MemoirFollowupTask, chapter_state["task_id"])
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
    old_task_id = first.json()["chapters"][0]["task_id"]

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
    assert stale.json()["chapters"][0]["state"] == "needs_preparation"
    workspace = await client.get(
        f"/api/memoir/projects/{project_id}/chapters/{chapter.id}/workspace"
    )
    assert workspace.status_code == 200
    assert workspace.json()["latest_audit_job"] is None

    await _run_completion_preparation(client, test_db, project_id, chapter.id)

    refreshed = await client.get(f"/api/memoir/projects/{project_id}/book-completion/overview")
    assert refreshed.json()["chapters"][0]["state"] == "complete"
    assert refreshed.json()["chapters"][0]["task_id"] is None
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
            == MemoirEditingJobType.RECOMPUTE_CHAPTER_STRUCTURE_AUDIT.value,
        )
    )
    assert second_audit is None


async def test_book_completion_reuses_a_current_semantic_chapter_audit(client, test_db):
    project_id, chapter, _ = await _create_structured_chapter(
        client,
        test_db,
        complete=False,
    )
    semantic_job = await gap_audit.enqueue_chapter_gap_audit(
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
        return await gap_audit.run_chapter_gap_audit_job(
            db,
            job,
            semantic_ai_client=EmptySemanticClient(),
        )

    register_memoir_editing_job_handlers()
    register_editing_job_handler(
        MemoirEditingJobType.RECOMPUTE_CHAPTER_STRUCTURE_AUDIT,
        semantic_handler,
    )
    assert await run_test_editing_job(test_db, semantic_job.id) is True
    generation_job = await _latest_job(
        test_db,
        project_id,
        MemoirEditingJobType.GENERATE_FOLLOWUP_TASKS,
    )
    assert generation_job is not None
    assert await run_test_editing_job(test_db, generation_job.id) is True

    status = await client.post(
        f"/api/memoir/projects/{project_id}/book-completion/chapters/{chapter.id}/prepare"
    )
    audit_jobs = (
        await test_db.scalars(
            select(MemoirEditingJob).where(
                MemoirEditingJob.project_id == project_id,
                MemoirEditingJob.chapter_id == chapter.id,
                MemoirEditingJob.job_type
                == MemoirEditingJobType.RECOMPUTE_CHAPTER_STRUCTURE_AUDIT.value,
            )
        )
    ).all()

    assert status.status_code == 200
    assert status.json()["state"] == "ready"
    assert [job.id for job in audit_jobs] == [semantic_job.id]
