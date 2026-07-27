from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.database import Base, register_models
from app.domain.memoir.types import (
    MemoirBookCompletionJobStatus,
    MemoirBookChapterDraftStatus,
    MemoirCompletionIssueScope,
    MemoirEditingJobStatus,
    MemoirEditingJobType,
)
from app.migrations.memoir_books import (
    assert_memoir_book_schema,
    migrate_memoir_book_schema,
)
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirBookCompletionJob,
    MemoirBookReview,
    MemoirCompletionIssue,
    MemoirEditingJob,
    MemoirSection,
    MemoirSectionMaterial,
)
from app.models.projects import Project
from app.schemas.memoir_narrative import (
    MemoirChapterDraftSectionAIRevisionRequest,
    MemoirChapterCompletionNextAction,
    MemoirChapterCompletionSummary,
)
from app.schemas.memoir_book import MemoirBookChapterDraftRevisionRequest
from app.services.memoir import (
    book_completion_jobs,
    book_reviews,
    chapter_preparation_jobs,
    chapter_digests,
    chapter_generation,
    completion_read_model,
    section_digests,
)
from app.services.memoir.editing_job_handlers import (
    register_memoir_editing_job_handlers,
)
from app.services.memoir.editing_jobs import clear_editing_job_handlers
from tests.memoir_helpers import (
    add_test_section_materials,
    create_test_book_plan_with_chapters,
    create_test_chapter_draft,
    create_test_voice_transcript,
    ensure_mobile_memoir_project,
    memoir_source_ref,
    run_test_editing_job,
)


class LayeredBookAI:
    provider_name = "test"
    model_name = "test-layered-book"

    def __init__(
        self,
        *,
        section_issue: bool = False,
        chapter_manual_issue: bool = False,
        book_issue: bool = False,
    ):
        self.section_issue = section_issue
        self.chapter_manual_issue = chapter_manual_issue
        self.book_issue = book_issue
        self.system_prompts: list[str] = []
        self.prompts: list[str] = []

    async def _call_llm(
        self,
        system_prompt,
        user_prompt,
        temperature=0.7,
        max_tokens=4000,
        timeout=60.0,
        return_raw=False,
    ):
        self.system_prompts.append(system_prompt)
        self.prompts.append(user_prompt)
        material_ids = _prompt_ids(user_prompt, "material_id")
        section_ids = _prompt_ids(user_prompt, "section_id")
        chapter_ids = _prompt_ids(user_prompt, "chapter_id")
        if "请审阅下面这个回忆录小章节" in user_prompt:
            candidates = []
            if self.section_issue:
                candidates = [
                    {
                        "issue_type": "meaning",
                        "question": "这件事后来对你有什么影响？",
                        "reason": "个人影响会让这一段的重点更清楚。",
                        "priority": "high",
                        "material_ids": material_ids[:1],
                    }
                ]
            payload = {
                "summary": "讲述者回忆了一次学校比赛。",
                "focus": "这次比赛留下的记忆。",
                "covered_points": ["事情经过"],
                "candidate_issues": candidates,
            }
        elif "请审阅回忆录章节" in user_prompt:
            candidates = []
            if self.chapter_manual_issue:
                candidates = [
                    {
                        "issue_type": "privacy",
                        "channel": "manual_review",
                        "question": "这一章是否愿意保留真实学校名称？",
                        "reason": "这是用户需要决定的隐私边界。",
                        "priority": "high",
                        "section_ids": section_ids[:1],
                    }
                ]
            payload = {
                "summary": "本章围绕一次学校比赛展开。",
                "focus": "比赛经历和它留下的记忆。",
                "structure_guidance": ["从比赛现场开始。"],
                "candidate_issues": candidates,
            }
        elif "请审阅回忆录“" in user_prompt:
            candidates = []
            if self.book_issue:
                candidates = [
                    {
                        "issue_type": "structure",
                        "channel": "manual_review",
                        "question": "是否保留两章相近的学校经历？",
                        "reason": "两章主题接近，需要用户决定取舍。",
                        "priority": "medium",
                        "chapter_ids": chapter_ids[:2],
                    }
                ]
            payload = {
                "summary": "全书记录了几段重要成长经历。",
                "strengths": ["人生阶段清楚"],
                "structure_guidance": ["统一章节之间的过渡。"],
                "candidate_issues": candidates,
            }
        else:
            section_materials = _prompt_section_materials(user_prompt)
            is_revision = "用户修改要求：" in user_prompt
            payload = {
                "sections": [
                    {
                        "section_id": section_id,
                        "title": "比赛后的回想风" if is_revision else "",
                        "content": (
                            "那天刮着风，我参加了学校比赛。"
                            if is_revision
                            else "那年，我参加了学校比赛。"
                        )
                        + "这段经历让我一直记得，也让我第一次认真看待输赢。",
                        "used_material_ids": allowed_material_ids,
                    }
                    for section_id, allowed_material_ids in section_materials
                ],
                "missing_source_candidates": [],
                "editor_notes": [],
            }
        return json.dumps(payload, ensure_ascii=False)


def test_section_revision_keeps_whole_chapter_size_limit():
    with pytest.raises(ValueError, match="整章正文不能超过"):
        MemoirBookChapterDraftRevisionRequest(
            expected_version_id="version-1",
            sections=[
                {
                    "section_id": f"section-{index}",
                    "content": "一" * 70_000,
                }
                for index in range(3)
            ],
        )


def test_section_ai_revision_normalizes_required_instruction():
    assert MemoirChapterDraftSectionAIRevisionRequest(
        expected_version_id=" version-1 ",
        instruction_text=" 写得更平实一些 ",
    ).model_dump() == {
        "ai_model": None,
        "expected_version_id": "version-1",
        "instruction_text": "写得更平实一些",
    }
    with pytest.raises(ValueError, match="instruction_text 不能为空"):
        MemoirChapterDraftSectionAIRevisionRequest(
            expected_version_id="version-1",
            instruction_text="   ",
        )
    with pytest.raises(ValueError):
        MemoirChapterDraftSectionAIRevisionRequest(
            expected_version_id="version-1",
        )


async def test_book_review_migration_creates_only_the_review_cache():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(text("DROP TABLE memoir_book_reviews"))

        first = await migrate_memoir_book_schema(engine)
        second = await migrate_memoir_book_schema(engine)

        assert first == {
            "changed_tables": ["memoir_book_reviews"],
            "changed_columns": [],
        }
        assert second == {
            "changed_tables": [],
            "changed_columns": [],
        }
        await assert_memoir_book_schema(engine)
    finally:
        await engine.dispose()


def _prompt_ids(prompt: str, key: str) -> list[str]:
    prefix = f"{key}: "
    return [
        line.removeprefix(prefix).strip()
        for line in prompt.splitlines()
        if line.startswith(prefix)
    ]


def _prompt_section_materials(prompt: str) -> list[tuple[str, list[str]]]:
    result: list[tuple[str, list[str]]] = []
    current_materials: list[str] | None = None
    for line in prompt.splitlines():
        if line.startswith("section_id: "):
            current_materials = []
            result.append(
                (line.removeprefix("section_id: ").strip(), current_materials)
            )
        elif line.startswith("material_id: ") and current_materials is not None:
            current_materials.append(
                line.removeprefix("material_id: ").strip()
            )
    return result


def _install_ai(
    monkeypatch,
    fake: LayeredBookAI,
    requested_models: list[str | None] | None = None,
) -> None:
    def factory(model=None):
        if requested_models is not None:
            requested_models.append(model)
        return fake

    monkeypatch.setattr(section_digests, "get_ai_service", factory)
    monkeypatch.setattr(chapter_digests, "get_ai_service", factory)
    monkeypatch.setattr(chapter_generation, "get_ai_service", factory)
    monkeypatch.setattr(book_reviews, "get_ai_service", factory)


async def _book(client, test_db, *, chapter_count: int = 1):
    project_id = await ensure_mobile_memoir_project(client)
    sources = [
        await create_test_voice_transcript(
            client,
            test_db,
            f"那年，我参加了第{index + 1}次学校比赛。",
            project_id=project_id,
        )
        for index in range(chapter_count)
    ]
    _, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=[
            f"学校里的比赛{index + 1}" for index in range(chapter_count)
        ],
        source_refs_by_chapter=[
            [memoir_source_ref(source)] for source in sources
        ],
    )
    return project_id, chapters, sources


async def _create_current_draft(
    test_db,
    *,
    project_id: str,
    chapter_id: str,
    status: MemoirBookChapterDraftStatus,
) -> tuple[MemoirBookChapterDraft, MemoirBookChapterDraftVersion]:
    draft = await create_test_chapter_draft(
        test_db,
        project_id=project_id,
        chapter_id=chapter_id,
        status=status,
        content="这是已经存在的完整章节正文。",
    )
    context = await chapter_generation.load_chapter_generation_context(
        test_db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    version = await test_db.scalar(
        select(MemoirBookChapterDraftVersion)
        .where(MemoirBookChapterDraftVersion.draft_id == draft.id)
        .order_by(MemoirBookChapterDraftVersion.version.desc())
    )
    assert version is not None
    used_ids = {item.material_id for item in context.materials}
    version.input_signature = context.input_signature
    version.used_material_ids_json = [
        item.material_id for item in context.materials
    ]
    version.material_snapshot_json = chapter_generation._snapshot(
        context,
        used_material_ids=used_ids,
    )
    version.section_snapshot_json = [
        {
            "section_id": section.section_id,
            "title": section.title,
            "section_type": section.section_type,
            "section_version": section.section_version,
            "sort_order": section.sort_order,
            "content": version.content,
            "used_material_ids": [
                material.material_id for material in section.materials
            ],
        }
        for section in context.sections
    ]
    test_db.add(version)
    await test_db.commit()
    return draft, version


async def test_existing_current_draft_still_requires_a_real_completion_check(
    client,
    test_db,
):
    project_id, chapters, _ = await _book(client, test_db)
    await _create_current_draft(
        test_db,
        project_id=project_id,
        chapter_id=chapters[0].id,
        status=MemoirBookChapterDraftStatus.CONFIRMED,
    )

    overview = await client.get(
        f"/api/memoir/projects/{project_id}/book-completion/overview"
    )

    assert overview.status_code == 200
    summary = overview.json()["chapters"][0]
    assert summary["state"] == "not_started"
    assert summary["headline"] == "尚未检查是否需要补充"
    assert summary["next_action"]["action_text"] == "检查本章"


async def test_prepare_keeps_a_current_existing_draft_unchanged(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, _ = await _book(client, test_db)
    chapter = chapters[0]
    _, version = await _create_current_draft(
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
        status=MemoirBookChapterDraftStatus.DRAFT,
    )
    fake = LayeredBookAI()
    _install_ai(monkeypatch, fake)

    prepared = await client.post(
        f"/api/memoir/projects/{project_id}/book-completion/"
        f"chapters/{chapter.id}/prepare",
        json={},
    )

    assert prepared.status_code == 200, prepared.text
    body = prepared.json()
    assert body["completion_summary"]["state"] == "draft_ready"
    assert body["draft_version_id"] == version.id
    assert body["draft_content"] == "这是已经存在的完整章节正文。"
    assert not any(
        "请把下面的原始讲述整理为回忆录章节" in prompt
        for prompt in fake.prompts
    )
    version_count = await test_db.scalar(
        select(func.count())
        .select_from(MemoirBookChapterDraftVersion)
        .where(MemoirBookChapterDraftVersion.chapter_id == chapter.id)
    )
    assert version_count == 1


async def test_prepare_uses_and_remembers_the_selected_ai_model(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, _ = await _book(client, test_db)
    chapter = chapters[0]
    fake = LayeredBookAI()
    requested_models: list[str | None] = []
    _install_ai(monkeypatch, fake, requested_models)

    first = await client.post(
        f"/api/memoir/projects/{project_id}/book-completion/"
        f"chapters/{chapter.id}/prepare",
        json={"ai_model": "claude-opus-4-8"},
    )

    assert first.status_code == 200, first.text
    assert requested_models
    assert set(requested_models) == {"claude-opus-4-8"}
    project = await test_db.get(Project, project_id)
    assert project is not None
    assert project.metadata_json["ai_model"] == "claude-opus-4-8"

    requested_models.clear()
    second = await client.post(
        f"/api/memoir/projects/{project_id}/book-completion/"
        f"chapters/{chapter.id}/prepare"
    )

    assert second.status_code == 200, second.text
    assert requested_models
    assert set(requested_models) == {"claude-opus-4-8"}


async def test_chapter_preparation_job_reports_progress_and_selected_model(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, _ = await _book(client, test_db)
    chapter = chapters[0]
    fake = LayeredBookAI()
    requested_models: list[str | None] = []
    _install_ai(monkeypatch, fake, requested_models)

    queued = await client.post(
        f"/api/memoir/projects/{project_id}/book-completion/"
        f"chapters/{chapter.id}/jobs",
        json={
            "operation": "prepare",
            "ai_model": "claude-opus-4-8",
        },
    )

    assert queued.status_code == 202, queued.text
    queued_body = queued.json()
    assert queued_body["status"] == "pending"
    assert queued_body["stage"] == "queued"
    assert queued_body["total_section_count"] == 1
    assert queued_body["requested_ai_model"] == "claude-opus-4-8"
    assert queued_body["can_cancel"] is True

    overview = await client.get(
        f"/api/memoir/projects/{project_id}/book-completion/overview"
    )
    assert overview.status_code == 200, overview.text
    assert overview.json()["chapters"][0]["state"] == "preparing"
    assert overview.json()["chapters"][0]["next_action"]["action_text"] == (
        "查看进度"
    )

    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        assert await run_test_editing_job(
            test_db,
            queued_body["job_id"],
        ) is True
    finally:
        clear_editing_job_handlers()
    current = await client.get(
        f"/api/memoir/projects/{project_id}/book-completion/"
        f"chapters/{chapter.id}/jobs/current"
    )

    assert current.status_code == 200, current.text
    current_body = current.json()
    assert current_body["status"] == "succeeded"
    assert current_body["stage"] == "completed"
    assert current_body["completed_section_count"] == 1
    assert current_body["outcome"] == "completed"
    assert current_body["requested_ai_model"] == "claude-opus-4-8"
    assert current_body["resolved_ai_model"] == fake.model_name
    assert current_body["can_cancel"] is False
    assert set(requested_models) == {"claude-opus-4-8"}


async def test_book_completion_job_persists_progress_failure_retry_and_cancel(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, _ = await _book(client, test_db, chapter_count=2)
    fake = LayeredBookAI()
    _install_ai(monkeypatch, fake)

    idle = await client.get(
        f"/api/memoir/projects/{project_id}/book-completion/jobs/current"
    )
    assert idle.status_code == 200, idle.text
    assert idle.json()["status"] == "idle"
    assert idle.json()["job_id"] is None

    queued = await client.post(
        f"/api/memoir/projects/{project_id}/book-completion/jobs",
        json={
            "scope": "pending",
            "ai_model": "claude-opus-4-8",
        },
    )

    assert queued.status_code == 202, queued.text
    queued_body = queued.json()
    assert queued_body["status"] == "running"
    assert queued_body["total_chapter_count"] == 2
    assert queued_body["processed_chapter_count"] == 0
    assert queued_body["current_chapter_id"] == chapters[0].id
    assert queued_body["requested_ai_model"] == "claude-opus-4-8"
    assert queued_body["can_cancel"] is True
    child_job_ids = [item["job_id"] for item in queued_body["chapters"]]
    assert all(child_job_ids)

    duplicate = await client.post(
        f"/api/memoir/projects/{project_id}/book-completion/jobs",
        json={"scope": "pending"},
    )

    assert duplicate.status_code == 202, duplicate.text
    assert duplicate.json()["job_id"] == queued_body["job_id"]
    assert [item["job_id"] for item in duplicate.json()["chapters"]] == child_job_ids
    batch_count = await test_db.scalar(
        select(func.count()).select_from(MemoirBookCompletionJob)
    )
    assert batch_count == 1

    resumed = await client.get(
        f"/api/memoir/projects/{project_id}/book-completion/jobs/current"
    )

    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["job_id"] == queued_body["job_id"]
    assert [item["job_id"] for item in resumed.json()["chapters"]] == child_job_ids

    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        assert await run_test_editing_job(test_db, child_job_ids[0]) is True
    finally:
        clear_editing_job_handlers()

    partial = await client.get(
        f"/api/memoir/projects/{project_id}/book-completion/jobs/current"
    )

    assert partial.status_code == 200, partial.text
    assert partial.json()["status"] == "running"
    assert partial.json()["processed_chapter_count"] == 1
    assert partial.json()["succeeded_chapter_count"] == 1
    assert partial.json()["current_chapter_id"] == chapters[1].id

    failed_child = await test_db.get(MemoirEditingJob, child_job_ids[1])
    assert failed_child is not None
    failed_child.status = MemoirEditingJobStatus.FAILED.value
    failed_child.error_message = "provider unavailable"
    test_db.add(failed_child)
    await test_db.commit()

    failed = await client.get(
        f"/api/memoir/projects/{project_id}/book-completion/jobs/current"
    )

    assert failed.status_code == 200, failed.text
    failed_body = failed.json()
    assert failed_body["status"] == "failed"
    assert failed_body["processed_chapter_count"] == 2
    assert failed_body["succeeded_chapter_count"] == 1
    assert failed_body["failed_chapter_count"] == 1
    assert failed_body["can_cancel"] is False
    assert failed_body["can_retry"] is True
    assert failed_body["error_message"] == "provider unavailable"
    assert failed_body["chapters"][1]["error_message"] == "provider unavailable"
    failed_batch = await test_db.get(
        MemoirBookCompletionJob,
        queued_body["job_id"],
    )
    assert failed_batch is not None
    assert [item["status"] for item in failed_batch.chapter_results_json] == [
        "succeeded",
        "failed",
    ]

    retried = await client.post(
        f"/api/memoir/projects/{project_id}/book-completion/jobs",
        json={
            "scope": "pending",
            "retry_job_id": queued_body["job_id"],
        },
    )

    assert retried.status_code == 202, retried.text
    retried_body = retried.json()
    assert retried_body["job_id"] != queued_body["job_id"]
    assert retried_body["status"] == "running"
    assert retried_body["total_chapter_count"] == 1
    assert retried_body["chapters"][0]["chapter_id"] == chapters[1].id

    stale_cancel = await client.delete(
        f"/api/memoir/projects/{project_id}/book-completion/jobs/"
        f"{queued_body['job_id']}"
    )
    assert stale_cancel.status_code == 200, stale_cancel.text
    assert stale_cancel.json()["status"] == "failed"
    assert stale_cancel.json()["processed_chapter_count"] == 2
    assert [item["status"] for item in stale_cancel.json()["chapters"]] == [
        "succeeded",
        "failed",
    ]
    current_retry = await client.get(
        f"/api/memoir/projects/{project_id}/book-completion/jobs/current"
    )
    assert current_retry.status_code == 200, current_retry.text
    assert current_retry.json()["job_id"] == retried_body["job_id"]
    assert current_retry.json()["status"] == "running"

    cancelled = await client.delete(
        f"/api/memoir/projects/{project_id}/book-completion/jobs/"
        f"{retried_body['job_id']}"
    )

    assert cancelled.status_code == 200, cancelled.text
    cancelled_body = cancelled.json()
    assert cancelled_body["status"] == "cancelled"
    assert cancelled_body["processed_chapter_count"] == 1
    assert cancelled_body["cancelled_chapter_count"] == 1
    assert cancelled_body["can_retry"] is True
    stored_batch = await test_db.get(
        MemoirBookCompletionJob,
        retried_body["job_id"],
    )
    assert stored_batch is not None
    assert stored_batch.active_key is None


async def test_book_completion_job_does_not_enqueue_after_concurrent_cancel(
    client,
    test_db,
    monkeypatch,
):
    project_id, _, _ = await _book(client, test_db, chapter_count=2)
    original_enqueue = (
        chapter_preparation_jobs.enqueue_chapter_preparation_job
    )
    cancellation_injected = False

    async def enqueue_then_cancel(*args, **kwargs):
        nonlocal cancellation_injected
        child = await original_enqueue(*args, **kwargs)
        if not cancellation_injected:
            cancellation_injected = True
            batch = await test_db.scalar(
                select(MemoirBookCompletionJob).where(
                    MemoirBookCompletionJob.project_id == project_id,
                )
            )
            assert batch is not None
            batch.status = MemoirBookCompletionJobStatus.CANCELLED.value
            batch.active_key = None
            test_db.add(batch)
            await test_db.commit()
        return child

    monkeypatch.setattr(
        book_completion_jobs.chapter_preparation_jobs,
        "enqueue_chapter_preparation_job",
        enqueue_then_cancel,
    )

    queued = await client.post(
        f"/api/memoir/projects/{project_id}/book-completion/jobs",
        json={"scope": "pending"},
    )

    assert queued.status_code == 202, queued.text
    assert queued.json()["status"] == "cancelled"
    assert queued.json()["cancelled_chapter_count"] == 2
    child_jobs = (
        await test_db.scalars(
            select(MemoirEditingJob).where(
                MemoirEditingJob.project_id == project_id,
                MemoirEditingJob.job_type
                == MemoirEditingJobType.PREPARE_CHAPTER.value,
            )
        )
    ).all()
    assert len(child_jobs) == 1
    assert child_jobs[0].status == MemoirEditingJobStatus.SKIPPED.value


async def test_chapter_workspace_exposes_section_structure(
    client,
    test_db,
):
    project_id, chapters, sources = await _book(client, test_db)
    chapter = chapters[0]

    response = await client.get(
        f"/api/memoir/projects/{project_id}/chapters/{chapter.id}/workspace"
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["server_revision"] >= 1
    section = body["sections"][0]
    assert section["title"] == chapter.title
    assert section["section_type"] == "life_fragment"
    assert section["material_count"] == 1
    assert section["included_material_count"] == 1
    assert section["materials"][0] == {
        "utterance_id": sources[0]["utterance_id"],
        "sort_order": 0,
        "text": "那年，我参加了第1次学校比赛。",
        "include_in_book": True,
    }


async def test_current_completion_issue_overrides_confirmed_draft(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, _ = await _book(client, test_db)
    chapter = chapters[0]
    chapter_id = chapter.id
    await _create_current_draft(
        test_db,
        project_id=project_id,
        chapter_id=chapter_id,
        status=MemoirBookChapterDraftStatus.CONFIRMED,
    )
    fake = LayeredBookAI(chapter_manual_issue=True)
    _install_ai(monkeypatch, fake)

    prepared = await client.post(
        f"/api/memoir/projects/{project_id}/book-completion/"
        f"chapters/{chapter.id}/prepare",
        json={},
    )

    assert prepared.status_code == 200, prepared.text
    summary = prepared.json()["completion_summary"]
    assert summary["state"] == "needs_input"
    assert summary["next_action"]["kind"] == "confirm"
    assert summary["active_issue"]["question"] == "这一章是否愿意保留真实学校名称？"
    rewritten = await client.post(
        f"/api/memoir/projects/{project_id}/book-completion/"
        f"chapters/{chapter.id}/rewrite",
        json={},
    )
    assert rewritten.status_code == 409
    assert rewritten.json()["detail"] == "本章还有一项需要您先确认"


async def test_chapter_review_filters_section_findings_before_rewrite(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, _ = await _book(client, test_db)
    chapter = chapters[0]
    chapter_id = chapter.id
    await _create_current_draft(
        test_db,
        project_id=project_id,
        chapter_id=chapter_id,
        status=MemoirBookChapterDraftStatus.CONFIRMED,
    )
    supplemental = await create_test_voice_transcript(
        client,
        test_db,
        "这是后来补充的一段背景。",
        project_id=project_id,
    )
    await add_test_section_materials(
        test_db,
        project_id=project_id,
        chapter_id=chapter_id,
        source_refs=[memoir_source_ref(supplemental)],
    )
    chapter.title = "离乡后的成长"
    test_db.add(chapter)
    await test_db.commit()
    test_db.expire_all()
    fake = LayeredBookAI(section_issue=True)
    _install_ai(monkeypatch, fake)

    prepared = await client.post(
        f"/api/memoir/projects/{project_id}/book-completion/"
        f"chapters/{chapter_id}/prepare",
        json={},
    )

    assert prepared.status_code == 200, prepared.text
    workspace = prepared.json()
    assert workspace["completion_summary"]["state"] == "draft_ready"
    assert workspace["draft_needs_rewrite"] is False
    assert workspace["draft_content"].startswith("那年，我参加了学校比赛")
    issue = await test_db.scalar(
        select(MemoirCompletionIssue).where(
            MemoirCompletionIssue.project_id == project_id,
            MemoirCompletionIssue.chapter_id == chapter_id,
        )
    )
    assert issue is None
    assert any(
        "这件事后来对你有什么影响" in prompt
        for prompt in fake.prompts
        if "请审阅回忆录章节" in prompt
    )


async def test_rewrite_reuses_current_sections_and_generates_only_new_sections(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, _ = await _book(client, test_db)
    chapter = chapters[0]
    chapter_id = chapter.id
    await _create_current_draft(
        test_db,
        project_id=project_id,
        chapter_id=chapter_id,
        status=MemoirBookChapterDraftStatus.CONFIRMED,
    )
    added_sources = [
        await create_test_voice_transcript(
            client,
            test_db,
            f"这是第 {index + 2} 个小节的讲述。",
            project_id=project_id,
        )
        for index in range(3)
    ]
    for index, source in enumerate(added_sources, start=1):
        section = MemoirSection(
            project_id=project_id,
            chapter_id=chapter_id,
            title=f"新小节 {index + 1}",
            section_type="event",
            sort_order=index,
        )
        test_db.add(section)
        await test_db.flush()
        test_db.add(
            MemoirSectionMaterial(
                project_id=project_id,
                section_id=section.id,
                utterance_id=source["utterance_id"],
                transcript_version_id=source["transcript_version_id"],
                sort_order=0,
                assignment_reason="four_section_rewrite_regression",
            )
        )
    await test_db.commit()
    test_db.expire_all()
    fake = LayeredBookAI()
    requested_models: list[str | None] = []
    _install_ai(monkeypatch, fake, requested_models)

    rewritten = await client.post(
        f"/api/memoir/projects/{project_id}/book-completion/"
        f"chapters/{chapter_id}/rewrite",
        json={"ai_model": "claude-opus-4-8"},
    )

    assert rewritten.status_code == 200, rewritten.text
    body = rewritten.json()
    assert body["completion_summary"]["state"] == "draft_ready"
    assert len(body["sections"]) == 4
    assert len(body["draft_sections"]) == 4
    assert [
        item["section_id"] for item in body["draft_sections"]
    ] == [
        item["id"] for item in body["sections"]
    ]
    assert len(fake.prompts) == 3
    assert all(
        "请把下面的原始讲述整理为回忆录章节" in prompt
        for prompt in fake.prompts
    )
    assert all(
        prompt.count("material_id: ") == 1
        for prompt in fake.prompts
    )
    assert (
        body["draft_sections"][0]["content"]
        == "这是已经存在的完整章节正文。"
    )
    assert requested_models == ["claude-opus-4-8"]


async def test_ai_revision_of_one_draft_section_preserves_other_content(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, sources = await _book(client, test_db)
    chapter = chapters[0]
    added_source = await create_test_voice_transcript(
        client,
        test_db,
        "比赛结束后，我一个人在操场边想了很久。",
        project_id=project_id,
    )
    target_section = MemoirSection(
        project_id=project_id,
        chapter_id=chapter.id,
        title="比赛后的回想峰",
        section_type="viewpoint_reflection",
        sort_order=1,
    )
    test_db.add(target_section)
    await test_db.flush()
    test_db.add(
        MemoirSectionMaterial(
            project_id=project_id,
            section_id=target_section.id,
            utterance_id=added_source["utterance_id"],
            transcript_version_id=added_source["transcript_version_id"],
            sort_order=0,
            assignment_reason="single_section_regeneration_regression",
        )
    )
    await test_db.commit()
    _, version = await _create_current_draft(
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
        status=MemoirBookChapterDraftStatus.CONFIRMED,
    )
    fake = LayeredBookAI()
    requested_models: list[str | None] = []
    _install_ai(monkeypatch, fake, requested_models)

    revised = await client.post(
        f"/api/memoir/projects/{project_id}/chapters/{chapter.id}/"
        f"draft-sections/{target_section.id}/ai-revise",
        json={
            "expected_version_id": version.id,
            "ai_model": "claude-opus-4-8",
            "instruction_text": "把山峰的峰改成刮风的风。",
        },
    )

    assert revised.status_code == 200, revised.text
    body = revised.json()
    assert body["draft_version"] == 2
    assert body["draft_status"] == MemoirBookChapterDraftStatus.DRAFT.value
    assert body["draft_sections"][0]["content"] == (
        "这是已经存在的完整章节正文。"
    )
    assert body["draft_sections"][1]["section_id"] == target_section.id
    assert body["draft_sections"][1]["title"] == "比赛后的回想风"
    assert body["draft_sections"][1]["content"].startswith("那天刮着风")
    assert len(fake.prompts) == 1
    assert sources[0]["utterance_id"] not in fake.prompts[0]
    assert added_source["utterance_id"] not in fake.prompts[0]
    assert "material_id: M1" in fake.prompts[0]
    assert "学校里的比赛1" in fake.prompts[0]
    assert "当前小节标题：\n比赛后的回想峰" in fake.prompts[0]
    assert "当前小节正文：" in fake.prompts[0]
    assert "这是已经存在的完整章节正文。" in fake.prompts[0]
    assert "把山峰的峰改成刮风的风。" in fake.prompts[0]
    assert "同时出现在标题和正文时，两处必须同步修正" in fake.prompts[0]
    assert "用户明确给出的事实更正" in fake.system_prompts[0]
    assert requested_models == ["claude-opus-4-8"]
    latest = await test_db.scalar(
        select(MemoirBookChapterDraftVersion)
        .where(MemoirBookChapterDraftVersion.draft_id == version.draft_id)
        .order_by(MemoirBookChapterDraftVersion.version.desc())
    )
    assert latest is not None
    assert latest.prompt_snapshot.endswith(":section_ai_revision")
    assert latest.feedback == (
        "AI 按要求修改小节《比赛后的回想风》："
        "把山峰的峰改成刮风的风。"
    )
    await test_db.refresh(target_section)
    assert target_section.title == "比赛后的回想风"
    assert target_section.version == 2
    draft = await test_db.get(MemoirBookChapterDraft, version.draft_id)
    assert draft is not None
    assert draft.metadata_json["last_requested_ai_model"] == "claude-opus-4-8"
    assert draft.metadata_json["last_resolved_ai_model"] == "test-layered-book"

    stale = await client.post(
        f"/api/memoir/projects/{project_id}/chapters/{chapter.id}/"
        f"draft-sections/{target_section.id}/ai-revise",
        json={
            "expected_version_id": version.id,
            "instruction_text": "把结尾改得更简洁。",
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"] == "正文已更新，请刷新后再修改"
    assert len(fake.prompts) == 1


async def test_book_overview_counts_all_unfinished_chapter_states(
    client,
    test_db,
    monkeypatch,
):
    project_id, _, _ = await _book(client, test_db, chapter_count=3)
    states = iter(("not_started", "needs_input", "draft_ready"))

    async def fake_chapter_summary(db, *, project_id, chapter):
        state = next(states)
        return MemoirChapterCompletionSummary(
            chapter_id=chapter.id,
            title=chapter.title,
            sort_order=chapter.sort_order,
            state=state,
            next_action=MemoirChapterCompletionNextAction(
                kind="prepare",
                title="下一步",
                detail="测试",
            ),
            headline="测试",
            detail="测试",
        )

    monkeypatch.setattr(
        completion_read_model,
        "chapter_summary",
        fake_chapter_summary,
    )

    overview = await completion_read_model.book_overview(
        test_db,
        project_id=project_id,
    )

    assert overview.headline == "还有 3 章未完成"
    assert overview.detail == (
        "1 章待检查，1 章待补充或确认，1 章待通读。"
        "每章只显示当前最重要的一步。"
    )


async def test_book_completion_uses_layered_pipeline_without_editing_jobs(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, _ = await _book(client, test_db)
    chapter = chapters[0]
    fake = LayeredBookAI()
    _install_ai(monkeypatch, fake)

    initial = await client.get(
        f"/api/memoir/projects/{project_id}/book-completion/overview"
    )
    assert initial.status_code == 200
    assert initial.json()["chapters"][0]["state"] == "not_started"

    prepared = await client.post(
        f"/api/memoir/projects/{project_id}/book-completion/"
        f"chapters/{chapter.id}/prepare",
        json={},
    )
    assert prepared.status_code == 200, prepared.text
    body = prepared.json()
    assert body["completion_summary"]["state"] == "draft_ready"
    assert body["draft_content"].startswith("那年，我参加了学校比赛")
    assert len(body["draft_sections"]) == 1
    assert body["draft_sections"][0]["content"] == body["draft_content"]
    assert body["draft_sections"][0]["used_material_ids"]
    original_version_id = body["draft_version_id"]
    assert await test_db.scalar(
        select(MemoirEditingJob.id).where(
            MemoirEditingJob.project_id == project_id,
        )
    ) is None

    revised = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/"
        f"chapters/{chapter.id}/draft/revise",
        json={
            "expected_version_id": original_version_id,
            "sections": [
                {
                    "section_id": body["draft_sections"][0]["section_id"],
                    "content": (
                        "那年，我参加了学校比赛。"
                        "这件事至今仍留在记忆里。"
                    ),
                }
            ],
        },
    )
    assert revised.status_code == 200, revised.text
    revised_version = await test_db.get(
        MemoirBookChapterDraftVersion,
        revised.json()["latest_version"]["id"],
    )
    assert revised_version is not None
    assert revised.json()["latest_version"]["sections"][0]["content"].endswith(
        "这件事至今仍留在记忆里。"
    )
    assert revised_version.input_signature
    assert revised_version.used_material_ids_json

    confirmed = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/"
        f"chapters/{chapter.id}/draft/confirm"
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "confirmed"
    overview = await client.get(
        f"/api/memoir/projects/{project_id}/book-completion/overview"
    )
    assert overview.json()["chapters"][0]["state"] == "confirmed"


async def test_section_revision_rejects_changed_section_boundary(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, _ = await _book(client, test_db)
    chapter = chapters[0]
    _install_ai(monkeypatch, LayeredBookAI())
    prepared = await client.post(
        f"/api/memoir/projects/{project_id}/book-completion/"
        f"chapters/{chapter.id}/prepare",
        json={},
    )
    assert prepared.status_code == 200, prepared.text
    body = prepared.json()

    revised = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/"
        f"chapters/{chapter.id}/draft/revise",
        json={
            "expected_version_id": body["draft_version_id"],
            "sections": [
                {
                    "section_id": "another-section",
                    "content": "不应保存的正文",
                }
            ],
        },
    )

    assert revised.status_code == 409
    assert "小节已经变化" in revised.json()["detail"]


async def test_legacy_flat_draft_requires_rewrite_before_confirmation(
    client,
    test_db,
):
    project_id, chapters, _ = await _book(client, test_db)
    draft, version = await _create_current_draft(
        test_db,
        project_id=project_id,
        chapter_id=chapters[0].id,
        status=MemoirBookChapterDraftStatus.DRAFT,
    )
    version.section_snapshot_json = []
    test_db.add(version)
    await test_db.commit()

    workspace_response = await client.get(
        f"/api/memoir/projects/{project_id}/chapters/"
        f"{chapters[0].id}/workspace"
    )
    assert workspace_response.status_code == 200
    workspace = workspace_response.json()
    assert workspace["draft_needs_rewrite"] is True
    assert workspace["can_rewrite_draft"] is True
    assert workspace["draft_sections"] == []

    confirmed = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/"
        f"chapters/{chapters[0].id}/draft/confirm"
    )
    assert confirmed.status_code == 409
    assert "没有小节结构" in confirmed.json()["detail"]
    assert draft.status == MemoirBookChapterDraftStatus.DRAFT.value


async def test_chapter_manual_issue_is_one_explicit_next_action(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, _ = await _book(client, test_db)
    chapter = chapters[0]
    fake = LayeredBookAI(chapter_manual_issue=True)
    _install_ai(monkeypatch, fake)

    prepared = await client.post(
        f"/api/memoir/projects/{project_id}/book-completion/"
        f"chapters/{chapter.id}/prepare",
        json={},
    )
    assert prepared.status_code == 200
    summary = prepared.json()["completion_summary"]
    assert summary["state"] == "needs_input"
    assert summary["next_action"]["kind"] == "confirm"
    assert summary["active_issue"]["question"] == "这一章是否愿意保留真实学校名称？"


async def test_book_review_uses_chapter_digests_and_creates_book_issue(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, _ = await _book(client, test_db, chapter_count=2)
    fake = LayeredBookAI(book_issue=True)
    _install_ai(monkeypatch, fake)
    bootstrapped = await client.post(
        f"/api/memoir/projects/{project_id}/sections/bootstrap"
    )
    assert bootstrapped.status_code == 200

    generated = await client.post(
        f"/api/memoir/projects/{project_id}/book-review/generate",
        json={"force_regenerate": False},
    )
    assert generated.status_code == 200
    review = generated.json()

    assert review["status"] == "ready"
    book_prompt = next(
        prompt for prompt in fake.prompts if "请审阅回忆录“" in prompt
    )
    assert all(chapter.id not in book_prompt for chapter in chapters)
    assert "chapter_id: C1" in book_prompt
    assert review["review"]["summary"] == "全书记录了几段重要成长经历。"
    issue = await test_db.scalar(
        select(MemoirCompletionIssue).where(
            MemoirCompletionIssue.project_id == project_id,
            MemoirCompletionIssue.scope
            == MemoirCompletionIssueScope.BOOK.value,
        )
    )
    assert issue is not None
    assert issue.channel == "manual_review"
    assert await test_db.scalar(
        select(MemoirBookReview.id).where(
            MemoirBookReview.project_id == project_id
        )
    ) == review["id"]
