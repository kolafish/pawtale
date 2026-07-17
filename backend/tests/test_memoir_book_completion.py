from __future__ import annotations

import json

from sqlalchemy import select

from app.domain.memoir.types import MemoirEditingJobType
from app.models.memoir import MemoirEditingJob
from app.models.memoir_narrative import MemoirCompletionItem
from app.services.memoir.editing_job_handlers import (
    register_memoir_editing_job_handlers,
)
from app.services.memoir.editing_jobs import register_editing_job_handler
from app.services.memoir.narrative import completion_refresh, draft_generation_jobs
from tests.memoir_helpers import (
    create_test_book_plan_with_chapters,
    create_test_source_backed_story,
    create_test_voice_transcript,
    memoir_source_ref,
    run_test_editing_job,
)


class EmptyChapterAuditAI:
    provider_name = "test"
    model_name = "test-chapter-audit"

    async def _call_llm(self, *_args, **_kwargs):
        return '{"schema_version":"2","gaps":[]}'


class WholeChapterDraftAI:
    provider_name = "test"
    model_name = "test-whole-chapter"

    def __init__(self, claim_id: str):
        self.claim_id = claim_id

    async def _call_llm(self, *_args, **_kwargs):
        return json.dumps(
            {
                "content": "那年，我参加了学校比赛。这段经历让我一直记得。",
                "used_claim_ids": [self.claim_id],
                "unsupported_fact_candidates": [],
            },
            ensure_ascii=False,
        )


async def _run_refresh(test_db, job: MemoirEditingJob):
    async def handler(db, queued_job):
        return await completion_refresh.run_chapter_completion_refresh_job(
            db,
            queued_job,
            semantic_ai_client=EmptyChapterAuditAI(),
        )

    register_memoir_editing_job_handlers()
    register_editing_job_handler(
        MemoirEditingJobType.REFRESH_CHAPTER_COMPLETION,
        handler,
    )
    assert await run_test_editing_job(test_db, job.id) is True
    await test_db.refresh(job)


async def _run_generation(
    test_db,
    job: MemoirEditingJob,
    *,
    claim_id: str,
):
    async def handler(db, queued_job):
        return await draft_generation_jobs.run_chapter_draft_generation_job(
            db,
            queued_job,
            ai_client=WholeChapterDraftAI(claim_id),
        )

    register_editing_job_handler(
        MemoirEditingJobType.GENERATE_CHAPTER_DRAFT,
        handler,
    )
    assert await run_test_editing_job(test_db, job.id) is True
    await test_db.refresh(job)


async def test_chapter_completion_runs_from_source_material_to_one_confirmed_draft(
    client,
    test_db,
):
    source_text = "那年，我参加了学校比赛。"
    source = await create_test_voice_transcript(client, test_db, source_text)
    project_id = source["session"]["project_id"]
    _, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["学校里的比赛"],
        source_refs_by_chapter=[[memoir_source_ref(source)]],
    )
    chapter = chapters[0]
    _, claim = await create_test_source_backed_story(
        test_db,
        project_id=project_id,
        user_id=source["user_id"],
        source=source,
        title="学校里的比赛",
        statement=source_text,
    )

    initial = await client.get(
        f"/api/memoir/projects/{project_id}/book-completion/overview"
    )
    assert initial.status_code == 200
    assert initial.json()["chapters"][0]["state"] == "not_started"

    prepared = await client.post(
        (
            f"/api/memoir/projects/{project_id}/book-completion/"
            f"chapters/{chapter.id}/prepare"
        ),
        json={},
    )
    assert prepared.status_code == 202, prepared.text
    refresh_job = await test_db.get(MemoirEditingJob, prepared.json()["id"])
    assert refresh_job is not None
    await _run_refresh(test_db, refresh_job)

    draft_job = await test_db.scalar(
        select(MemoirEditingJob).where(
            MemoirEditingJob.project_id == project_id,
            MemoirEditingJob.chapter_id == chapter.id,
            MemoirEditingJob.job_type
            == MemoirEditingJobType.GENERATE_CHAPTER_DRAFT.value,
        )
    )
    assert draft_job is not None
    await _run_generation(test_db, draft_job, claim_id=claim.id)

    workspace = await client.get(
        f"/api/memoir/projects/{project_id}/chapters/{chapter.id}/workspace"
    )
    assert workspace.status_code == 200, workspace.text
    workspace_body = workspace.json()
    assert workspace_body["completion_summary"]["state"] == "draft_ready"
    assert workspace_body["draft_content"].startswith("那年，我参加了学校比赛")
    original_version_id = workspace_body["draft_version_id"]

    revised = await client.post(
        (
            f"/api/mobile/memoir/projects/{project_id}/book-plan/"
            f"chapters/{chapter.id}/draft/revise"
        ),
        json={
            "expected_version_id": original_version_id,
            "content": "那年，我参加了学校比赛。这件事至今仍留在我的记忆里。",
        },
    )
    assert revised.status_code == 200, revised.text
    assert revised.json()["latest_version"]["version"] == 2
    assert revised.json()["status"] == "draft"

    confirmed = await client.post(
        (
            f"/api/mobile/memoir/projects/{project_id}/book-plan/"
            f"chapters/{chapter.id}/draft/confirm"
        )
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "confirmed"

    no_op_revision = await client.post(
        (
            f"/api/mobile/memoir/projects/{project_id}/book-plan/"
            f"chapters/{chapter.id}/draft/revise"
        ),
        json={
            "expected_version_id": confirmed.json()["latest_version"]["id"],
            "content": confirmed.json()["latest_version"]["content"],
        },
    )
    assert no_op_revision.status_code == 200, no_op_revision.text
    assert no_op_revision.json()["status"] == "confirmed"
    assert no_op_revision.json()["latest_version"]["version"] == 2

    final_overview = await client.get(
        f"/api/memoir/projects/{project_id}/book-completion/overview"
    )
    assert final_overview.json()["chapters"][0]["state"] == "confirmed"

    recheck = await client.post(
        (
            f"/api/memoir/projects/{project_id}/book-completion/"
            f"chapters/{chapter.id}/prepare"
        ),
        json={},
    )
    assert recheck.status_code == 202, recheck.text
    recheck_job = await test_db.get(MemoirEditingJob, recheck.json()["id"])
    assert recheck_job is not None
    await _run_refresh(test_db, recheck_job)
    assert recheck_job.result_json["draft_generation_job_id"] is None

    checked_overview = await client.get(
        f"/api/memoir/projects/{project_id}/book-completion/overview"
    )
    checked_chapter = checked_overview.json()["chapters"][0]
    assert checked_chapter["state"] == "confirmed"
    assert checked_chapter["headline"] == "本次检查未发现待补内容"


async def test_chapter_completion_exposes_one_interview_item_when_material_is_missing(
    client,
    test_db,
):
    source = await create_test_voice_transcript(client, test_db, "这段以后再讲。")
    project_id = source["session"]["project_id"]
    _, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["第一次离家"],
    )
    chapter = chapters[0]

    prepared = await client.post(
        (
            f"/api/memoir/projects/{project_id}/book-completion/"
            f"chapters/{chapter.id}/prepare"
        ),
        json={},
    )
    assert prepared.status_code == 202
    job = await test_db.get(MemoirEditingJob, prepared.json()["id"])
    assert job is not None
    await _run_refresh(test_db, job)

    overview = await client.get(
        f"/api/memoir/projects/{project_id}/book-completion/overview"
    )
    chapter_summary = overview.json()["chapters"][0]
    assert chapter_summary["state"] == "needs_input"
    assert chapter_summary["next_action"]["kind"] == "start_interview"
    assert chapter_summary["active_item"]["id"]
    assert await test_db.scalar(
        select(MemoirCompletionItem).where(
            MemoirCompletionItem.chapter_id == chapter.id
        )
    ) is not None
    assert await test_db.scalar(
        select(MemoirEditingJob).where(
            MemoirEditingJob.chapter_id == chapter.id,
            MemoirEditingJob.job_type
            == MemoirEditingJobType.GENERATE_CHAPTER_DRAFT.value,
        )
    ) is None


async def test_openapi_exposes_only_whole_chapter_completion_actions(client):
    schema = (await client.get("/openapi.json")).json()
    paths = schema["paths"]
    schemas = schema["components"]["schemas"]

    assert any(path.endswith("/draft/revise") for path in paths)
    assert any(path.endswith("/draft/confirm") for path in paths)
    assert any(path.endswith("/book-output/assemble") for path in paths)
    assert not any("contract" in path for path in paths)
    assert not any("chapter-nodes" in path for path in paths)
    assert not any("draft-blocks" in path for path in paths)
    assert "MemoirBookChapterDraftRevisionRequest" in schemas
    assert not any("DraftBlock" in name for name in schemas)
