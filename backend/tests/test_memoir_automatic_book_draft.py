import copy
import json

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ai_base import AITextServiceBase
from app.clock import utcnow_naive
from app.models.memoir import MemoirAutomationEvent, MemoirBookChapterDraftVersion, MemoirCompletionIssue, MemoirEditingJob, MemoirSection
from app.services.memoir import automatic_book_draft, chapter_generation
from app.services.memoir.editing_job_handlers import register_memoir_editing_job_handlers
from app.services.memoir.editing_jobs import clear_editing_job_handlers
from tests.memoir_helpers import run_test_editing_job
from tests.test_memoir_chapter_pipeline import _chapter, FakeChapterAI


async def _setup(client, test_db, monkeypatch):
    project_id, chapter, section, materials = await _chapter(client, test_db)
    fake = FakeChapterAI(
        section_material_id=materials[0]["utterance_id"],
        chapter_section_id=section["id"],
        chapter_material_id=materials[0]["utterance_id"],
        draft_material_ids=[materials[0]["utterance_id"]],
    )
    for module in ("chapter_generation", "chapter_digests", "section_digests", "book_reviews"):
        monkeypatch.setattr(f"app.services.memoir.{module}.get_ai_service", lambda *_: fake)
    call = fake._call_llm
    async def with_book_review(system, prompt, **kwargs):
        if "请通读下面这本回忆录" in prompt:
            fake.prompts.append(prompt)
            return json.dumps({"summary": "一段旧友重逢的故事。", "strengths": [], "structure_guidance": [], "candidate_issues": [], "editorial_actions": []}, ensure_ascii=False)
        return await call(system, prompt, **kwargs)
    fake._call_llm = with_book_review
    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    parent = await automatic_book_draft.schedule_book_draft(test_db, project_id=project_id)
    await test_db.commit()
    return project_id, chapter.id, section["id"], fake, parent.id


async def test_backend_alone_creates_chapter_and_reuses_same_input(client, test_db, monkeypatch):
    project_id, chapter_id, _, fake, parent_id = await _setup(client, test_db, monkeypatch)
    assert not await run_test_editing_job(test_db, parent_id)
    parent = await test_db.get(MemoirEditingJob, parent_id)
    child_id = parent.result_json["chapters"][chapter_id]["job_id"]
    assert await run_test_editing_job(test_db, child_id)
    assert not await run_test_editing_job(test_db, parent_id)
    assert parent.result_json["output_version_id"]
    assert await run_test_editing_job(test_db, parent.result_json["review_job_id"])
    assert await run_test_editing_job(test_db, parent_id)
    latest = await chapter_generation.latest_chapter_draft_version(
        test_db, project_id=project_id, chapter_id=chapter_id
    )
    assert latest and "朋友" in latest.content
    events = list((await test_db.scalars(select(MemoirAutomationEvent).where(
        MemoirAutomationEvent.project_id == project_id,
    ))).all())
    assert {event.event_type for event in events} == {"chapter_draft_generated", "book_output_generated"}
    assert all(event.status == "completed" for event in events)
    chapter_event = next(event for event in events if event.event_type == "chapter_draft_generated")
    assert chapter_event.chapter_id == chapter_id
    assert chapter_event.metadata_json["draft_version_id"] == latest.id
    event_ids = {event.id for event in events}
    chapter_event.metadata_json = {**chapter_event.metadata_json, "acknowledged_at": "2026-09-05T08:50:49Z"}
    calls = len(fake.prompts)
    await automatic_book_draft.schedule_book_draft(test_db, project_id=project_id)
    await test_db.commit()
    assert await run_test_editing_job(test_db, parent_id)
    assert len(fake.prompts) == calls
    assert set((await test_db.scalars(select(MemoirAutomationEvent.id).where(
        MemoirAutomationEvent.project_id == project_id,
    ))).all()) == event_ids
    assert parent.result_json["stage"] == "ready"
    output = await client.get(f"/api/mobile/memoir/projects/{project_id}/book-output")
    assert output.status_code == 200
    assert output.json()["is_stale"] is False


async def test_failed_chapter_is_not_automatically_reissued(client, test_db, monkeypatch):
    project_id, chapter_id, _, _, parent_id = await _setup(client, test_db, monkeypatch)
    assert not await run_test_editing_job(test_db, parent_id)
    parent = await test_db.get(MemoirEditingJob, parent_id)
    child_id = parent.result_json["chapters"][chapter_id]["job_id"]
    child = await test_db.get(MemoirEditingJob, child_id)
    child.status, child.error_message = "failed", "模型超时，结果未知"
    await test_db.commit()
    assert await run_test_editing_job(test_db, parent_id)
    assert parent.result_json["chapters"][chapter_id]["status"] == "failed"
    await automatic_book_draft.schedule_book_draft(test_db, project_id=project_id, source="retry")
    await test_db.commit()
    assert not await run_test_editing_job(test_db, parent_id)
    new_id = parent.result_json["chapters"][chapter_id]["job_id"]
    assert new_id != child_id
    assert not await run_test_editing_job(test_db, parent_id)
    assert parent.result_json["chapters"][chapter_id]["job_id"] == new_id


@pytest.mark.parametrize("edited_during_generation", [False, True])
async def test_automatic_generation_keeps_manual_text_as_candidate(
    client, test_db, monkeypatch, edited_during_generation
):
    project_id, chapter_id, section_id, fake, _ = await _setup(client, test_db, monkeypatch)
    _, initial = await chapter_generation.generate_and_save_chapter_draft(
        test_db, project_id=project_id, chapter_id=chapter_id, editorial_guidance=[], ai_client=fake
    )
    await test_db.commit()
    values = {
        key: copy.deepcopy(getattr(initial, key))
        for key in (
            "draft_id",
            "project_id",
            "chapter_id",
            "input_signature",
            "used_material_ids_json",
            "material_snapshot_json",
            "section_snapshot_json",
        )
    }

    async def manual_edit():
        async with AsyncSession(test_db.bind, expire_on_commit=False) as other:
            version = MemoirBookChapterDraftVersion(
                **values, version=2, content="这是我亲手改的正文。", created_by="user"
            )
            other.add(version)
            await other.commit()
            return version.id

    if not edited_during_generation:
        manual_id = await manual_edit()
    section = await test_db.get(MemoirSection, section_id)
    section.version += 1
    await test_db.commit()
    original = fake._call_llm

    async def generate(*args, **kwargs):
        nonlocal manual_id
        if edited_during_generation:
            manual_id = await manual_edit()
        return await original(*args, **kwargs)

    monkeypatch.setattr(fake, "_call_llm", generate)
    with pytest.raises(chapter_generation.ChapterDraftCandidateReady) as caught:
        await chapter_generation.generate_and_save_chapter_draft(
            test_db, project_id=project_id, chapter_id=chapter_id, editorial_guidance=[], ai_client=fake, automatic=True
        )
    latest = await chapter_generation.latest_chapter_draft_version(
        test_db, project_id=project_id, chapter_id=chapter_id
    )
    assert latest.id == manual_id and latest.content == "这是我亲手改的正文。"
    job = MemoirEditingJob(
        project_id=project_id,
        chapter_id=chapter_id,
        job_type="prepare_chapter",
        dedupe_key=f"prepare-chapter:{chapter_id}",
        status="succeeded",
        run_after=utcnow_naive(),
        result_json={**caught.value.candidate, "outcome": "needs_review"},
    )
    test_db.add(job)
    await test_db.commit()
    current = await client.get(
        f"/api/memoir/projects/{project_id}/book-completion/chapters/{chapter_id}/jobs/current"
    )
    assert current.status_code == 200, current.text
    candidate_section = current.json()["candidate_sections"][0]
    assert candidate_section["section_id"] == section_id
    assert (
        candidate_section["title"]
        == caught.value.candidate["checkpoint_sections"][0]["title"]
    )
    assert (
        candidate_section["content"]
        == caught.value.candidate["checkpoint_sections"][0]["content"]
    )
    assert "checkpoint_sections" not in current.json()
    calls = len(fake.prompts)
    accepted = await client.post(
        f"/api/memoir/projects/{project_id}/book-completion/chapters/{chapter_id}/jobs/{job.id}/candidate/accept"
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["candidate_sections"] == []
    assert len(fake.prompts) == calls
    assert accepted.json()["outcome"] == "ready"
    latest = await chapter_generation.latest_chapter_draft_version(
        test_db, project_id=project_id, chapter_id=chapter_id
    )
    assert latest.version == 3


async def test_candidate_rejects_changed_source_and_interrupted_call_requires_retry(client, test_db, monkeypatch):
    project_id, chapter_id, section_id, _, parent_id = await _setup(client, test_db, monkeypatch)
    assert not await run_test_editing_job(test_db, parent_id)
    parent = await test_db.get(MemoirEditingJob, parent_id)
    child = await test_db.get(MemoirEditingJob, parent.result_json["chapters"][chapter_id]["job_id"])
    child.result_json = {**child.result_json, "provider_request_pending": True}
    await test_db.commit()
    assert not await run_test_editing_job(test_db, child.id)
    assert "模型请求尚未确认完成" in child.error_message
    section = await test_db.get(MemoirSection, section_id)
    section.version += 1
    await test_db.commit()
    with pytest.raises(HTTPException) as error:
        await chapter_generation.save_chapter_draft_candidate(
            test_db,
            project_id=project_id,
            chapter_id=chapter_id,
            checkpoint={"candidate_base_version_id": None, "checkpoint_input_signature": "old"},
        )
    assert error.value.status_code == 409
    assert (
        await test_db.scalar(
            select(MemoirBookChapterDraftVersion.id).where(MemoirBookChapterDraftVersion.chapter_id == chapter_id)
        )
        is None
    )


async def test_new_privacy_decision_during_generation_prevents_publication(client, test_db, monkeypatch):
    project_id, chapter_id, _, fake, _ = await _setup(client, test_db, monkeypatch)
    generate = fake._call_llm

    async def add_privacy_decision(*args, **kwargs):
        async with AsyncSession(test_db.bind, expire_on_commit=False) as other:
            other.add(MemoirCompletionIssue(project_id=project_id, chapter_id=chapter_id, scope="chapter",
                issue_type="privacy", channel="manual_review", question="是否保留这段私人经历？",
                priority="high", fingerprint="a" * 64, input_signature="review"))
            await other.commit()
        return await generate(*args, **kwargs)

    monkeypatch.setattr(fake, "_call_llm", add_privacy_decision)
    with pytest.raises(chapter_generation.ChapterDraftCandidateReady):
        await chapter_generation.generate_and_save_chapter_draft(test_db, project_id=project_id,
            chapter_id=chapter_id, editorial_guidance=[], ai_client=fake, automatic=True)
    assert await chapter_generation.latest_chapter_draft_version(test_db, project_id=project_id,
        chapter_id=chapter_id) is None


async def test_review_failure_leaves_readable_book_and_does_not_repeat_model(client, test_db, monkeypatch):
    from app.models.memoir import MemoirBookOutputVersion, MemoirBookChapterDraft
    project_id, chapter_id, _, _, parent_id = await _setup(client, test_db, monkeypatch)
    assert not await run_test_editing_job(test_db, parent_id)
    parent = await test_db.get(MemoirEditingJob, parent_id)
    assert await run_test_editing_job(test_db, parent.result_json["chapters"][chapter_id]["job_id"])
    assert not await run_test_editing_job(test_db, parent_id)
    version_id = parent.result_json["output_version_id"]
    review_id = parent.result_json["review_job_id"]
    calls = 0
    class FailedReview(AITextServiceBase):
        provider_name = "test"
        model_name = "failed-review"
        async def _call_llm(self, *args, **kwargs):
            nonlocal calls
            calls += 1
            raise RuntimeError("provider unavailable")
    monkeypatch.setattr("app.services.memoir.book_reviews.get_ai_service", lambda *_: FailedReview())
    assert not await run_test_editing_job(test_db, review_id)
    assert await run_test_editing_job(test_db, parent_id)
    assert parent.result_json["stage"] == "ready_review_failed"
    assert (await test_db.get(MemoirBookOutputVersion, version_id)).content
    draft = await test_db.scalar(select(MemoirBookChapterDraft).where(MemoirBookChapterDraft.chapter_id == chapter_id))
    assert draft.status == "draft"
    previous_calls = calls
    await automatic_book_draft.schedule_book_draft(test_db, project_id=project_id)
    await test_db.commit()
    assert await run_test_editing_job(test_db, parent_id)
    assert calls == previous_calls
    assert parent.result_json["output_version_id"] == version_id


async def test_auto_assembly_preserves_manual_book_and_omits_pending_decisions(client, test_db):
    from app.domain.memoir.types import MemoirBookChapterDraftStatus
    from app.models.memoir import MemoirBookOutputVersion
    from app.services.memoir.narrative.book_output_assembly import assemble_book_output
    from tests.test_memoir_book_completion import _book, _create_current_draft
    project_id, chapters, _ = await _book(client, test_db, chapter_count=2)
    for chapter in chapters:
        await _create_current_draft(test_db, project_id=project_id, chapter_id=chapter.id,
                                    status=MemoirBookChapterDraftStatus.DRAFT)
    test_db.add(MemoirCompletionIssue(project_id=project_id, chapter_id=chapters[0].id, scope="chapter",
        issue_type="privacy", channel="manual_review", question="是否保留这段私人经历？",
        priority="high", fingerprint="b" * 64, input_signature="review"))
    await test_db.commit()
    partial = await assemble_book_output(test_db, project_id=project_id, automatic=True)
    assert partial.omitted_chapter_ids == [chapters[0].id]
    assert [item["chapter_id"] for item in partial.chapter_snapshot_json] == [chapters[1].id]
    version = await test_db.get(MemoirBookOutputVersion, partial.output_version_id)
    version.content, version.created_by = "用户亲自整理的全书。", "user"
    await test_db.commit()
    preserved = await assemble_book_output(test_db, project_id=project_id, automatic=True)
    assert preserved.preserved_manual
    assert preserved.output_version_id == partial.output_version_id
    assert preserved.content == "用户亲自整理的全书。"


async def test_stale_book_review_decisions_still_omit_their_chapters(client, test_db):
    from app.domain.memoir.types import MemoirBookChapterDraftStatus
    from app.models.memoir import MemoirBookReview
    from app.services.memoir import completion_issues
    from app.services.memoir.narrative.book_output_assembly import assemble_book_output
    from tests.test_memoir_book_completion import _book, _create_current_draft
    project_id, chapters, _ = await _book(client, test_db, chapter_count=2)
    for chapter in chapters:
        await _create_current_draft(test_db, project_id=project_id, chapter_id=chapter.id,
            status=MemoirBookChapterDraftStatus.DRAFT)
    candidates = [{"issue_type": "privacy", "channel": "manual_review", "priority": "high",
        "question": "是否保留私人经历？", "reason": "包含个人隐私", "chapter_ids": [chapters[0].id]}]
    test_db.add(MemoirBookReview(project_id=project_id, engine="old", input_signature="old-input",
        status="stale", review_json={"candidate_issues": candidates}))
    await completion_issues.sync_book_review_issues(test_db, project_id=project_id,
        input_signature="old-input", candidates=candidates)
    await test_db.commit()
    result = await assemble_book_output(test_db, project_id=project_id, automatic=True)
    assert result.omitted_chapter_ids == [chapters[0].id]
    assert [item["chapter_id"] for item in result.chapter_snapshot_json] == [chapters[1].id]
