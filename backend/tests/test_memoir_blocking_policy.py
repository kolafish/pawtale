from hashlib import sha256
import json

from fastapi import HTTPException
import pytest

from app.models.memoir import MemoirCompletionIssue
from app.models.projects import Project
from app.services.memoir import automatic_book_draft, chapter_generation, completion_issues, completion_read_model
from app.services.memoir.narrative import book_completion, book_output_assembly
from tests.test_memoir_book_completion import LayeredBookAI, _book, _install_ai


class StructureReviewAI(LayeredBookAI):
    async def _call_llm(self, system_prompt, user_prompt, **kwargs):
        result = await super()._call_llm(system_prompt, user_prompt, **kwargs)
        if "请通读回忆录章节" in user_prompt:
            payload = json.loads(result)
            for issue in payload["candidate_issues"]:
                issue.update(issue_type="structure", question="是否合并两段比赛经历？")
            return json.dumps(payload, ensure_ascii=False)
        return result


@pytest.mark.ci_smoke
async def test_structure_advice_allows_automatic_writing_confirmation_and_rewrite(client, test_db, monkeypatch):
    project_id, chapters, _ = await _book(client, test_db)
    chapter_id = chapters[0].id
    user_id = (await test_db.get(Project, project_id)).user_id
    _install_ai(monkeypatch, StructureReviewAI(chapter_manual_issue=True))
    workspace = await book_completion.prepare_chapter(
        test_db, project_id=project_id, chapter_id=chapter_id, user_id=user_id, automatic=True,
    )
    assert workspace.completion_summary.state == "draft_ready"
    issues = await completion_issues.list_issues(test_db, project_id=project_id)
    assert len(issues) == 1 and issues[0].issue_type == "structure"
    assert issues[0].status == "open"
    draft, version = await chapter_generation.confirm_chapter_draft(
        test_db, project_id=project_id, chapter_id=chapter_id,
    )
    assert draft.status == "confirmed" and version.content
    assembled = await book_output_assembly.assemble_book_output(test_db, project_id=project_id, automatic=True)
    assert assembled.omitted_chapter_ids == []
    assert assembled.chapter_snapshot_json[0]["chapter_id"] == chapter_id
    rewritten = await book_completion.rewrite_chapter(
        test_db, project_id=project_id, chapter_id=chapter_id, user_id=user_id,
    )
    assert rewritten.completion_summary.state in {"draft_ready", "confirmed"}
    assert issues[0].status == "open"


@pytest.mark.parametrize("kind,scope", [
    ("conflict", "chapter"), ("conflict", "book"),
    ("structure", "chapter"), ("privacy", "chapter"),
])
async def test_save_and_confirm_share_the_scoped_book_blocking_policy(client, test_db, monkeypatch, kind, scope):
    project_id, chapters, _ = await _book(client, test_db, chapter_count=2)
    chapter_id = chapters[0].id
    user_id = (await test_db.get(Project, project_id)).user_id
    _install_ai(monkeypatch, LayeredBookAI())
    await book_completion.prepare_chapter(
        test_db, project_id=project_id, chapter_id=chapter_id, user_id=user_id, automatic=True,
    )
    test_db.add(MemoirCompletionIssue(
        project_id=project_id, chapter_id=chapter_id if scope == "chapter" else None,
        scope=scope, issue_type=kind, channel="manual_review", question="请确认这一处。",
        priority="high", fingerprint=sha256((kind + scope).encode()).hexdigest(), input_signature="probe",
        related_chapter_ids_json=[chapter_id],
    ))
    await test_db.commit()
    blocking = kind == "conflict"
    workspace = await completion_read_model.chapter_workspace(test_db, project_id=project_id, chapter_id=chapter_id)
    overview = await completion_read_model.book_overview(test_db, project_id=project_id)
    assert (workspace.completion_summary.state == "needs_input") == blocking
    assert (overview.chapters[0].state == "needs_input") == blocking
    assert overview.chapters[1].state != "needs_input"
    if blocking:
        with pytest.raises(HTTPException, match="需要确认"):
            await chapter_generation.confirm_chapter_draft(test_db, project_id=project_id, chapter_id=chapter_id)
        with pytest.raises(HTTPException, match="事实冲突"):
            await chapter_generation.generate_and_save_chapter_draft(
                test_db, project_id=project_id, chapter_id=chapter_id, force_refresh=True,
            )
        with pytest.raises(chapter_generation.ChapterDraftCandidateReady):
            await chapter_generation.generate_and_save_chapter_draft(
                test_db, project_id=project_id, chapter_id=chapter_id, automatic=True, force_refresh=True)
    else:
        draft, _ = await chapter_generation.confirm_chapter_draft(test_db, project_id=project_id, chapter_id=chapter_id)
        assert draft.status == "confirmed"
        _, version = await chapter_generation.generate_and_save_chapter_draft(
            test_db, project_id=project_id, chapter_id=chapter_id, automatic=True, force_refresh=True)
        assert version.content


async def test_recover_structure_blocked_first_book_without_existing_output(client, test_db):
    project_id, chapters, _ = await _book(client, test_db)
    chapter_id = chapters[0].id
    test_db.add(MemoirCompletionIssue(
        project_id=project_id, chapter_id=chapter_id, scope="chapter", issue_type="structure",
        channel="manual_review", question="是否合并两段经历？", priority="high",
        fingerprint="a" * 64, input_signature="old-review",
    ))
    parent = await automatic_book_draft.schedule_book_draft(test_db, project_id=project_id)
    parent.status = "succeeded"
    parent.result_json = automatic_book_draft.DraftProgress(chapters={
        chapter_id: automatic_book_draft.ChapterProgress(status="needs_input"),
    }).model_dump(mode="json")
    await test_db.commit()
    assert await automatic_book_draft.reconcile_books_omitted_by_editorial_review(test_db) == 1
    await test_db.refresh(parent)
    assert parent.status == "pending"
    assert await automatic_book_draft.reconcile_books_omitted_by_editorial_review(test_db) == 0
