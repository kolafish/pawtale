import copy
import json

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ai_base import AITextServiceBase
from app.clock import utcnow_naive
from app.models.memoir import MemoirAutomationEvent, MemoirBookChapterDraftVersion, MemoirBookOutputVersion, MemoirCompletionIssue, MemoirEditingJob, MemoirSection
from app.schemas.memoir_narrative import MemoirChapterPreparationJobRequest
from app.services.memoir import automatic_book_draft, chapter_generation, chapter_preparation_jobs
from app.services.memoir.editing_job_handlers import register_memoir_editing_job_handlers
from app.services.memoir.editing_jobs import clear_editing_job_handlers
from tests.memoir_helpers import run_test_editing_job
from tests.test_memoir_chapter_pipeline import _chapter, FakeChapterAI


def test_candidate_diff_segments_reconstruct_both_versions():
    before = "十八岁那年，我第一次离开家。月台很安静。"
    after = "十八岁那年，我第一次坐上长途火车。月台很明亮。这是新的开始。"

    segments = chapter_preparation_jobs._candidate_diff_segments(before, after)

    assert "".join(
        segment["text"]
        for segment in segments
        if segment["kind"] in {"equal", "deleted"}
    ) == before
    assert (
        "".join(
            segment["text"]
            for segment in segments
            if segment["kind"] in {"equal", "modified", "added"}
        )
        == after
    )
    assert {segment["kind"] for segment in segments} >= {
        "equal",
        "deleted",
        "modified",
    }
    assert chapter_preparation_jobs._candidate_diff_segments("相同", "相同新增")[-1] == {
        "kind": "added",
        "text": "新增",
    }


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
    assert {event.event_type for event in events} == {"chapter_draft_generated", "book_output_generated", "book_editorial_copy_generated"}
    assert all(event.status == "completed" for event in events if event.event_type != "book_editorial_copy_generated")
    chapter_event = next(event for event in events if event.event_type == "chapter_draft_generated")
    assert chapter_event.chapter_id == chapter_id
    assert chapter_event.metadata_json["draft_version_id"] == latest.id
    assert chapter_event.metadata_json["book_char_count_delta"] == len(latest.content.strip())
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


async def test_five_chapter_review_does_not_cover_six_chapters_when_an_update_fails(client, test_db, monkeypatch):
    from app.domain.memoir.types import MemoirBookChapterDraftStatus
    from app.models.memoir import MemoirBookPlanChapter
    from tests.test_memoir_book_completion import _book, _create_current_draft, LayeredBookAI

    project_id, chapters, _ = await _book(client, test_db, chapter_count=6)
    first_chapter_id, last_chapter_id = chapters[0].id, chapters[-1].id
    chapters[-1].include_in_book = False
    for chapter in chapters[:-1]:
        _, version = await _create_current_draft(test_db, project_id=project_id, chapter_id=chapter.id,
            status=MemoirBookChapterDraftStatus.DRAFT)
        version.created_by = "ai"
    fake = LayeredBookAI()
    for module in ("chapter_generation", "chapter_digests", "section_digests", "book_reviews"):
        monkeypatch.setattr(f"app.services.memoir.{module}.get_ai_service", lambda *_: fake)
    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    parent = await automatic_book_draft.schedule_book_draft(test_db, project_id=project_id)
    parent_id = parent.id
    await test_db.commit()
    assert not await run_test_editing_job(test_db, parent_id)
    old_review_id = parent.result_json["review_job_id"]
    assert await run_test_editing_job(test_db, old_review_id)
    await test_db.refresh(parent)
    old_review = await test_db.get(MemoirEditingJob, old_review_id)
    assert old_review.status == "succeeded", old_review.error_message
    old_review_signature = old_review.payload_json["input_signature"]
    assert await run_test_editing_job(test_db, parent_id)
    assert parent.result_json["review_status"] == "succeeded"

    last_chapter = await test_db.get(MemoirBookPlanChapter, last_chapter_id)
    last_chapter.include_in_book = True
    await _create_current_draft(test_db, project_id=project_id, chapter_id=last_chapter_id,
        status=MemoirBookChapterDraftStatus.DRAFT)
    section = await test_db.scalar(select(MemoirSection).where(MemoirSection.chapter_id == first_chapter_id))
    section.title = "重新整理的第一次比赛"
    section.version += 1
    await automatic_book_draft.schedule_book_draft(test_db, project_id=project_id)
    await test_db.commit()
    assert not await run_test_editing_job(test_db, parent_id)
    child = await test_db.get(MemoirEditingJob, parent.result_json["chapters"][first_chapter_id]["job_id"])
    child.status, child.error_message = "failed", "章节更新失败"
    await test_db.commit()
    assert await run_test_editing_job(test_db, parent_id)
    assert parent.result_json["stage"] == "partial_ready"
    assert parent.result_json["review_status"] == "not_started"
    assert parent.result_json["review_job_id"] is None
    output = await test_db.get(MemoirBookOutputVersion, parent.result_json["output_version_id"])
    assert len(output.chapter_snapshot_json) == 6
    old_review = await test_db.get(MemoirEditingJob, old_review_id)
    assert old_review.status == "succeeded"
    assert old_review.result_json["total_chapter_count"] == 5

    # An old persisted progress record is projected safely without rerunning jobs or writing it back.
    parent.result_json = {**parent.result_json, "review_job_id": old_review_id, "review_status": "succeeded"}
    await test_db.commit()
    before = copy.deepcopy(parent.result_json)
    calls = len(fake.prompts)
    response = await client.get(f"/api/mobile/memoir/projects/{project_id}/draft-progress")
    assert response.status_code == 200
    assert response.json()["review_status"] == "not_started"
    assert response.json()["readable_chapter_count"] == 6
    await test_db.refresh(parent)
    assert parent.result_json == before
    assert len(fake.prompts) == calls

    # After chapter recovery the normal scheduler reviews the new six-chapter input exactly once.
    await chapter_generation.generate_and_save_chapter_draft(test_db, project_id=project_id,
        chapter_id=first_chapter_id, editorial_guidance=[], ai_client=fake, automatic=True)
    await automatic_book_draft.schedule_book_draft(test_db, project_id=project_id)
    await test_db.commit()
    assert not await run_test_editing_job(test_db, parent_id)
    review_id = parent.result_json["review_job_id"]
    review = await test_db.get(MemoirEditingJob, review_id)
    assert review.status == "pending"
    assert review.payload_json["input_signature"] != old_review_signature
    assert review.result_json["total_chapter_count"] == 6
    assert await run_test_editing_job(test_db, review_id)
    assert await run_test_editing_job(test_db, parent_id)
    assert parent.result_json["review_status"] == "succeeded"
    assert (await test_db.get(MemoirEditingJob, review_id)).result_json["total_chapter_count"] == 6
    calls = len(fake.prompts)
    await automatic_book_draft.schedule_book_draft(test_db, project_id=project_id)
    await test_db.commit()
    assert await run_test_editing_job(test_db, parent_id)
    assert parent.result_json["review_job_id"] == review_id
    assert len(fake.prompts) == calls


async def test_policy_assembly_does_not_reuse_review_status_for_changed_book_title(client, test_db, monkeypatch):
    from app.models.projects import Project

    project_id, chapter_id, _, fake, parent_id = await _setup(client, test_db, monkeypatch)
    assert not await run_test_editing_job(test_db, parent_id)
    parent = await test_db.get(MemoirEditingJob, parent_id)
    assert await run_test_editing_job(test_db, parent.result_json["chapters"][chapter_id]["job_id"])
    assert not await run_test_editing_job(test_db, parent_id)
    assert await run_test_editing_job(test_db, parent.result_json["review_job_id"])
    assert await run_test_editing_job(test_db, parent_id)
    calls = len(fake.prompts)
    await automatic_book_draft.schedule_book_draft(test_db, project_id=project_id, source="assembly_policy")
    await test_db.commit()
    assert await run_test_editing_job(test_db, parent_id)
    assert parent.result_json["review_status"] == "succeeded"
    project = await test_db.get(Project, project_id)
    project.title = "改过名字的回忆录"
    await automatic_book_draft.schedule_book_draft(test_db, project_id=project_id, source="assembly_policy")
    await test_db.commit()
    assert await run_test_editing_job(test_db, parent_id)
    assert parent.result_json["review_status"] == "not_started"
    assert parent.result_json["review_job_id"] is None
    assert len(fake.prompts) == calls


@pytest.mark.parametrize("growth_failed", [False, True])
async def test_user_retry_survives_growth_and_coalesced_updates(client, test_db, monkeypatch, growth_failed):
    from app.models.memoir import MemoirSectionMaterial, MemoirVoiceUtterance
    from app.services.memoir import draft_progress

    project_id, chapter_id, section_id, fake, parent_id = await _setup(client, test_db, monkeypatch)
    assert not await run_test_editing_job(test_db, parent_id)
    parent = await test_db.get(MemoirEditingJob, parent_id)
    child = await test_db.get(MemoirEditingJob, parent.result_json["chapters"][chapter_id]["job_id"])
    child.status, child.error_message = "failed", "模型超时"
    await test_db.commit()
    assert await run_test_editing_job(test_db, parent_id)
    session_id = await test_db.scalar(select(MemoirVoiceUtterance.voice_session_id).join(
        MemoirSectionMaterial, MemoirSectionMaterial.utterance_id == MemoirVoiceUtterance.id,
    ).where(MemoirSectionMaterial.section_id == section_id).limit(1))
    growth = MemoirEditingJob(project_id=project_id, job_type="organize_material",
        dedupe_key=f"organize_session:{session_id}", run_after=utcnow_naive(),
        status="failed" if growth_failed else "succeeded", error_message="归类中断" if growth_failed else "",
        payload_json={"voice_session_id": session_id, "source": "transcript_ready"})
    test_db.add(growth)
    await test_db.commit()
    calls = len(fake.prompts)
    response = await client.post(f"/api/mobile/memoir/projects/{project_id}/draft-progress/retry")
    assert response.status_code == 202
    await test_db.refresh(growth)
    assert growth.status == ("pending" if growth_failed else "succeeded")
    if growth_failed:
        assert not await run_test_editing_job(test_db, parent_id)
        assert await run_test_editing_job(test_db, growth.id)
    assert len(fake.prompts) == calls  # Assigned material is never reclassified.
    await automatic_book_draft.schedule_book_draft(test_db, project_id=project_id, source="organized", settled=True)
    await test_db.commit()
    assert not await run_test_editing_job(test_db, parent_id)
    await test_db.refresh(parent)
    replacement = parent.result_json["chapters"][chapter_id]["job_id"]
    assert replacement != child.id
    await draft_progress.retry_draft_progress(test_db, project_id=project_id)  # Double tap while active is inert.
    assert not await run_test_editing_job(test_db, parent_id)
    assert parent.result_json["chapters"][chapter_id]["job_id"] == replacement


async def test_stale_generation_requeues_without_reusing_its_checkpoint(client, test_db, monkeypatch):
    project_id, chapter_id, _, _, parent_id = await _setup(client, test_db, monkeypatch)
    assert not await run_test_editing_job(test_db, parent_id)
    parent = await test_db.get(MemoirEditingJob, parent_id)
    child_id = parent.result_json["chapters"][chapter_id]["job_id"]

    async def stale(*args, **kwargs):
        raise chapter_generation.ChapterGenerationStale("base changed")

    monkeypatch.setattr(chapter_preparation_jobs.book_completion, "prepare_chapter", stale)
    assert not await run_test_editing_job(test_db, child_id)
    child = await test_db.get(MemoirEditingJob, child_id)
    assert child.status == "skipped"
    assert child.result_json == {"stage": "cancelled", "outcome": "stale"}

    assert not await run_test_editing_job(test_db, parent_id)
    replacement_id = parent.result_json["chapters"][chapter_id]["job_id"]
    assert replacement_id != child_id
    replacement = await test_db.get(MemoirEditingJob, replacement_id)
    assert replacement.status == "pending"
    assert "checkpoint_sections" not in replacement.result_json


async def test_polish_setting_change_schedules_one_refresh_for_existing_draft(client, test_db, monkeypatch):
    project_id, chapter_id, _, fake, _ = await _setup(client, test_db, monkeypatch)
    await chapter_generation.generate_and_save_chapter_draft(
        test_db, project_id=project_id, chapter_id=chapter_id,
        editorial_guidance=[], ai_client=fake,
    )
    await test_db.commit()
    scheduled: list[str] = []

    async def record_schedule(db, *, project_id, source):
        scheduled.append(source)

    monkeypatch.setattr(automatic_book_draft, "schedule_book_draft", record_schedule)
    for _ in range(2):
        response = await client.patch(
            f"/api/mobile/memoir/projects/{project_id}",
            json={"transcript_polish_level": "medium"},
        )
        assert response.status_code == 200, response.text
    assert scheduled == ["polish_level"]


async def test_old_confirmation_candidate_is_requeued_under_automatic_policy(client, test_db, monkeypatch):
    project_id, chapter_id, _, fake, _ = await _setup(client, test_db, monkeypatch)
    _, version = await chapter_generation.generate_and_save_chapter_draft(
        test_db, project_id=project_id, chapter_id=chapter_id,
        editorial_guidance=[], ai_client=fake,
    )
    context = await chapter_generation.load_chapter_generation_context(
        test_db, project_id=project_id, chapter_id=chapter_id,
    )
    old = MemoirEditingJob(
        project_id=project_id, chapter_id=chapter_id,
        job_type="prepare_chapter", dedupe_key=f"prepare-chapter:{chapter_id}",
        status="succeeded", run_after=utcnow_naive(),
        payload_json={"input_signature": context.input_signature},
        result_json={"outcome": "needs_review", "candidate_content": "旧的整章候选稿", "candidate_base_version_id": version.id},
    )
    test_db.add(old)
    await test_db.commit()
    user_id = (await client.get("/api/auth/me")).json()["id"]

    state = await chapter_preparation_jobs.enqueue_chapter_preparation_job(
        test_db, user_id=user_id, project_id=project_id, chapter_id=chapter_id,
        req=MemoirChapterPreparationJobRequest(operation="prepare"), automatic=True,
    )
    assert state["job_id"] != old.id
    assert state["status"] == "pending"


@pytest.mark.db_independent
@pytest.mark.parametrize("edited_during_generation", [False, True])
async def test_automatic_generation_preserves_manual_text_or_discards_stale_output(
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
    original = fake._call_llm

    async def generate(*args, **kwargs):
        nonlocal manual_id
        if edited_during_generation:
            manual_id = await manual_edit()
        return await original(*args, **kwargs)

    monkeypatch.setattr(fake, "_call_llm", generate)
    expected = (
        chapter_generation.ChapterGenerationStale
        if edited_during_generation
        else chapter_generation.ChapterDraftCandidateReady
    )
    with pytest.raises(expected) as caught:
        await chapter_generation.generate_and_save_chapter_draft(
            test_db, project_id=project_id, chapter_id=chapter_id, editorial_guidance=[],
            ai_client=fake, automatic=True, force_refresh=True,
        )
    latest = await chapter_generation.latest_chapter_draft_version(
        test_db, project_id=project_id, chapter_id=chapter_id
    )
    assert latest.id == manual_id and latest.content == "这是我亲手改的正文。"
    if edited_during_generation:
        return
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
    candidate_diff = current.json()["candidate_diff_segments"]
    assert (
        "".join(
            segment["text"]
            for segment in candidate_diff
            if segment["kind"] in {"equal", "deleted"}
        )
        == "这是我亲手改的正文。"
    )
    assert (
        "".join(
            segment["text"]
            for segment in candidate_diff
            if segment["kind"] in {"equal", "modified", "added"}
        )
        == current.json()["candidate_content"]
    )
    assert {segment["kind"] for segment in candidate_diff} & {"modified", "added"}
    assert any(segment["kind"] == "deleted" for segment in candidate_diff)
    assert "checkpoint_sections" not in current.json()
    calls = len(fake.prompts)
    accepted = await client.post(
        f"/api/memoir/projects/{project_id}/book-completion/chapters/{chapter_id}/jobs/{job.id}/candidate/accept"
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["candidate_sections"] == []
    assert accepted.json()["candidate_diff_segments"] == []
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


@pytest.mark.db_independent
@pytest.mark.parametrize("issue_type", ["privacy", "conflict"])
async def test_only_fact_conflict_during_generation_requires_confirmation(client, test_db, monkeypatch, issue_type):
    project_id, chapter_id, _, fake, _ = await _setup(client, test_db, monkeypatch)
    generate = fake._call_llm

    async def add_decision(*args, **kwargs):
        async with AsyncSession(test_db.bind, expire_on_commit=False) as other:
            other.add(MemoirCompletionIssue(project_id=project_id, chapter_id=chapter_id, scope="chapter",
                issue_type=issue_type, channel="manual_review", question="这段经历是否需要调整？",
                priority="high", fingerprint="a" * 64, input_signature="review"))
            await other.commit()
        return await generate(*args, **kwargs)

    monkeypatch.setattr(fake, "_call_llm", add_decision)
    if issue_type == "privacy":
        _, version = await chapter_generation.generate_and_save_chapter_draft(
            test_db, project_id=project_id, chapter_id=chapter_id,
            editorial_guidance=[], ai_client=fake, automatic=True,
        )
        draft, confirmed_version = await chapter_generation.confirm_chapter_draft(
            test_db, project_id=project_id, chapter_id=chapter_id,
        )
        assert draft.status == "confirmed"
        assert confirmed_version.id == version.id
        return
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
    project_id, chapters, _ = await _book(client, test_db, chapter_count=3)
    for chapter in chapters:
        await _create_current_draft(test_db, project_id=project_id, chapter_id=chapter.id,
                                    status=MemoirBookChapterDraftStatus.DRAFT)
    test_db.add(MemoirCompletionIssue(project_id=project_id, chapter_id=chapters[0].id, scope="chapter",
        issue_type="conflict", channel="manual_review", question="这段经历的时间冲突如何处理？",
        priority="high", fingerprint="b" * 64, input_signature="review"))
    await test_db.commit()
    partial = await assemble_book_output(test_db, project_id=project_id, automatic=True)
    assert partial.omitted_chapter_ids == [chapters[0].id]
    assert [item["chapter_id"] for item in partial.chapter_snapshot_json] == [
        chapters[1].id, chapters[2].id,
    ]
    manual = await assemble_book_output(test_db, project_id=project_id)
    assert manual.output_version_id == partial.output_version_id
    assert manual.omitted_chapter_ids == [chapters[0].id]
    version = await test_db.get(MemoirBookOutputVersion, partial.output_version_id)
    version.content, version.created_by = "用户亲自整理的全书。", "user"
    await test_db.commit()
    preserved = await assemble_book_output(test_db, project_id=project_id, automatic=True)
    assert preserved.preserved_manual
    assert preserved.output_version_id == partial.output_version_id
    assert preserved.content == "用户亲自整理的全书。"
    test_db.add(MemoirCompletionIssue(
        project_id=project_id, chapter_id=chapters[1].id, scope="chapter",
        issue_type="conflict", channel="manual_review", question="新讲述的时间冲突如何处理？",
        priority="high", fingerprint="c" * 64, input_signature="review-2",
    ))
    await test_db.commit()
    updated = await assemble_book_output(test_db, project_id=project_id, automatic=True)
    assert updated.output_version_id != partial.output_version_id
    assert updated.omitted_chapter_ids == [chapters[0].id, chapters[1].id]
    assert [item["chapter_id"] for item in updated.chapter_snapshot_json] == [chapters[2].id]


async def test_editorial_review_questions_do_not_hide_readable_book_or_export(client, test_db):
    from app.domain.memoir.types import MemoirBookChapterDraftStatus
    from app.services.memoir.narrative.book_output_assembly import assemble_book_output
    from tests.test_memoir_book_completion import _book, _create_current_draft

    project_id, chapters, _ = await _book(client, test_db, chapter_count=4)
    for chapter in chapters:
        await _create_current_draft(
            test_db, project_id=project_id, chapter_id=chapter.id,
            status=MemoirBookChapterDraftStatus.DRAFT,
        )
    for index, (issue_type, related_chapters) in enumerate((
        ("clarity", chapters[:1]),
        ("relationship", chapters[1:2]),
        ("sequence", chapters[2:3]),
        ("structure", chapters[2:]),
    )):
        test_db.add(MemoirCompletionIssue(
            project_id=project_id, scope="book", issue_type=issue_type,
            channel="manual_review", resolution="user_decision",
            question=f"这部分需要调整吗？{index}", priority="medium",
            related_chapter_ids_json=[chapter.id for chapter in related_chapters],
            fingerprint=f"{index + 1:064x}", input_signature="review",
        ))
    await test_db.commit()

    assembled = await assemble_book_output(test_db, project_id=project_id, automatic=True)
    await test_db.commit()
    assert assembled.omitted_chapter_ids == []
    assert [item["chapter_id"] for item in assembled.chapter_snapshot_json] == [
        chapter.id for chapter in chapters
    ]
    manual = await assemble_book_output(test_db, project_id=project_id)
    assert manual.output_version_id == assembled.output_version_id

    output = await client.get(f"/api/mobile/memoir/projects/{project_id}/book-output")
    assert output.status_code == 200
    assert output.json()["is_stale"] is False
    assert len(output.json()["latest_version"]["manuscript"]["chapters"]) == 4

    exported = await client.get(
        f"/api/mobile/memoir/projects/{project_id}/book-output/export?format=txt"
    )
    assert exported.status_code == 200
    assert "这是已经存在的完整章节正文。" in exported.text


async def test_completed_books_requeue_and_offer_safe_chapters_before_conflict_decision(client, test_db):
    from app.domain.memoir.types import MemoirBookChapterDraftStatus
    from app.services.memoir.narrative.book_output_assembly import assemble_book_output
    from tests.test_memoir_book_completion import _book, _create_current_draft

    project_id, chapters, _ = await _book(client, test_db, chapter_count=5)
    versions = {}
    for chapter in chapters:
        _, version = await _create_current_draft(
            test_db, project_id=project_id, chapter_id=chapter.id,
            status=MemoirBookChapterDraftStatus.DRAFT,
        )
        versions[chapter.id] = version.id
    test_db.add_all([
        MemoirCompletionIssue(
            project_id=project_id, scope="book", issue_type="structure",
            channel="manual_review", question="需要调整结构吗？", priority="medium",
            related_chapter_ids_json=[chapter.id for chapter in chapters],
            fingerprint="1" * 64, input_signature="review",
        ),
        MemoirCompletionIssue(
            project_id=project_id, scope="book", issue_type="conflict",
            channel="manual_review", question="这段经历的时间冲突如何处理？", priority="high",
            related_chapter_ids_json=[chapters[-1].id],
            fingerprint="2" * 64, input_signature="review",
        ),
    ])
    old = await assemble_book_output(
        test_db, project_id=project_id, eligible_chapter_ids=set(), automatic=True,
    )
    assert old.chapter_snapshot_json == []
    job = await automatic_book_draft.schedule_book_draft(test_db, project_id=project_id)
    job.status = "succeeded"
    job.result_json = {
        "stage": "needs_attention",
        "chapters": {
            chapter.id: {"status": "ready", "draft_version_id": versions[chapter.id]}
            for chapter in chapters
        },
        "output_id": old.output_id,
        "output_version_id": old.output_version_id,
        "omitted_chapter_ids": old.omitted_chapter_ids,
        "review_status": "succeeded",
    }
    await test_db.commit()

    assert await automatic_book_draft.reconcile_books_omitted_by_editorial_review(test_db) == 1
    await test_db.refresh(job)
    assert job.status == "pending"
    assert await automatic_book_draft.reconcile_books_omitted_by_editorial_review(test_db) == 0

    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    assert await run_test_editing_job(test_db, job.id)
    await test_db.refresh(job)
    assert job.result_json["review_job_id"] is None
    assert job.result_json["omitted_chapter_ids"] == [chapters[-1].id]
    assert [item["chapter_id"] for item in (
        await test_db.get(MemoirBookOutputVersion, job.result_json["output_version_id"])
    ).chapter_snapshot_json] == [
        chapter.id for chapter in chapters[:-1]
    ]
    conflict_issue = await test_db.scalar(select(MemoirCompletionIssue).where(
        MemoirCompletionIssue.project_id == project_id,
        MemoirCompletionIssue.issue_type == "conflict",
    ))
    assert conflict_issue.status == "open"
    response = await client.get(f"/api/mobile/memoir/projects/{project_id}/book-output")
    assert response.status_code == 200
    assert response.json()["is_stale"] is False
    assert len(response.json()["latest_version"]["manuscript"]["chapters"]) == 4
    exported = await client.get(
        f"/api/mobile/memoir/projects/{project_id}/book-output/export?format=txt"
    )
    assert exported.status_code == 200
    assert chapters[-1].title not in exported.text


async def test_automatic_book_snapshot_keeps_legacy_flat_chapter_while_upgrade_needs_review(
    client,
    test_db,
    monkeypatch,
):
    from app.domain.memoir.types import MemoirBookChapterDraftStatus
    from app.models.memoir import MemoirBookOutputVersion
    from app.services.memoir.narrative.book_output_assembly import assemble_book_output
    from tests.test_memoir_book_completion import _book, _create_current_draft

    project_id, chapters, _ = await _book(client, test_db, chapter_count=3)
    drafts = []
    versions = []
    for chapter in chapters:
        draft, version = await _create_current_draft(
            test_db,
            project_id=project_id,
            chapter_id=chapter.id,
            status=MemoirBookChapterDraftStatus.CONFIRMED,
        )
        drafts.append(draft)
        versions.append(version)
    drafts[0].status = MemoirBookChapterDraftStatus.DRAFT.value
    drafts[0].metadata_json = {"confirmed_version_id": versions[0].id}
    versions[0].section_snapshot_json = []
    test_db.add(drafts[0])
    test_db.add(versions[0])
    await test_db.commit()

    previous = await assemble_book_output(
        test_db,
        project_id=project_id,
        eligible_chapter_ids={chapters[2].id},
        automatic=True,
    )
    assert [item["chapter_id"] for item in previous.chapter_snapshot_json] == [
        chapters[2].id
    ]

    async def keep_upgrade_for_review(_db, **kwargs):
        assert kwargs["chapter_id"] == chapters[0].id
        return {
            "job_id": "legacy-upgrade-job",
            "status": "succeeded",
            "outcome": "needs_review",
            "error_message": "",
        }

    monkeypatch.setattr(
        automatic_book_draft.chapter_preparation_jobs,
        "enqueue_chapter_preparation_job",
        keep_upgrade_for_review,
    )
    parent = await automatic_book_draft.schedule_book_draft(
        test_db,
        project_id=project_id,
    )
    await test_db.commit()

    result = await automatic_book_draft.run_book_draft_job(test_db, parent)

    assert result.result_json["stage"] == "needs_attention"
    assert result.result_json["omitted_chapter_ids"] == []
    latest = await test_db.get(
        MemoirBookOutputVersion,
        result.result_json["output_version_id"],
    )
    assert latest is not None
    assert [item["chapter_id"] for item in latest.chapter_snapshot_json] == [
        chapter.id for chapter in chapters
    ]
    assert latest.chapter_snapshot_json[0]["sections"] == []
    assert latest.chapter_snapshot_json[0]["content"] == versions[0].content

    response = await client.get(
        f"/api/mobile/memoir/projects/{project_id}/book-output"
    )
    assert response.status_code == 200
    output = response.json()
    assert output["is_stale"] is False
    assert [
        chapter["chapter_id"]
        for chapter in output["latest_version"]["manuscript"]["chapters"]
    ] == [chapter.id for chapter in chapters]


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
    candidates = [{"issue_type": "conflict", "channel": "manual_review", "priority": "high",
        "question": "这段经历的时间冲突如何处理？", "reason": "存在时间冲突", "chapter_ids": [chapters[0].id]}]
    test_db.add(MemoirBookReview(project_id=project_id, engine="old", input_signature="old-input",
        status="stale", review_json={"candidate_issues": candidates}))
    await completion_issues.sync_book_review_issues(test_db, project_id=project_id,
        input_signature="old-input", candidates=candidates)
    await test_db.commit()
    result = await assemble_book_output(test_db, project_id=project_id, automatic=True)
    assert result.omitted_chapter_ids == [chapters[0].id]
    assert [item["chapter_id"] for item in result.chapter_snapshot_json] == [chapters[1].id]
