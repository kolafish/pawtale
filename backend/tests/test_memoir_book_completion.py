from __future__ import annotations

import json

from httpx import Response
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
    MemoirBookOutput,
    MemoirBookOutputVersion,
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
    book_revision_generation,
    book_review_jobs,
    book_reviews,
    chapter_preparation_jobs,
    chapter_digests,
    chapter_generation,
    completion_read_model,
    section_digests,
)
from app.services.memoir.book_reviews import (
    BookReviewChapter,
    BookReviewContext,
    normalize_book_review,
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
        chapter_interview_issue: bool = False,
        chapter_manual_issue: bool = False,
        book_issue: bool = False,
        book_actions: bool = False,
    ):
        self.section_issue = section_issue
        self.chapter_interview_issue = chapter_interview_issue
        self.chapter_manual_issue = chapter_manual_issue
        self.book_issue = book_issue
        self.book_actions = book_actions
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
            if self.chapter_interview_issue:
                candidates = [
                    {
                        "issue_type": "meaning",
                        "channel": "interview",
                        "question": "这段经历后来对你有什么影响？",
                        "reason": "补充个人影响会让这一章更丰富。",
                        "priority": "medium",
                        "section_ids": section_ids[:1],
                    }
                ]
            elif self.chapter_manual_issue:
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
        elif "请审阅下面这本回忆录的全书结构" in user_prompt:
            candidates = []
            if self.book_issue:
                candidates = [
                    {
                        "issue_type": "structure",
                        "question": "是否保留两章相近的学校经历？",
                        "reason": "两章主题接近，需要用户决定取舍。",
                        "priority": "medium",
                        "chapter_ids": chapter_ids[:2],
                        "resolution": "user_decision",
                    }
                ]
            payload = {
                "summary": "全书记录了几段重要成长经历。",
                "strengths": ["人生阶段清楚"],
                "structure_guidance": ["统一章节之间的过渡。"],
                "editorial_actions": (
                    [
                        {
                            "action_type": "remove_repetition",
                            "title": "精简两章重复的学校经历",
                            "reason": "两章都重复描述了同一场比赛。",
                            "chapter_ids": chapter_ids[:2],
                        },
                        {
                            "action_type": "improve_transition",
                            "title": "补充学校到工作的衔接",
                            "reason": "两章之间的时间跳转较突然。",
                            "chapter_ids": chapter_ids[:2],
                        },
                        {
                            "action_type": "reorder_chapters",
                            "title": "先写第二段经历",
                            "reason": "这样更符合回忆展开的顺序。",
                            "chapter_ids": list(reversed(chapter_ids[:2])),
                        },
                    ]
                    if self.book_actions
                    else []
                ),
                "candidate_issues": candidates,
            }
        elif (
            "请根据全书检查问题和用户回答，制定书稿修改思路" in user_prompt
            or "请根据用户选中的全书编辑建议，制定书稿修改思路" in user_prompt
        ):
            payload = {
                "summary": "先保留两段经历，再统一相关人物称谓。",
                "items": [
                    {
                        "title": "保留经历并统一称谓",
                        "reason": "两段经历发生在不同阶段，人物称谓需要一致。",
                        "approach": "保留两段经历，只统一相关人物的称谓。",
                        "targets": ["学校经历"],
                    }
                ],
            }
        elif "请根据用户反馈更新完整修改思路" in user_prompt:
            payload = {
                "summary": "保留两段经历，统一称谓并适当精简。",
                "items": [
                    {
                        "title": "统一称谓并精简重复",
                        "reason": "保留不同阶段经历，同时避免表述重复。",
                        "approach": "将相关人物统一称为老同学，并压缩重复叙述。",
                        "targets": ["学校经历"],
                    }
                ],
            }
        elif "请按照已确认的修改思路生成书稿修改稿" in user_prompt:
            payload = {
                "section_revisions": [
                    {
                        "section_id": section_ids[0],
                        "content": "两段经历都保留，相关人物统一称为老同学。",
                    }
                ],
            }
        else:
            section_materials = _prompt_section_materials(user_prompt)
            is_revision = "用户修改要求：" in user_prompt
            payload = {
                "sections": [
                    {
                        "section_id": section_id,
                        "title": "比赛后的回想风" if is_revision else "",
                        "content": ("那天刮着风，我参加了学校比赛。" if is_revision else "那年，我参加了学校比赛。")
                        + "这段经历让我一直记得，也让我第一次认真看待输赢。",
                        "used_material_ids": allowed_material_ids,
                    }
                    for section_id, allowed_material_ids in section_materials
                ],
                "missing_source_candidates": [],
                "editor_notes": [],
            }
        return json.dumps(payload, ensure_ascii=False)


def test_book_review_restores_chapter_titles_and_promotes_editorial_changes():
    context = BookReviewContext(
        project_id="project-1",
        plan_id="plan-1",
        title="爷爷的故事",
        chapters=(
            BookReviewChapter(
                chapter_id="chapter-1",
                title="故园旧忆与 C2 家人琐记",
                sort_order=1,
                digest_id="digest-1",
                digest_signature="digest-signature-1",
                digest={},
                confirmed_draft_version_id="version-1",
                confirmed_draft_hash="hash-1",
                material_ids=frozenset({"material-1"}),
            ),
            BookReviewChapter(
                chapter_id="chapter-2",
                title="工作与选择",
                sort_order=2,
                digest_id="digest-2",
                digest_signature="digest-signature-2",
                digest={},
                confirmed_draft_version_id="version-2",
                confirmed_draft_hash="hash-2",
                material_ids=frozenset({"material-2"}),
            ),
        ),
        input_signature="book-signature-1",
    )

    review = normalize_book_review(
        {
            "summary": "C1 讲家庭，C2 讲工作。",
            "strengths": ["C2 的职业主线完整。"],
            "structure_guidance": [
                "在 C1 与 C2 之间增加过渡。",
                *[f"普通编辑建议 {index}" for index in range(2, 9)],
            ],
            "candidate_issues": [
                {
                    "issue_type": "meaning",
                    "question": "书名是否应从 C1 的爷爷主题改为个人成长？",
                    "reason": "这是 C1、C2 共同决定的全书定位。",
                    "priority": "medium",
                    "chapter_ids": ["C1", "C2"],
                    "resolution": "editorial_change",
                }
            ],
        },
        context=context,
    )

    visible = json.dumps(review, ensure_ascii=False)
    assert "C1" not in visible
    assert review["summary"] == ("第1章《故园旧忆与 C2 家人琐记》 讲家庭，第2章《工作与选择》 讲工作。")
    assert "第1章《故园旧忆与 C2 家人琐记》" in visible
    assert "第2章《工作与选择》" in visible
    assert review["candidate_issues"] == []
    assert len(review["structure_guidance"]) == 8
    assert "书名是否应" in review["structure_guidance"][0]


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


async def test_book_review_migration_creates_review_and_revision_proposal_tables():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(text("DROP TABLE memoir_book_revision_proposals"))
            await conn.execute(text("DROP TABLE memoir_book_reviews"))

        first = await migrate_memoir_book_schema(engine)
        second = await migrate_memoir_book_schema(engine)

        assert first == {
            "changed_tables": [
                "memoir_book_reviews",
                "memoir_book_revision_proposals",
            ],
            "changed_columns": [],
        }
        assert second == {
            "changed_tables": [],
            "changed_columns": [],
        }
        await assert_memoir_book_schema(engine)
    finally:
        await engine.dispose()


async def test_book_review_migration_adds_revision_plan_columns():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(text("ALTER TABLE memoir_book_revision_proposals DROP COLUMN revision_plan_json"))
            await conn.execute(text("ALTER TABLE memoir_book_revision_proposals DROP COLUMN phase"))

        first = await migrate_memoir_book_schema(engine)
        second = await migrate_memoir_book_schema(engine)

        assert first == {
            "changed_tables": [],
            "changed_columns": [
                "memoir_book_revision_proposals.phase",
                "memoir_book_revision_proposals.revision_plan_json",
            ],
        }
        assert second == {"changed_tables": [], "changed_columns": []}
        await assert_memoir_book_schema(engine)
    finally:
        await engine.dispose()


def _prompt_ids(prompt: str, key: str) -> list[str]:
    prefix = f"{key}: "
    return [line.removeprefix(prefix).strip() for line in prompt.splitlines() if line.startswith(prefix)]


def _prompt_section_materials(prompt: str) -> list[tuple[str, list[str]]]:
    result: list[tuple[str, list[str]]] = []
    current_materials: list[str] | None = None
    for line in prompt.splitlines():
        if line.startswith("section_id: "):
            current_materials = []
            result.append((line.removeprefix("section_id: ").strip(), current_materials))
        elif line.startswith("material_id: ") and current_materials is not None:
            current_materials.append(line.removeprefix("material_id: ").strip())
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
    monkeypatch.setattr(book_revision_generation, "get_ai_service", factory)


async def _run_chapter_preparation(
    client,
    test_db,
    *,
    project_id: str,
    chapter_id: str,
    operation: str = "prepare",
    ai_model: str | None = None,
):
    queued = await client.post(
        f"/api/memoir/projects/{project_id}/book-completion/chapters/{chapter_id}/jobs",
        json={"operation": operation, "ai_model": ai_model},
    )
    assert queued.status_code == 202, queued.text
    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        assert await run_test_editing_job(
            test_db,
            queued.json()["job_id"],
        ) is True
    finally:
        clear_editing_job_handlers()
    return await client.get(
        f"/api/memoir/projects/{project_id}/chapters/{chapter_id}/workspace",
    )


async def _run_book_review(
    client,
    test_db,
    *,
    project_id: str,
    force_regenerate: bool = False,
    ai_model: str | None = None,
):
    queued = await client.post(
        f"/api/memoir/projects/{project_id}/book-review/jobs",
        json={
            "force_regenerate": force_regenerate,
            "ai_model": ai_model,
        },
    )
    assert queued.status_code == 202, queued.text
    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        assert await run_test_editing_job(
            test_db,
            queued.json()["job_id"],
        ) is True
    finally:
        clear_editing_job_handlers()
    snapshot = await client.get(
        f"/api/memoir/projects/{project_id}/book-completion/overview",
    )
    return Response(
        snapshot.status_code,
        json=snapshot.json()["book_review"],
        request=snapshot.request,
    )


async def _completion_field(client, project_id: str, field: str):
    response = await client.get(
        f"/api/memoir/projects/{project_id}/book-completion/overview"
    )
    assert response.status_code == 200, response.text
    return response.json()[field]


def test_book_review_answer_output_rejects_structure_changes():
    valid = {
        "section_revisions": [
            {
                "section_id": "S1",
                "content": "修改后的完整小节正文。",
            }
        ],
    }

    assert book_revision_generation._RevisionOutput.model_validate(valid).model_dump() == valid
    with pytest.raises(ValueError):
        book_revision_generation._RevisionOutput.model_validate(
            {
                **valid,
                "summary": "补充 C3 章节并调整 S19 小节。",
            }
        )
    with pytest.raises(ValueError):
        book_revision_generation._RevisionOutput.model_validate(
            {
                **valid,
                "chapter_order": ["C1"],
            }
        )
    with pytest.raises(ValueError):
        book_revision_generation._RevisionOutput.model_validate(
            {
                **valid,
                "section_revisions": [
                    {
                        **valid["section_revisions"][0],
                        "title": "模型不应改名",
                    }
                ],
            }
        )


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
        chapter_titles=[f"学校里的比赛{index + 1}" for index in range(chapter_count)],
        source_refs_by_chapter=[[memoir_source_ref(source)] for source in sources],
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
    version.used_material_ids_json = [item.material_id for item in context.materials]
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
            "input_signature": chapter_generation._section_input_signature(
                context,
                section,
            ),
            "content": version.content,
            "used_material_ids": [material.material_id for material in section.materials],
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

    overview = await client.get(f"/api/memoir/projects/{project_id}/book-completion/overview")

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
    fake = LayeredBookAI(chapter_interview_issue=True)
    _install_ai(monkeypatch, fake)

    prepared = await _run_chapter_preparation(
        client,
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
    )

    assert prepared.status_code == 200, prepared.text
    body = prepared.json()
    assert body["completion_summary"]["state"] == "draft_ready"
    assert body["draft_version_id"] == version.id
    assert body["draft_content"] == "这是已经存在的完整章节正文。"
    assert not any("请把下面的原始讲述整理为回忆录章节" in prompt for prompt in fake.prompts)
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

    first = await _run_chapter_preparation(
        client,
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
        ai_model="claude-opus-4-8",
    )

    assert first.status_code == 200, first.text
    assert requested_models
    assert set(requested_models) == {"claude-opus-4-8"}
    project = await test_db.get(Project, project_id)
    assert project is not None
    assert project.metadata_json["ai_model"] == "claude-opus-4-8"

    requested_models.clear()
    second = await _run_chapter_preparation(
        client,
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
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
        f"/api/memoir/projects/{project_id}/book-completion/chapters/{chapter.id}/jobs",
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

    overview = await client.get(f"/api/memoir/projects/{project_id}/book-completion/overview")
    assert overview.status_code == 200, overview.text
    assert overview.json()["chapters"][0]["state"] == "preparing"
    assert overview.json()["chapters"][0]["next_action"]["action_text"] == ("查看进度")

    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        assert (
            await run_test_editing_job(
                test_db,
                queued_body["job_id"],
            )
            is True
        )
    finally:
        clear_editing_job_handlers()
    current = await client.get(f"/api/memoir/projects/{project_id}/book-completion/chapters/{chapter.id}/jobs/current")

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

    idle = await _completion_field(client, project_id, "completion_job")
    assert idle["status"] == "idle"
    assert idle["job_id"] is None

    queued = await client.post(
        f"/api/memoir/projects/{project_id}/book-completion/jobs",
        json={
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
        json={},
    )

    assert duplicate.status_code == 202, duplicate.text
    assert duplicate.json()["job_id"] == queued_body["job_id"]
    assert [item["job_id"] for item in duplicate.json()["chapters"]] == child_job_ids
    batch_count = await test_db.scalar(select(func.count()).select_from(MemoirBookCompletionJob))
    assert batch_count == 1

    resumed = await _completion_field(client, project_id, "completion_job")
    assert resumed["job_id"] == queued_body["job_id"]
    assert [item["job_id"] for item in resumed["chapters"]] == child_job_ids

    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        assert await run_test_editing_job(test_db, child_job_ids[0]) is True
    finally:
        clear_editing_job_handlers()

    partial = await _completion_field(client, project_id, "completion_job")
    assert partial["status"] == "running"
    assert partial["processed_chapter_count"] == 1
    assert partial["succeeded_chapter_count"] == 1
    assert partial["current_chapter_id"] == chapters[1].id

    failed_child = await test_db.get(MemoirEditingJob, child_job_ids[1])
    assert failed_child is not None
    failed_child.status = MemoirEditingJobStatus.FAILED.value
    failed_child.error_message = "provider unavailable"
    test_db.add(failed_child)
    await test_db.commit()

    failed_body = await _completion_field(client, project_id, "completion_job")
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
    assert failed_batch.status == MemoirBookCompletionJobStatus.RUNNING.value
    assert failed_batch.active_key is not None
    assert failed_batch.completed_at is None

    retried = await client.post(
        f"/api/memoir/projects/{project_id}/book-completion/jobs",
        json={
            "retry_job_id": queued_body["job_id"],
        },
    )

    assert retried.status_code == 202, retried.text
    retried_body = retried.json()
    assert retried_body["job_id"] != queued_body["job_id"]
    assert retried_body["status"] == "running"
    assert retried_body["total_chapter_count"] == 1
    assert retried_body["chapters"][0]["chapter_id"] == chapters[1].id
    assert retried_body["chapters"][0]["job_id"] != child_job_ids[1]
    await test_db.refresh(failed_batch)
    assert failed_batch.status == MemoirBookCompletionJobStatus.FAILED.value
    assert failed_batch.active_key is None
    assert failed_batch.error_message == "provider unavailable"
    assert failed_batch.completed_at is not None

    stale_cancel = await client.delete(
        f"/api/memoir/projects/{project_id}/book-completion/jobs/{queued_body['job_id']}"
    )
    assert stale_cancel.status_code == 200, stale_cancel.text
    assert stale_cancel.json()["status"] == "failed"
    assert stale_cancel.json()["processed_chapter_count"] == 2
    assert [item["status"] for item in stale_cancel.json()["chapters"]] == [
        "succeeded",
        "failed",
    ]
    current_retry = await _completion_field(client, project_id, "completion_job")
    assert current_retry["job_id"] == retried_body["job_id"]
    assert current_retry["status"] == "running"

    cancelled = await client.delete(f"/api/memoir/projects/{project_id}/book-completion/jobs/{retried_body['job_id']}")

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


async def test_book_completion_overview_does_not_resume_or_update_job(
    client,
    test_db,
):
    project_id, chapters, _ = await _book(client, test_db, chapter_count=2)
    batch = MemoirBookCompletionJob(
        project_id=project_id,
        active_key=f"book-completion:{project_id}",
        status=MemoirBookCompletionJobStatus.PENDING.value,
        target_chapters_json=[
            {"chapter_id": chapter.id, "title": chapter.title}
            for chapter in chapters
        ],
        chapter_job_ids_json={},
        enqueue_errors_json={},
    )
    test_db.add(batch)
    await test_db.commit()

    snapshot = await _completion_field(client, project_id, "completion_job")

    assert snapshot["job_id"] == batch.id
    assert snapshot["status"] == "pending"
    assert snapshot["processed_chapter_count"] == 0
    assert all(item["job_id"] is None for item in snapshot["chapters"])
    await test_db.refresh(batch)
    assert batch.status == MemoirBookCompletionJobStatus.PENDING.value
    assert batch.chapter_job_ids_json == {}
    assert batch.active_key == f"book-completion:{project_id}"
    child_count = await test_db.scalar(
        select(func.count())
        .select_from(MemoirEditingJob)
        .where(
            MemoirEditingJob.project_id == project_id,
            MemoirEditingJob.job_type
            == MemoirEditingJobType.PREPARE_CHAPTER.value,
        )
    )
    assert child_count == 0


async def test_book_completion_job_does_not_enqueue_after_concurrent_cancel(
    client,
    test_db,
    monkeypatch,
):
    project_id, _, _ = await _book(client, test_db, chapter_count=2)
    original_enqueue = chapter_preparation_jobs.enqueue_chapter_preparation_job
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
        json={},
    )

    assert queued.status_code == 202, queued.text
    assert queued.json()["status"] == "cancelled"
    assert queued.json()["cancelled_chapter_count"] == 2
    child_jobs = (
        await test_db.scalars(
            select(MemoirEditingJob).where(
                MemoirEditingJob.project_id == project_id,
                MemoirEditingJob.job_type == MemoirEditingJobType.PREPARE_CHAPTER.value,
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

    response = await client.get(f"/api/memoir/projects/{project_id}/chapters/{chapter.id}/workspace")

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

    prepared = await _run_chapter_preparation(
        client,
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
    )

    assert prepared.status_code == 200, prepared.text
    summary = prepared.json()["completion_summary"]
    assert summary["state"] == "needs_input"
    assert summary["next_action"]["kind"] == "confirm"
    assert summary["active_issue"]["question"] == "这一章是否愿意保留真实学校名称？"
    rewritten = await client.post(
        f"/api/memoir/projects/{project_id}/book-completion/chapters/{chapter.id}/jobs",
        json={"operation": "rewrite"},
    )
    assert rewritten.status_code == 202
    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        assert await run_test_editing_job(
            test_db,
            rewritten.json()["job_id"],
        ) is False
    finally:
        clear_editing_job_handlers()
    failed = await client.get(
        f"/api/memoir/projects/{project_id}/book-completion/"
        f"chapters/{chapter_id}/jobs/current",
    )
    assert failed.json()["error_message"] == "本章还有一项需要您先确认"


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

    prepared = await _run_chapter_preparation(
        client,
        test_db,
        project_id=project_id,
        chapter_id=chapter_id,
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
    assert any("这件事后来对你有什么影响" in prompt for prompt in fake.prompts if "请审阅回忆录章节" in prompt)


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

    rewritten = await _run_chapter_preparation(
        client,
        test_db,
        project_id=project_id,
        chapter_id=chapter_id,
        operation="rewrite",
        ai_model="claude-opus-4-8",
    )

    assert rewritten.status_code == 200, rewritten.text
    body = rewritten.json()
    assert body["completion_summary"]["state"] == "draft_ready"
    assert len(body["sections"]) == 4
    assert len(body["draft_sections"]) == 4
    assert [item["section_id"] for item in body["draft_sections"]] == [item["id"] for item in body["sections"]]
    assert len(fake.prompts) == 3
    assert all("请把下面的原始讲述整理为回忆录章节" in prompt for prompt in fake.prompts)
    assert all(prompt.count("material_id: ") == 1 for prompt in fake.prompts)
    assert body["draft_sections"][0]["content"] == "这是已经存在的完整章节正文。"
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
        f"/api/memoir/projects/{project_id}/chapters/{chapter.id}/draft-sections/{target_section.id}/ai-revise",
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
    assert body["draft_sections"][0]["content"] == ("这是已经存在的完整章节正文。")
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
    assert latest.feedback == ("AI 按要求修改小节《比赛后的回想风》：把山峰的峰改成刮风的风。")
    await test_db.refresh(target_section)
    assert target_section.title == "比赛后的回想风"
    assert target_section.version == 2
    draft = await test_db.get(MemoirBookChapterDraft, version.draft_id)
    assert draft is not None
    assert draft.metadata_json["last_requested_ai_model"] == "claude-opus-4-8"
    assert draft.metadata_json["last_resolved_ai_model"] == "test-layered-book"

    stale = await client.post(
        f"/api/memoir/projects/{project_id}/chapters/{chapter.id}/draft-sections/{target_section.id}/ai-revise",
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

    async def fake_chapter_summary(db, *, project_id, chapter, snapshot):
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
    assert overview.detail == ("1 章待检查，1 章待确认，1 章待通读。每章只显示当前最重要的一步。")


async def test_book_completion_uses_background_chapter_preparation(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, _ = await _book(client, test_db)
    chapter = chapters[0]
    fake = LayeredBookAI(chapter_interview_issue=True)
    _install_ai(monkeypatch, fake)

    initial = await client.get(f"/api/memoir/projects/{project_id}/book-completion/overview")
    assert initial.status_code == 200
    assert initial.json()["chapters"][0]["state"] == "not_started"

    prepared = await _run_chapter_preparation(
        client,
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
    )
    assert prepared.status_code == 200, prepared.text
    body = prepared.json()
    assert body["completion_summary"]["state"] == "draft_ready"
    assert body["draft_content"].startswith("那年，我参加了学校比赛")
    assert len(body["draft_sections"]) == 1
    assert body["draft_sections"][0]["content"] == body["draft_content"]
    assert body["draft_sections"][0]["used_material_ids"]
    original_version_id = body["draft_version_id"]
    preparation_job = await test_db.scalar(
        select(MemoirEditingJob).where(
            MemoirEditingJob.project_id == project_id,
            MemoirEditingJob.job_type
            == MemoirEditingJobType.PREPARE_CHAPTER.value,
        )
    )
    assert preparation_job is not None
    assert preparation_job.status == MemoirEditingJobStatus.SUCCEEDED.value

    revised = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter.id}/draft/revise",
        json={
            "expected_version_id": original_version_id,
            "sections": [
                {
                    "section_id": body["draft_sections"][0]["section_id"],
                    "content": ("那年，我参加了学校比赛。这件事至今仍留在记忆里。"),
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
    assert revised.json()["latest_version"]["sections"][0]["content"].endswith("这件事至今仍留在记忆里。")
    assert revised_version.input_signature
    assert revised_version.used_material_ids_json

    confirmed = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter.id}/draft/confirm"
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "confirmed"
    assert (
        await test_db.scalar(
            select(func.count())
            .select_from(MemoirCompletionIssue)
            .where(
                MemoirCompletionIssue.project_id == project_id,
                MemoirCompletionIssue.status == "open",
            )
        )
        == 1
    )
    overview = await client.get(f"/api/memoir/projects/{project_id}/book-completion/overview")
    assert overview.json()["chapters"][0]["state"] == "confirmed"


async def test_chapter_reorder_preserves_checks_and_confirmed_drafts(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, _ = await _book(
        client,
        test_db,
        chapter_count=2,
    )
    fake = LayeredBookAI()
    _install_ai(monkeypatch, fake)

    for chapter in chapters:
        prepared = await _run_chapter_preparation(
            client,
            test_db,
            project_id=project_id,
            chapter_id=chapter.id,
        )
        assert prepared.status_code == 200, prepared.text
        confirmed = await client.post(
            f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter.id}/draft/confirm"
        )
        assert confirmed.status_code == 200, confirmed.text

        version = await test_db.scalar(
            select(MemoirBookChapterDraftVersion)
            .where(
                MemoirBookChapterDraftVersion.project_id == project_id,
                MemoirBookChapterDraftVersion.chapter_id == chapter.id,
            )
            .order_by(MemoirBookChapterDraftVersion.version.desc())
        )
        assert version is not None
        version.input_signature = f"legacy-order-{chapter.sort_order}"
        test_db.add(version)
    await test_db.commit()
    reviewed = await _run_book_review(
        client,
        test_db,
        project_id=project_id,
    )
    assert reviewed.status_code == 200, reviewed.text
    prompt_count = len(fake.prompts)

    moved = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/structure-commands",
        json={
            "type": "move_chapter",
            "chapter_id": chapters[1].id,
            "direction": "up",
        },
    )
    assert moved.status_code == 200, moved.text

    overview = await client.get(f"/api/memoir/projects/{project_id}/book-completion/overview")
    assert overview.status_code == 200, overview.text
    assert [item["chapter_id"] for item in overview.json()["chapters"]] == [chapters[1].id, chapters[0].id]
    assert [item["state"] for item in overview.json()["chapters"]] == ["confirmed", "confirmed"]
    for chapter in chapters:
        workspace = await client.get(f"/api/memoir/projects/{project_id}/chapters/{chapter.id}/workspace")
        assert workspace.status_code == 200, workspace.text
        assert workspace.json()["draft_needs_rewrite"] is False
    current_review = await _completion_field(
        client,
        project_id,
        "book_review",
    )
    assert current_review is None
    assert len(fake.prompts) == prompt_count


async def test_section_revision_rejects_changed_section_boundary(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, _ = await _book(client, test_db)
    chapter = chapters[0]
    _install_ai(monkeypatch, LayeredBookAI())
    prepared = await _run_chapter_preparation(
        client,
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
    )
    assert prepared.status_code == 200, prepared.text
    body = prepared.json()

    revised = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter.id}/draft/revise",
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

    workspace_response = await client.get(f"/api/memoir/projects/{project_id}/chapters/{chapters[0].id}/workspace")
    assert workspace_response.status_code == 200
    workspace = workspace_response.json()
    assert workspace["draft_needs_rewrite"] is True
    assert workspace["can_rewrite_draft"] is True
    assert workspace["draft_sections"] == []

    confirmed = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapters[0].id}/draft/confirm"
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

    prepared = await _run_chapter_preparation(
        client,
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
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
    requested_models: list[str | None] = []
    _install_ai(monkeypatch, fake, requested_models)
    bootstrapped = await client.post(f"/api/memoir/projects/{project_id}/sections/bootstrap")
    assert bootstrapped.status_code == 200
    for chapter in chapters:
        await _create_current_draft(
            test_db,
            project_id=project_id,
            chapter_id=chapter.id,
            status=MemoirBookChapterDraftStatus.CONFIRMED,
        )
    unreviewed = await client.post(
        f"/api/memoir/projects/{project_id}/book-output/assemble",
    )
    assert unreviewed.status_code == 409
    assert "完成当前版本的全书检查" in unreviewed.json()["detail"]

    generated = await _run_book_review(
        client,
        test_db,
        project_id=project_id,
        ai_model="claude-opus-4-8",
    )
    assert generated.status_code == 200
    review = generated.json()

    assert review["status"] == "ready"
    assert set(requested_models) == {"claude-opus-4-8"}
    book_prompt = next(prompt for prompt in fake.prompts if "请审阅下面这本回忆录的全书结构" in prompt)
    assert all(chapter.id not in book_prompt for chapter in chapters)
    assert "chapter_id: C1" in book_prompt
    assert review["review"]["summary"] == "全书记录了几段重要成长经历。"
    assert "candidate_issues" not in review["review"]
    assert "generation" not in review["review"]
    issue = await test_db.scalar(
        select(MemoirCompletionIssue).where(
            MemoirCompletionIssue.project_id == project_id,
            MemoirCompletionIssue.scope == MemoirCompletionIssueScope.BOOK.value,
        )
    )
    assert issue is not None
    issue_id = issue.id
    assert issue.channel == "manual_review"
    assert issue.related_material_ids_json
    current = await _completion_field(client, project_id, "book_review")
    assert current["id"] == review["id"]
    blocked = await client.post(
        f"/api/memoir/projects/{project_id}/book-output/assemble",
    )
    assert blocked.status_code == 409
    assert "全书检查中需要确认的问题" in blocked.json()["detail"]
    closed = await client.post(
        f"/api/memoir/projects/{project_id}/completion-issues/{issue_id}/close",
        json={"outcome": "confirmed"},
    )
    assert closed.status_code == 200
    assembled = await client.post(
        f"/api/memoir/projects/{project_id}/book-output/assemble",
    )
    assert assembled.status_code == 200
    assert (
        await test_db.scalar(select(MemoirBookReview.id).where(MemoirBookReview.project_id == project_id))
        == review["id"]
    )


async def test_book_review_job_persists_across_requests_and_runs_in_worker(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, _ = await _book(client, test_db, chapter_count=2)
    fake = LayeredBookAI(book_issue=True)
    requested_models: list[str | None] = []
    _install_ai(monkeypatch, fake, requested_models)
    bootstrapped = await client.post(f"/api/memoir/projects/{project_id}/sections/bootstrap")
    assert bootstrapped.status_code == 200
    for chapter in chapters:
        await _create_current_draft(
            test_db,
            project_id=project_id,
            chapter_id=chapter.id,
            status=MemoirBookChapterDraftStatus.CONFIRMED,
        )

    idle = await _completion_field(client, project_id, "book_review_job")
    assert idle["status"] == "idle"

    queued = await client.post(
        f"/api/memoir/projects/{project_id}/book-review/jobs",
        json={
            "force_regenerate": False,
            "ai_model": "claude-opus-4-8",
        },
    )

    assert queued.status_code == 202, queued.text
    queued_body = queued.json()
    assert queued_body["status"] == "pending"
    assert queued_body["stage"] == "queued"
    assert queued_body["total_chapter_count"] == 2
    assert queued_body["processed_chapter_count"] == 0
    assert queued_body["can_cancel"] is True
    assert (
        await test_db.scalar(
            select(func.count()).select_from(MemoirBookReview).where(MemoirBookReview.project_id == project_id)
        )
        == 0
    )

    duplicate = await client.post(
        f"/api/memoir/projects/{project_id}/book-review/jobs",
        json={"force_regenerate": False},
    )
    assert duplicate.status_code == 202
    assert duplicate.json()["job_id"] == queued_body["job_id"]
    assert (
        await test_db.scalar(
            select(func.count())
            .select_from(MemoirEditingJob)
            .where(
                MemoirEditingJob.project_id == project_id,
                MemoirEditingJob.job_type == MemoirEditingJobType.GENERATE_BOOK_REVIEW.value,
            )
        )
        == 1
    )

    resumed = await _completion_field(client, project_id, "book_review_job")
    assert resumed["job_id"] == queued_body["job_id"]
    assert resumed["stage"] == "queued"

    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        assert (
            await run_test_editing_job(
                test_db,
                queued_body["job_id"],
            )
            is True
        )
    finally:
        clear_editing_job_handlers()

    completed_body = await _completion_field(
        client,
        project_id,
        "book_review_job",
    )
    assert completed_body["status"] == "succeeded"
    assert completed_body["stage"] == "completed"
    assert completed_body["processed_chapter_count"] == 2
    assert completed_body["review_id"]
    assert completed_body["can_cancel"] is False
    assert completed_body["requested_ai_model"] == "claude-opus-4-8"
    assert completed_body["resolved_ai_model"] == fake.model_name
    assert set(requested_models) == {"claude-opus-4-8"}

    snapshot = await client.get(
        f"/api/memoir/projects/{project_id}/book-completion/overview"
    )
    assert snapshot.status_code == 200
    snapshot_body = snapshot.json()
    assert snapshot_body["book_review"]["id"] == completed_body["review_id"]
    assert snapshot_body["book_review"]["status"] == "ready"
    assert snapshot_body["book_review_job"]["status"] == "succeeded"
    assert snapshot_body["book_review_job"]["review_id"] == completed_body["review_id"]
    assert len(snapshot_body["book_issues"]) == 1
    assert snapshot_body["book_output_available"] is False
    assert snapshot_body["answer_application_job"]["unresolved_count"] == 1
    assert snapshot_body["editorial_revision_job"]["status"] == "idle"

    requeued = await client.post(
        f"/api/memoir/projects/{project_id}/book-review/jobs",
        json={"force_regenerate": True},
    )
    assert requeued.status_code == 202
    assert requeued.json()["job_id"] == queued_body["job_id"]
    assert requeued.json()["status"] == "pending"
    cancelled = await client.delete(f"/api/memoir/projects/{project_id}/book-review/jobs/{queued_body['job_id']}")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["can_retry"] is True


async def test_book_review_answers_plan_feedback_draft_and_apply(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, _ = await _book(
        client,
        test_db,
        chapter_count=3,
    )
    fake = LayeredBookAI(book_issue=True, book_actions=True)
    requested_models: list[str | None] = []
    _install_ai(monkeypatch, fake, requested_models)
    assert (await client.post(f"/api/memoir/projects/{project_id}/sections/bootstrap")).status_code == 200
    for chapter in chapters:
        await _create_current_draft(
            test_db,
            project_id=project_id,
            chapter_id=chapter.id,
            status=MemoirBookChapterDraftStatus.CONFIRMED,
        )

    generated = await _run_book_review(
        client,
        test_db,
        project_id=project_id,
    )
    assert generated.status_code == 200, generated.text
    issue = await test_db.scalar(
        select(MemoirCompletionIssue).where(
            MemoirCompletionIssue.project_id == project_id,
            MemoirCompletionIssue.scope == MemoirCompletionIssueScope.BOOK.value,
        )
    )
    assert issue is not None
    blocked_editorial = await client.post(
        f"/api/memoir/projects/{project_id}/book-review/editorial-revision/jobs",
        json={"action_ids": [action["id"] for action in generated.json()["review"]["editorial_actions"]]},
    )
    assert blocked_editorial.status_code == 409
    assert "先处理完所有需要确认的问题" in blocked_editorial.json()["detail"]
    blocked_state = await _completion_field(
        client,
        project_id,
        "answer_application_job",
    )
    assert blocked_state["answer_count"] == 0
    assert blocked_state["unresolved_count"] == 1
    assert blocked_state["can_apply"] is False
    closed = await client.post(
        f"/api/memoir/projects/{project_id}/completion-issues/{issue.id}/close",
        json={
            "outcome": "confirmed",
            "note": "两段经历发生在不同阶段，我希望都保留。",
        },
    )
    assert closed.status_code == 200

    unapplied = await client.post(f"/api/memoir/projects/{project_id}/book-output/assemble")
    assert unapplied.status_code == 409
    assert "根据全书检查里的回答调整书稿" in unapplied.json()["detail"]

    idle = await _completion_field(client, project_id, "answer_application_job")
    assert idle["status"] == "idle"
    assert idle["answer_count"] == 1
    assert idle["can_apply"] is True

    queued = await client.post(
        f"/api/memoir/projects/{project_id}/book-review/answer-application/jobs",
        json={"ai_model": "claude-opus-4-8"},
    )
    assert queued.status_code == 202, queued.text
    queued_body = queued.json()
    assert queued_body["status"] == "pending"
    assert queued_body["stage"] == "queued"
    assert queued_body["answer_count"] == 1
    assert queued_body["can_apply"] is False

    cancelled = await client.delete(
        f"/api/memoir/projects/{project_id}/book-review/answer-application/jobs/{queued_body['job_id']}"
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    retried = await client.post(
        f"/api/memoir/projects/{project_id}/book-review/answer-application/jobs",
        json={"ai_model": "claude-opus-4-8"},
    )
    assert retried.status_code == 202, retried.text
    assert retried.json()["job_id"] == queued_body["job_id"]

    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        assert (
            await run_test_editing_job(
                test_db,
                queued_body["job_id"],
            )
            is True
        )
    finally:
        clear_editing_job_handlers()

    completed_body = await _completion_field(
        client,
        project_id,
        "answer_application_job",
    )
    assert completed_body["status"] == "succeeded"
    assert completed_body["stage"] == "completed"
    assert completed_body["answer_count"] == 1
    assert completed_body["changed_chapter_count"] == 0
    assert completed_body["proposal_id"]
    assert completed_body["proposal_status"] == "ready"
    assert completed_body["proposal_phase"] == "plan_ready"
    assert completed_body["resolved_ai_model"] == fake.model_name
    assert completed_body["summary"] == "先保留两段经历，再统一相关人物称谓。"
    assert "C1" not in completed_body["summary"]
    assert "S1" not in completed_body["summary"]
    assert set(requested_models) == {None, "claude-opus-4-8"}

    versions = list(
        (
            await test_db.scalars(
                select(MemoirBookOutputVersion)
                .where(MemoirBookOutputVersion.project_id == project_id)
                .order_by(MemoirBookOutputVersion.version)
            )
        ).all()
    )
    assert len(versions) == 1
    assert "我决定都保留在书里" not in versions[0].content

    proposal = await client.get(
        f"/api/memoir/projects/{project_id}/book-review/revision-proposals/{completed_body['proposal_id']}?preview=none"
    )
    assert proposal.status_code == 200, proposal.text
    proposal_body = proposal.json()
    assert proposal_body["status"] == "ready"
    assert proposal_body["phase"] == "plan_ready"
    assert proposal_body["can_apply"] is False
    assert proposal_body["can_dismiss"] is True
    assert proposal_body["comparison"]["changed_chapter_count"] == 0
    assert proposal_body["comparison"]["changed_section_count"] == 0
    assert proposal_body["revision_plan"]["items"][0]["title"] == ("保留经历并统一称谓")
    assert proposal_body["before"]["manuscript"] is None
    assert proposal_body["after"]["manuscript"] is None
    before_preview = await client.get(
        f"/api/memoir/projects/{project_id}/book-review/"
        f"revision-proposals/{completed_body['proposal_id']}?preview=before"
    )
    after_preview = await client.get(
        f"/api/memoir/projects/{project_id}/book-review/"
        f"revision-proposals/{completed_body['proposal_id']}?preview=after"
    )
    before_content = before_preview.json()["before"]["manuscript"]["chapters"][0]["content"]
    assert before_preview.json()["after"]["manuscript"] is None
    assert after_preview.json()["before"]["manuscript"] is None
    assert after_preview.json()["after"]["manuscript"] is None
    assert "我决定都保留在书里" not in before_content

    feedback_job = await client.post(
        f"/api/memoir/projects/{project_id}/book-review/"
        f"revision-proposals/{completed_body['proposal_id']}/feedback/jobs",
        json={
            "feedback": "保留两段经历，但请统一相关人物的称谓。",
            "ai_model": "claude-opus-4-8",
        },
    )
    assert feedback_job.status_code == 202, feedback_job.text
    feedback_job_body = feedback_job.json()
    assert feedback_job_body["status"] == "pending"
    assert feedback_job_body["proposal_id"] == completed_body["proposal_id"]
    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        feedback_succeeded = await run_test_editing_job(
            test_db,
            feedback_job_body["job_id"],
        )
        failed_feedback_job = await test_db.get(
            MemoirEditingJob,
            feedback_job_body["job_id"],
        )
        assert feedback_succeeded is True, failed_feedback_job.error_message
    finally:
        clear_editing_job_handlers()
    refined = await client.get(
        f"/api/memoir/projects/{project_id}/book-review/"
        f"revision-proposals/{completed_body['proposal_id']}/feedback/jobs/current"
    )
    assert refined.status_code == 200, refined.text
    assert refined.json()["status"] == "succeeded"
    assert refined.json()["changed_chapter_count"] == 0
    assert refined.json()["proposal_phase"] == "plan_ready"
    proposal = await client.get(
        f"/api/memoir/projects/{project_id}/book-review/"
        f"revision-proposals/{completed_body['proposal_id']}?preview=after"
    )
    assert proposal.status_code == 200, proposal.text
    proposal_body = proposal.json()
    assert proposal_body["id"] == completed_body["proposal_id"]
    assert proposal_body["phase"] == "plan_ready"
    assert proposal_body["after"]["manuscript"] is None
    assert "老同学" in (proposal_body["revision_plan"]["items"][0]["approach"])
    versions = list(
        (
            await test_db.scalars(
                select(MemoirBookOutputVersion).where(MemoirBookOutputVersion.project_id == project_id)
            )
        ).all()
    )
    assert len(versions) == 1

    draft_job = await client.post(
        f"/api/memoir/projects/{project_id}/book-review/revision-proposals/{completed_body['proposal_id']}/draft/jobs",
        json={"ai_model": "claude-opus-4-8"},
    )
    assert draft_job.status_code == 202, draft_job.text
    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        assert (
            await run_test_editing_job(
                test_db,
                draft_job.json()["job_id"],
            )
            is True
        )
    finally:
        clear_editing_job_handlers()
    drafted = await client.get(
        f"/api/memoir/projects/{project_id}/book-review/"
        f"revision-proposals/{completed_body['proposal_id']}/draft/jobs/current"
    )
    assert drafted.status_code == 200, drafted.text
    assert drafted.json()["status"] == "succeeded"
    assert drafted.json()["proposal_phase"] == "draft_ready"
    proposal = await client.get(
        f"/api/memoir/projects/{project_id}/book-review/"
        f"revision-proposals/{completed_body['proposal_id']}?preview=after"
    )
    assert proposal.json()["can_apply"] is True
    assert "老同学" in (proposal.json()["after"]["manuscript"]["chapters"][0]["content"])

    second_feedback = await client.post(
        f"/api/memoir/projects/{project_id}/book-review/"
        f"revision-proposals/{completed_body['proposal_id']}/feedback/jobs",
        json={"feedback": "再精简一些。"},
    )
    assert second_feedback.status_code == 202, second_feedback.text
    assert second_feedback.json()["job_id"] == feedback_job_body["job_id"]
    assert second_feedback.json()["status"] == "pending"
    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        assert (
            await run_test_editing_job(
                test_db,
                feedback_job_body["job_id"],
            )
            is True
        )
    finally:
        clear_editing_job_handlers()
    replanned = await client.get(
        f"/api/memoir/projects/{project_id}/book-review/"
        f"revision-proposals/{completed_body['proposal_id']}?preview=after"
    )
    assert replanned.json()["phase"] == "plan_ready"
    assert replanned.json()["can_apply"] is False
    assert replanned.json()["after"]["manuscript"] is None

    second_draft = await client.post(
        f"/api/memoir/projects/{project_id}/book-review/revision-proposals/{completed_body['proposal_id']}/draft/jobs",
        json={},
    )
    assert second_draft.status_code == 202, second_draft.text
    assert second_draft.json()["job_id"] == draft_job.json()["job_id"]
    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        assert (
            await run_test_editing_job(
                test_db,
                second_draft.json()["job_id"],
            )
            is True
        )
    finally:
        clear_editing_job_handlers()

    review_record = await test_db.get(MemoirBookReview, generated.json()["id"])
    assert review_record is not None
    review_record.status = "failed"
    test_db.add(review_record)
    await test_db.commit()
    stale = await client.get(
        f"/api/memoir/projects/{project_id}/book-review/revision-proposals/{completed_body['proposal_id']}"
    )
    assert stale.status_code == 200
    assert stale.json()["status"] == "stale"
    assert stale.json()["can_apply"] is False
    rejected = await client.post(
        f"/api/memoir/projects/{project_id}/book-review/revision-proposals/{completed_body['proposal_id']}/apply"
    )
    assert rejected.status_code == 409
    review_record.status = "ready"
    test_db.add(review_record)
    await test_db.commit()

    applied = await client.post(
        f"/api/memoir/projects/{project_id}/book-review/revision-proposals/{completed_body['proposal_id']}/apply"
    )
    assert applied.status_code == 200, applied.text
    applied_body = applied.json()
    assert applied_body["status"] == "applied"
    assert applied_body["can_apply"] is False
    assert applied_body["result_output_version_id"]

    versions = list(
        (
            await test_db.scalars(
                select(MemoirBookOutputVersion)
                .where(MemoirBookOutputVersion.project_id == project_id)
                .order_by(MemoirBookOutputVersion.version)
            )
        ).all()
    )
    assert len(versions) == 2
    assert "老同学" in versions[1].content
    assert versions[1].created_by == "ai"
    assert [item["chapter_id"] for item in versions[1].chapter_snapshot_json] == [chapter.id for chapter in chapters]
    assert versions[1].chapter_snapshot_json[1:] == versions[0].chapter_snapshot_json[1:]
    plan_prompt = next(prompt for prompt in fake.prompts if "请根据全书检查问题和用户回答，制定书稿修改思路" in prompt)
    assert "两段经历发生在不同阶段，我希望都保留" in plan_prompt
    assert all(chapter.id not in plan_prompt for chapter in chapters)
    draft_prompt = next(prompt for prompt in fake.prompts if "请按照已确认的修改思路生成书稿修改稿" in prompt)
    assert "统一称谓并精简重复" in draft_prompt
    assert all(chapter.id not in draft_prompt for chapter in chapters)
    assert "不改变章节和小节的标题、数量" in draft_prompt

    editorial_job = await client.post(
        f"/api/memoir/projects/{project_id}/book-review/editorial-revision/jobs",
        json={"action_ids": [action["id"] for action in generated.json()["review"]["editorial_actions"]]},
    )
    assert editorial_job.status_code == 202, editorial_job.text
    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        assert (
            await run_test_editing_job(
                test_db,
                editorial_job.json()["job_id"],
            )
            is True
        )
    finally:
        clear_editing_job_handlers()
    editorial_state = await _completion_field(
        client,
        project_id,
        "editorial_revision_job",
    )
    editorial_draft = await client.post(
        f"/api/memoir/projects/{project_id}/book-review/"
        f"revision-proposals/{editorial_state['proposal_id']}/draft/jobs",
        json={},
    )
    assert editorial_draft.status_code == 202, editorial_draft.text
    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        assert (
            await run_test_editing_job(
                test_db,
                editorial_draft.json()["job_id"],
            )
            is True
        )
    finally:
        clear_editing_job_handlers()
    editorial_applied = await client.post(
        f"/api/memoir/projects/{project_id}/book-review/"
        f"revision-proposals/{editorial_state['proposal_id']}/apply"
    )
    assert editorial_applied.status_code == 200, editorial_applied.text

    preserved_answer_state = await _completion_field(
        client,
        project_id,
        "answer_application_job",
    )
    assert preserved_answer_state["already_applied"] is True
    assert preserved_answer_state["can_apply"] is False


async def test_selected_editorial_actions_create_a_preview_before_applying(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, _ = await _book(client, test_db, chapter_count=2)
    fake = LayeredBookAI(book_actions=True)
    _install_ai(monkeypatch, fake)
    bootstrapped = await client.post(f"/api/memoir/projects/{project_id}/sections/bootstrap")
    assert bootstrapped.status_code == 200
    for chapter in chapters:
        await _create_current_draft(
            test_db,
            project_id=project_id,
            chapter_id=chapter.id,
            status=MemoirBookChapterDraftStatus.CONFIRMED,
        )

    generated = await _run_book_review(
        client,
        test_db,
        project_id=project_id,
    )
    assert generated.status_code == 200, generated.text
    actions = generated.json()["review"]["editorial_actions"]
    assert {action["action_type"] for action in actions} == {
        "remove_repetition",
        "improve_transition",
        "reorder_chapters",
    }
    assembled = await client.post(f"/api/memoir/projects/{project_id}/book-output/assemble")
    assert assembled.status_code == 200, assembled.text
    baseline_version_id = assembled.json()["output_version_id"]

    queued = await client.post(
        f"/api/memoir/projects/{project_id}/book-review/editorial-revision/jobs",
        json={"action_ids": [action["id"] for action in actions]},
    )
    assert queued.status_code == 202, queued.text
    assert queued.json()["selected_action_count"] == 3
    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        assert (
            await run_test_editing_job(
                test_db,
                queued.json()["job_id"],
            )
            is True
        )
    finally:
        clear_editing_job_handlers()

    completed_body = await _completion_field(
        client,
        project_id,
        "editorial_revision_job",
    )
    assert completed_body["status"] == "succeeded"
    assert completed_body["proposal_status"] == "ready"
    assert completed_body["proposal_phase"] == "plan_ready"
    assert (
        await test_db.scalar(
            select(func.count())
            .select_from(MemoirBookOutputVersion)
            .where(MemoirBookOutputVersion.project_id == project_id)
        )
        == 1
    )

    proposal = await client.get(
        f"/api/memoir/projects/{project_id}/book-review/revision-proposals/{completed_body['proposal_id']}"
    )
    assert proposal.status_code == 200, proposal.text
    assert proposal.json()["can_apply"] is False
    assert proposal.json()["revision_plan"]["items"]
    draft = await client.post(
        f"/api/memoir/projects/{project_id}/book-review/revision-proposals/{completed_body['proposal_id']}/draft/jobs",
        json={},
    )
    assert draft.status_code == 202, draft.text
    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        assert (
            await run_test_editing_job(
                test_db,
                draft.json()["job_id"],
            )
            is True
        )
    finally:
        clear_editing_job_handlers()
    proposal = await client.get(
        f"/api/memoir/projects/{project_id}/book-review/revision-proposals/{completed_body['proposal_id']}"
    )
    comparison = proposal.json()["comparison"]
    assert comparison["chapter_order_changed"] is True
    assert [item["chapter_id"] for item in comparison["after_chapter_order"]] == [chapters[1].id, chapters[0].id]
    assert comparison["changed_section_count"] == 1
    assert proposal.json()["before"]["version"] == 1
    assert proposal.json()["after"]["version"] == 2

    applied = await client.post(
        f"/api/memoir/projects/{project_id}/book-review/revision-proposals/{completed_body['proposal_id']}/apply"
    )
    assert applied.status_code == 200, applied.text
    applied_version_id = applied.json()["result_output_version_id"]
    versions = list(
        (
            await test_db.scalars(
                select(MemoirBookOutputVersion)
                .where(MemoirBookOutputVersion.project_id == project_id)
                .order_by(MemoirBookOutputVersion.version)
            )
        ).all()
    )
    assert [version.id for version in versions] == [
        baseline_version_id,
        applied_version_id,
    ]
    assert [item["chapter_id"] for item in versions[-1].chapter_snapshot_json] == [chapters[1].id, chapters[0].id]
    assert "老同学" in versions[-1].content

    reassembled = await client.post(f"/api/memoir/projects/{project_id}/book-output/assemble")
    assert reassembled.status_code == 200, reassembled.text
    assert reassembled.json()["output_version_id"] == applied_version_id


async def test_book_review_job_preserves_failure_and_can_retry(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, _ = await _book(client, test_db)
    await _create_current_draft(
        test_db,
        project_id=project_id,
        chapter_id=chapters[0].id,
        status=MemoirBookChapterDraftStatus.CONFIRMED,
    )

    async def fail_review(*args, progress_callback=None, **kwargs):
        assert progress_callback is not None
        await progress_callback(
            {
                "stage": "reviewing",
                "total_chapter_count": 1,
                "processed_chapter_count": 1,
                "current_chapter_title": "",
            }
        )
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(
        book_review_jobs.book_reviews,
        "generate_or_reuse_book_review",
        fail_review,
    )
    queued = await client.post(
        f"/api/memoir/projects/{project_id}/book-review/jobs",
        json={"force_regenerate": False},
    )
    assert queued.status_code == 202
    job_id = queued.json()["job_id"]

    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        assert await run_test_editing_job(test_db, job_id) is False
    finally:
        clear_editing_job_handlers()

    failed = await _completion_field(client, project_id, "book_review_job")
    assert failed["status"] == "failed"
    assert failed["stage"] == "reviewing"
    assert failed["error_message"] == "provider unavailable"
    assert failed["can_retry"] is True

    retried = await client.post(
        f"/api/memoir/projects/{project_id}/book-review/jobs",
        json={"force_regenerate": False},
    )
    assert retried.status_code == 202
    assert retried.json()["job_id"] == job_id
    assert retried.json()["status"] == "pending"
    assert retried.json()["stage"] == "queued"
    assert retried.json()["error_message"] == ""


async def test_book_review_job_requires_every_chapter_draft_to_be_confirmed(
    client,
    test_db,
):
    project_id, chapters, _ = await _book(client, test_db)

    queued = await client.post(
        f"/api/memoir/projects/{project_id}/book-review/jobs",
        json={},
    )

    assert queued.status_code == 409
    assert queued.json()["detail"] == (
        f"请先确认章节“{chapters[0].title}”的正文，再检查全书"
    )


async def test_book_review_uses_the_current_output_title(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, _ = await _book(client, test_db)
    fake = LayeredBookAI()
    _install_ai(monkeypatch, fake)
    bootstrapped = await client.post(f"/api/memoir/projects/{project_id}/sections/bootstrap")
    assert bootstrapped.status_code == 200
    await _create_current_draft(
        test_db,
        project_id=project_id,
        chapter_id=chapters[0].id,
        status=MemoirBookChapterDraftStatus.CONFIRMED,
    )
    reviewed = await _run_book_review(
        client,
        test_db,
        project_id=project_id,
    )
    assert reviewed.status_code == 200, reviewed.text
    assembled = await client.post(
        f"/api/memoir/projects/{project_id}/book-output/assemble",
    )
    assert assembled.status_code == 200, assembled.text
    output = await test_db.scalar(
        select(MemoirBookOutput).where(
            MemoirBookOutput.project_id == project_id,
        )
    )
    assert output is not None
    output.title = "一个人的故园与远方"
    test_db.add(output)
    await test_db.flush()

    context = await book_reviews.load_book_review_context(
        test_db,
        project_id=project_id,
    )

    assert context.title == "一个人的故园与远方"
    assert context.input_signature != reviewed.json()["input_signature"]
