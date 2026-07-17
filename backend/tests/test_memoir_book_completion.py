from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import event, func, select

from app.domain.memoir.narrative_types import (
    MemoirChapterContractStatus,
    MemoirChapterNodeStatus,
    MemoirChapterNodeStoryRole,
    MemoirChapterNodeType,
    MemoirClaimTimePrecision,
    MemoirDraftBlockEditorialStatus,
    MemoirDraftBlockFreshnessStatus,
    MemoirDraftBlockRole,
    MemoirFollowupAnswerUse,
    MemoirFollowupTaskStatus,
    MemoirNarrativeActorType,
    MemoirNarrativeGapDetectedBy,
    MemoirNarrativeGapScopeType,
    MemoirNarrativeGapSeverity,
    MemoirNarrativeGapType,
    MemoirStoryLifecycleStatus,
    MemoirStoryPublicationStatus,
    MemoirStoryType,
    MemoirStoryVerificationStatus,
)
from app.domain.memoir.types import MemoirEditingJobType
from app.models.memoir import MemoirBookPlanChapter, MemoirEditingJob
from app.models.memoir_narrative import (
    MemoirChapterContract,
    MemoirChapterNode,
    MemoirChapterNodeStory,
    MemoirDraftBlock,
    MemoirDraftBlockVersion,
    MemoirFollowupTask,
    MemoirNarrativeGap,
    MemoirStory,
)
from app.schemas.memoir_narrative import MemoirNarrativeGapCandidate
from app.services.memoir.editing_job_handlers import register_memoir_editing_job_handlers
from app.services.memoir.editing_jobs import register_editing_job_handler
from app.services.memoir.narrative import (
    book_completion,
    completion_refresh,
    gaps,
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
    created = await create_test_voice_transcript(
        client,
        test_db,
        text="那年我参加了学校比赛。",
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
        summary="讲述者参加了学校比赛。",
        time_precision=MemoirClaimTimePrecision.UNKNOWN.value,
        lifecycle_status=MemoirStoryLifecycleStatus.ACTIVE.value,
        verification_status=MemoirStoryVerificationStatus.UNREVIEWED.value,
        publication_status=MemoirStoryPublicationStatus.ELIGIBLE.value,
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
    await test_db.flush()
    if confirm_contract:
        contract.status = MemoirChapterContractStatus.CONFIRMED.value
        contract.confirmed_by_user_id = user_id
        contract.confirmed_at = datetime.now(UTC).replace(tzinfo=None)
    await test_db.commit()
    return project_id, chapter, story, user_id


class EmptySemanticClient:
    provider_name = "test"
    model_name = "test-chapter-audit"

    async def _call_llm(self, *_args, **_kwargs):
        return '{"schema_version":"2","gaps":[]}'


class MissingContextSemanticClient:
    provider_name = "test"
    model_name = "test-chapter-audit"

    def __init__(self, story_id: str):
        self.story_id = story_id

    async def _call_llm(self, *_args, **_kwargs):
        return f"""{{
          "schema_version": "2",
          "gaps": [{{
            "gap_type": "causality",
            "severity": "medium",
            "dimension": "chapter_context",
            "reason_signal": "missing_new_evidence",
            "title": "比赛背景还不清楚",
            "description": "现有素材没有说明为什么参加这次比赛。",
            "story_ids": ["{self.story_id}"],
            "claim_ids": []
          }}]
        }}"""


async def _run_refresh_job(test_db, job: MemoirEditingJob, *, client=None):
    async def handler(db, queued_job):
        return await completion_refresh.run_chapter_completion_refresh_job(
            db,
            queued_job,
            semantic_ai_client=client or EmptySemanticClient(),
        )

    register_memoir_editing_job_handlers()
    register_editing_job_handler(
        MemoirEditingJobType.REFRESH_CHAPTER_COMPLETION,
        handler,
    )
    succeeded = await run_test_editing_job(test_db, job.id)
    await test_db.refresh(job)
    return succeeded


async def _prepare_chapter(
    client,
    test_db,
    project_id: str,
    chapter_id: str,
    *,
    semantic_client=None,
):
    response = await client.post(
        (
            f"/api/memoir/projects/{project_id}/book-completion/"
            f"chapters/{chapter_id}/prepare"
        ),
        json={},
    )
    assert response.status_code == 202, response.text
    job = await test_db.get(MemoirEditingJob, response.json()["id"])
    assert job is not None
    assert job.payload_json["include_semantic_audit"] is True
    assert await _run_refresh_job(test_db, job, client=semantic_client) is True
    return job


async def test_book_completion_prepares_one_chapter_and_exposes_one_followup(
    client,
    test_db,
):
    project_id, chapter, story, _ = await _create_structured_chapter(
        client,
        test_db,
        complete=False,
    )

    initial = await client.get(
        f"/api/memoir/projects/{project_id}/book-completion/overview"
    )
    assert initial.status_code == 200
    assert initial.json()["chapters"][0]["state"] == "not_started"
    assert initial.json()["chapters"][0]["next_action"]["kind"] == "prepare"

    await _prepare_chapter(
        client,
        test_db,
        project_id,
        chapter.id,
        semantic_client=MissingContextSemanticClient(story.id),
    )

    overview = await client.get(
        f"/api/memoir/projects/{project_id}/book-completion/overview"
    )
    summary = overview.json()["chapters"][0]
    assert summary["state"] == "needs_input"
    assert summary["next_action"]["kind"] == "start_interview"
    assert summary["active_task"]["id"]
    assert not any(
        key in summary
        for key in (
            "visible_tasks",
            "editorial_gaps",
            "review_gaps",
            "draft_readiness",
            "refresh_status",
        )
    )
    workspace = await client.get(
        f"/api/memoir/projects/{project_id}/chapters/{chapter.id}/workspace"
    )
    assert workspace.status_code == 200
    assert workspace.json() == {
        "project_id": project_id,
        "chapter_id": chapter.id,
        "chapter_title": chapter.title,
        "completion_summary": summary,
    }

    voice = await post_mobile_memoir_voice_session(
        client,
        project_id=project_id,
        json={
            "guidance_mode": "guided_interview",
            "session_goal": "book_completion",
            "current_followup_task_id": summary["active_task"]["id"],
            "provider": "volc_streaming_asr",
        },
    )
    assert voice.status_code == 201, voice.text
    resumed = await client.get(
        f"/api/memoir/projects/{project_id}/book-completion/overview"
    )
    resumed_task = resumed.json()["chapters"][0]["active_task"]
    assert resumed_task["status"] == "in_progress"
    assert resumed_task["active_session_id"] == voice.json()["session"]["id"]


async def test_prepare_auto_confirms_the_internal_chapter_direction(client, test_db):
    project_id, chapter, _, user_id = await _create_structured_chapter(
        client,
        test_db,
        complete=False,
        confirm_contract=False,
    )

    await _prepare_chapter(client, test_db, project_id, chapter.id)

    contract = await test_db.scalar(
        select(MemoirChapterContract).where(
            MemoirChapterContract.project_id == project_id,
            MemoirChapterContract.chapter_id == chapter.id,
        )
    )
    assert contract is not None
    assert contract.status == MemoirChapterContractStatus.CONFIRMED.value
    assert contract.confirmed_by_user_id == user_id


async def test_prepare_creates_and_confirms_an_internal_direction_when_none_exists(
    client,
    test_db,
):
    created = await create_test_voice_transcript(
        client,
        test_db,
        text="我想从学校生活讲起。",
    )
    project_id = created["session"]["project_id"]
    _, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["学校生活"],
    )
    chapter = chapters[0]

    response = await client.post(
        (
            f"/api/memoir/projects/{project_id}/book-completion/"
            f"chapters/{chapter.id}/prepare"
        ),
        json={},
    )

    assert response.status_code == 202, response.text
    contract = await test_db.scalar(
        select(MemoirChapterContract).where(
            MemoirChapterContract.project_id == project_id,
            MemoirChapterContract.chapter_id == chapter.id,
        )
    )
    assert contract is not None
    assert contract.status == MemoirChapterContractStatus.CONFIRMED.value
    assert await test_db.scalar(
        select(func.count(MemoirChapterNode.id)).where(
            MemoirChapterNode.contract_id == contract.id,
        )
    ) >= 1


async def test_active_preparation_keeps_an_older_current_draft_out_of_review(
    client,
    test_db,
):
    project_id, chapter, story, _ = await _create_structured_chapter(
        client,
        test_db,
        complete=True,
    )
    node = await test_db.scalar(
        select(MemoirChapterNode).where(
            MemoirChapterNode.project_id == project_id,
            MemoirChapterNode.chapter_id == chapter.id,
        )
    )
    assert node is not None
    block = MemoirDraftBlock(
        project_id=project_id,
        chapter_id=chapter.id,
        node_id=node.id,
        block_role=MemoirDraftBlockRole.OPENING.value,
        sort_order=0,
        editorial_status=MemoirDraftBlockEditorialStatus.DRAFT.value,
        freshness_status=MemoirDraftBlockFreshnessStatus.CURRENT.value,
        locked=False,
    )
    test_db.add(block)
    await test_db.flush()
    version = MemoirDraftBlockVersion(
        project_id=project_id,
        block_id=block.id,
        version=1,
        content="那年我参加了学校比赛。",
        created_by=MemoirNarrativeActorType.SYSTEM.value,
    )
    test_db.add(version)
    await test_db.flush()
    block.current_version_id = version.id
    await test_db.commit()

    response = await client.post(
        (
            f"/api/memoir/projects/{project_id}/book-completion/"
            f"chapters/{chapter.id}/prepare"
        ),
        json={},
    )
    assert response.status_code == 202, response.text
    overview = await client.get(
        f"/api/memoir/projects/{project_id}/book-completion/overview"
    )

    summary = overview.json()["chapters"][0]
    assert summary["state"] == "preparing"
    assert summary["next_action"]["kind"] == "wait"


async def test_prepare_rejects_a_chapter_outside_the_project(client, test_db):
    project_id, _, _, _ = await _create_structured_chapter(
        client,
        test_db,
        complete=False,
    )

    response = await client.post(
        (
            f"/api/memoir/projects/{project_id}/book-completion/"
            "chapters/missing/prepare"
        ),
        json={},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "章节不存在"


async def test_book_completion_voice_session_requires_a_selected_task(client, test_db):
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


async def test_summary_selects_one_interview_even_when_multiple_tasks_exist(
    client,
    test_db,
):
    project_id, chapter, story, _ = await _create_structured_chapter(
        client,
        test_db,
        complete=False,
    )
    await _prepare_chapter(
        client,
        test_db,
        project_id,
        chapter.id,
        semantic_client=MissingContextSemanticClient(story.id),
    )
    extra_gaps, _ = await gaps.upsert_gaps(
        test_db,
        project_id=project_id,
        candidates=[
            MemoirNarrativeGapCandidate(
                scope_type=MemoirNarrativeGapScopeType.CHAPTER,
                primary_chapter_id=chapter.id,
                gap_type=MemoirNarrativeGapType.CAUSALITY,
                severity=MemoirNarrativeGapSeverity.MEDIUM,
                target_slot="semantic:chapter_context:extra",
                title="再补一段比赛背景",
                description="说明为什么参加这次比赛。",
                detected_by=MemoirNarrativeGapDetectedBy.AI,
            )
        ],
    )
    test_db.add(
        MemoirFollowupTask(
            project_id=project_id,
            gap_id=extra_gaps[0].id,
            chapter_id=chapter.id,
            target_slot=extra_gaps[0].target_slot,
            answer_use=MemoirFollowupAnswerUse.CLARIFICATION.value,
            question="为什么参加这次比赛？",
            purpose="补充比赛背景。",
            priority=1,
            status=MemoirFollowupTaskStatus.PENDING.value,
            created_by=MemoirNarrativeActorType.SYSTEM.value,
        )
    )
    await test_db.commit()
    tasks = list(
        (
            await test_db.scalars(
                select(MemoirFollowupTask)
                .where(MemoirFollowupTask.project_id == project_id)
                .order_by(MemoirFollowupTask.priority.desc())
            )
        ).all()
    )
    assert len(tasks) > 1

    overview = await client.get(
        f"/api/memoir/projects/{project_id}/book-completion/overview"
    )
    summary = overview.json()["chapters"][0]

    assert summary["active_task"]["id"] == tasks[0].id
    assert summary["next_action"]["task_id"] == tasks[0].id
    assert "visible_tasks" not in summary


async def test_editorial_findings_become_draft_guidance_instead_of_public_gaps(
    client,
    test_db,
):
    project_id, chapter, story, _ = await _create_structured_chapter(
        client,
        test_db,
        complete=True,
    )

    class EditorialSemanticClient:
        provider_name = "test"
        model_name = "editorial-test"

        async def _call_llm(self, *_args, **_kwargs):
            return f"""{{
              "schema_version": "2",
              "gaps": [{{
                "gap_type": "repetition",
                "severity": "medium",
                "dimension": "repetition",
                "reason_signal": "draft_usage_problem",
                "title": "两段经历表达重复",
                "description": "整理正文时合并重复叙述。",
                "story_ids": ["{story.id}"],
                "claim_ids": []
              }}]
            }}"""

    prepared = await client.post(
        (
            f"/api/memoir/projects/{project_id}/book-completion/"
            f"chapters/{chapter.id}/prepare"
        ),
        json={},
    )
    job = await test_db.get(MemoirEditingJob, prepared.json()["id"])
    assert job is not None
    assert await _run_refresh_job(
        test_db,
        job,
        client=EditorialSemanticClient(),
    ) is True

    assert await test_db.scalar(
        select(func.count(MemoirNarrativeGap.id)).where(
            MemoirNarrativeGap.project_id == project_id,
            MemoirNarrativeGap.recommended_action == "editorial",
        )
    ) == 0
    draft_job = await test_db.scalar(
        select(MemoirEditingJob)
        .where(
            MemoirEditingJob.project_id == project_id,
            MemoirEditingJob.chapter_id == chapter.id,
            MemoirEditingJob.job_type
            == MemoirEditingJobType.GENERATE_DRAFT_BLOCKS.value,
        )
        .order_by(MemoirEditingJob.created_at.desc())
    )
    assert draft_job is not None
    assert draft_job.payload_json["editorial_guidance"] == [
        "两段经历表达重复：整理正文时合并重复叙述。"
    ]
    overview = await client.get(
        f"/api/memoir/projects/{project_id}/book-completion/overview"
    )
    summary = overview.json()["chapters"][0]
    assert summary["state"] == "preparing"
    assert "editorial_gaps" not in summary


async def test_user_fact_confirmation_is_carried_into_automatic_draft_guidance(
    client,
    test_db,
):
    project_id, chapter, _, _ = await _create_structured_chapter(
        client,
        test_db,
        complete=True,
    )
    manual_gaps, _ = await gaps.upsert_gaps(
        test_db,
        project_id=project_id,
        candidates=[
            MemoirNarrativeGapCandidate(
                scope_type=MemoirNarrativeGapScopeType.CHAPTER,
                primary_chapter_id=chapter.id,
                gap_type=MemoirNarrativeGapType.CONTRADICTION,
                severity=MemoirNarrativeGapSeverity.HIGH,
                target_slot="departure_age",
                title="离家年龄有两个说法",
                description="一处是十八岁，一处是十九岁。",
                detected_by=MemoirNarrativeGapDetectedBy.RULES,
            )
        ],
    )
    await test_db.commit()
    before = await client.get(
        f"/api/memoir/projects/{project_id}/book-completion/overview"
    )
    summary = before.json()["chapters"][0]
    assert summary["next_action"]["kind"] == "confirm_fact"
    assert summary["confirmation"]["id"] == manual_gaps[0].id

    resolved = await client.post(
        f"/api/memoir/projects/{project_id}/gaps/{manual_gaps[0].id}/resolve",
        json={
            "idempotency_key": "confirm-departure-age",
            "note": "正确的是十八岁。",
        },
    )
    assert resolved.status_code == 200, resolved.text
    await _prepare_chapter(client, test_db, project_id, chapter.id)

    draft_job = await test_db.scalar(
        select(MemoirEditingJob)
        .where(
            MemoirEditingJob.project_id == project_id,
            MemoirEditingJob.chapter_id == chapter.id,
            MemoirEditingJob.job_type
            == MemoirEditingJobType.GENERATE_DRAFT_BLOCKS.value,
        )
        .order_by(MemoirEditingJob.created_at.desc())
    )
    assert draft_job is not None
    assert draft_job.payload_json["editorial_guidance"][0] == (
        "用户确认“离家年龄有两个说法”的准确说法：正确的是十八岁。"
    )


async def test_completion_refresh_does_not_publish_partial_gaps_on_semantic_failure(
    client,
    test_db,
):
    project_id, chapter, _, _ = await _create_structured_chapter(
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

    assert await _run_refresh_job(
        test_db,
        refresh_job,
        client=FailingSemanticClient(),
    ) is False
    assert refresh_job.status == "failed"
    assert refresh_job.result_json["published"] is False
    assert await test_db.scalar(
        select(func.count(MemoirNarrativeGap.id)).where(
            MemoirNarrativeGap.project_id == project_id
        )
    ) == 0


async def test_completion_refresh_requeues_when_content_changed_before_analysis(
    client,
    test_db,
):
    project_id, chapter, story, _ = await _create_structured_chapter(
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
    assert replacement_job is not None
    assert replacement_job.status == "pending"


async def test_completion_refresh_rolls_back_gap_publication_when_planner_fails(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapter, _, _ = await _create_structured_chapter(
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
    project_id, first_chapter, _, _ = await _create_structured_chapter(
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


async def test_openapi_exposes_only_the_simplified_completion_actions(client):
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    document = response.json()
    paths = document["paths"]
    schemas = document["components"]["schemas"]

    assert (
        "/api/memoir/projects/{project_id}/book-completion/"
        "chapters/{chapter_id}/prepare"
    ) in paths
    assert (
        "/api/memoir/projects/{project_id}/chapters/"
        "{chapter_id}/completion-refresh-jobs"
    ) not in paths
    assert not any(
        path.endswith("/accept-incomplete")
        or path.endswith("/not-applicable")
        or path.endswith("/dismiss")
        or path.endswith("/draft-blocks/{block_id}/confirm")
        or path.endswith("/draft-blocks/{block_id}/lock")
        or path.endswith("/draft-blocks/{block_id}")
        or path.endswith("/draft-blocks/{block_id}/versions")
        or path.endswith("/revision-jobs")
        or path.endswith("/apply-revision")
        for path in paths
    )
    assert "MemoirChapterWorkspaceStoryResponse" not in schemas
    assert "MemoirDraftBlockRevisionJobRequest" not in schemas
