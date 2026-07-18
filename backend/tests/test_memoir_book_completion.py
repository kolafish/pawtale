from __future__ import annotations

import json

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.database import Base, register_models
from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirCompletionIssueOutcome,
    MemoirCompletionIssueScope,
    MemoirEditingJobType,
)
from app.migrations.memoir_books import (
    assert_memoir_book_schema,
    migrate_memoir_book_schema,
)
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirBookReview,
    MemoirCompletionIssue,
    MemoirEditingJob,
)
from app.schemas.memoir_narrative import (
    MemoirChapterCompletionNextAction,
    MemoirChapterCompletionSummary,
)
from app.services.memoir import (
    book_reviews,
    chapter_digests,
    chapter_generation,
    completion_read_model,
    section_digests,
)
from tests.memoir_helpers import (
    add_test_section_materials,
    create_test_book_plan_with_chapters,
    create_test_chapter_draft,
    create_test_voice_transcript,
    ensure_mobile_memoir_project,
    memoir_source_ref,
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
                        "material_ids": material_ids[:1],
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
                        "material_ids": [],
                    }
                ]
            payload = {
                "summary": "全书记录了几段重要成长经历。",
                "strengths": ["人生阶段清楚"],
                "structure_guidance": ["统一章节之间的过渡。"],
                "candidate_issues": candidates,
            }
        else:
            payload = {
                "content": "那年，我参加了学校比赛。"
                "这段经历让我一直记得，也让我第一次认真看待输赢。",
                "used_material_ids": material_ids,
                "missing_source_candidates": [],
                "editor_notes": [],
            }
        return json.dumps(payload, ensure_ascii=False)


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


def _install_ai(monkeypatch, fake: LayeredBookAI) -> None:
    factory = lambda _model=None: fake
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


async def test_rewrite_uses_current_material_without_forcing_more_interviews(
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
    before = prepared.json()
    assert before["completion_summary"]["state"] == "needs_input"
    assert before["draft_needs_rewrite"] is True
    assert before["can_rewrite_draft"] is True
    assert before["pending_new_source_count"] == 1

    rewritten = await client.post(
        f"/api/memoir/projects/{project_id}/book-completion/"
        f"chapters/{chapter_id}/rewrite",
        json={},
    )

    assert rewritten.status_code == 200, rewritten.text
    after = rewritten.json()
    assert after["completion_summary"]["state"] == "draft_ready"
    assert after["draft_needs_rewrite"] is False
    assert after["draft_content"].startswith("那年，我参加了学校比赛")
    issue = await test_db.scalar(
        select(MemoirCompletionIssue).where(
            MemoirCompletionIssue.project_id == project_id,
            MemoirCompletionIssue.chapter_id == chapter_id,
        )
    )
    assert issue is not None
    assert issue.outcome == MemoirCompletionIssueOutcome.DEFERRED.value
    assert issue.outcome_note == "用户选择先按现有素材重新生成正文"


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
            "content": "那年，我参加了学校比赛。这件事至今仍留在记忆里。",
        },
    )
    assert revised.status_code == 200, revised.text
    revised_version = await test_db.get(
        MemoirBookChapterDraftVersion,
        revised.json()["latest_version"]["id"],
    )
    assert revised_version is not None
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


async def test_closed_section_issue_is_not_reopened_on_prepare(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, _ = await _book(client, test_db)
    chapter = chapters[0]
    fake = LayeredBookAI(section_issue=True)
    _install_ai(monkeypatch, fake)

    first = await client.post(
        f"/api/memoir/projects/{project_id}/book-completion/"
        f"chapters/{chapter.id}/prepare",
        json={},
    )
    assert first.status_code == 200
    summary = first.json()["completion_summary"]
    assert summary["state"] == "needs_input"
    assert summary["next_action"]["kind"] == "start_interview"
    issue_id = summary["active_issue"]["id"]

    closed = await client.post(
        f"/api/memoir/projects/{project_id}/completion-issues/{issue_id}/close",
        json={"outcome": "not_remembered", "note": "确实想不起来"},
    )
    assert closed.status_code == 200
    second = await client.post(
        f"/api/memoir/projects/{project_id}/book-completion/"
        f"chapters/{chapter.id}/prepare",
        json={},
    )
    assert second.status_code == 200
    assert second.json()["completion_summary"]["state"] == "draft_ready"
    issue = await test_db.get(MemoirCompletionIssue, issue_id)
    assert issue is not None
    assert issue.status == "closed"
    assert issue.outcome == "not_remembered"


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
    project_id, _, _ = await _book(client, test_db, chapter_count=2)
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
