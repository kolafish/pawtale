"""Synthetic API/worker scenarios for editing straight into a reviewable preview."""
import pytest
from sqlalchemy import func, select

from app.domain.memoir.types import MemoirBookChapterDraftStatus
from app.models.memoir import MemoirBookChapterDraftVersion, MemoirSection, MemoirVoiceUtterance
from app.services.memoir import section_revision_jobs
from app.services.memoir.editing_job_handlers import register_memoir_editing_job_handlers
from app.services.memoir.editing_jobs import clear_editing_job_handlers
from tests.memoir_helpers import (
    create_test_book_plan_with_chapters, create_test_voice_transcript,
    ensure_mobile_memoir_project, memoir_source_ref, run_test_editing_job,
)
from tests.test_memoir_book_completion import LayeredBookAI, _create_current_draft, _install_ai


@pytest.mark.ci_smoke
@pytest.mark.parametrize("ending", ["apply", "dismiss", "stale", "failure", "stale_during_generation"])
async def test_direct_preview_retains_history_and_revises_the_latest_candidate(
    client, test_db, monkeypatch, ending,
):
    project_id = await ensure_mobile_memoir_project(client)
    source = await create_test_voice_transcript(client, test_db, "我在学校读了三年。", project_id=project_id)
    _, chapters = await create_test_book_plan_with_chapters(
        test_db, project_id=project_id, chapter_titles=["上学"],
        source_refs_by_chapter=[[memoir_source_ref(source)]],
    )
    chapter_id = chapters[0].id
    section = await test_db.scalar(select(MemoirSection).where(MemoirSection.chapter_id == chapter_id))
    section_id = section.id
    draft, baseline = await _create_current_draft(
        test_db, project_id=project_id, chapter_id=chapter_id,
        status=MemoirBookChapterDraftStatus.CONFIRMED,
    )
    draft_id, baseline_id, original_content = draft.id, baseline.id, baseline.content
    utterance = await test_db.get(MemoirVoiceUtterance, source["utterance_id"])
    original_transcript = utterance.transcript_raw
    fake = LayeredBookAI(section_revision_title="校园", section_revision_content="我在学校读了五年。\n这是保留的手写段落。")
    _install_ai(monkeypatch, fake, [])
    path = f"/api/memoir/projects/{project_id}/chapters/{chapter_id}/draft-sections/{section_id}/revision"

    async def submit(feedback, proposal_id=None, *, succeeds=True):
        response = await client.post(f"{path}/jobs", json={
            "operation": "revise", "proposal_id": proposal_id, "feedback": feedback,
        })
        assert response.status_code == 202, response.text
        job_id = response.json()["job_id"]
        clear_editing_job_handlers()
        register_memoir_editing_job_handlers()
        try:
            assert await run_test_editing_job(test_db, job_id) is succeeds
        finally:
            clear_editing_job_handlers()
        current = await client.get(f"{path}/jobs/current")
        assert current.json()["operation"] == "revise"
        preview = await client.get(f"{path}/proposals/{job_id}")
        assert preview.status_code == 200, preview.text
        return job_id, preview.json(), current.json()

    proposal_id, preview, state = await submit("改为五年，标题改成校园；保留其他内容。")
    assert state["status"] == "succeeded"
    assert preview["phase"] == "draft_ready"
    assert preview["revision_plan"] is None
    assert preview["can_apply"]
    assert preview["base_output_version_id"] == baseline_id
    assert await test_db.scalar(select(func.count(MemoirBookChapterDraftVersion.id)).where(
        MemoirBookChapterDraftVersion.draft_id == draft_id,
    )) == 1

    # Direct previews cannot accidentally enter the legacy plan-only feedback path.
    invalid = await client.post(f"{path}/jobs", json={
        "operation": "feedback", "proposal_id": proposal_id, "feedback": "再改一下",
    })
    assert invalid.status_code == 409
    fake.section_revision_title = "校园"
    fake.section_revision_content = "我在学校读了六年。\n这是保留的手写段落。"
    _, preview, state = await submit("五年改成六年，其他不动。", proposal_id)
    assert state["status"] == "succeeded"
    assert "我在学校读了五年。\n这是保留的手写段落。" in fake.prompts[-1]
    assert "当前小节标题：\n校园" in fake.prompts[-1]
    assert "改为五年，标题改成校园；保留其他内容。" in fake.prompts[-1]
    assert "本次用户要求（本次唯一编辑范围；与先前要求冲突时以本次为准）：\n五年改成六年，其他不动。" in fake.prompts[-1]
    change = preview["comparison"]["chapters"][0]["sections"][0]
    assert change["before_content"] == original_content
    assert change["after_content"] == fake.section_revision_content

    if ending == "failure":
        generate = section_revision_jobs._generate_candidate

        async def fail(*args, **kwargs):
            raise ValueError("合成的生成失败")

        monkeypatch.setattr(section_revision_jobs, "_generate_candidate", fail)
        _, failed_preview, state = await submit("再精简一点。", proposal_id, succeeds=False)
        assert state["status"] == "failed"
        assert failed_preview["comparison"] == preview["comparison"]
        assert not failed_preview["can_apply"]
        monkeypatch.setattr(section_revision_jobs, "_generate_candidate", generate)
        _, preview, state = await submit("保留当前预览，其他不动。", proposal_id)
        assert state["status"] == "succeeded"
        assert preview["can_apply"]
    elif ending == "stale":
        section = await test_db.get(MemoirSection, section_id)
        section.version += 1
        await test_db.commit()
    elif ending == "stale_during_generation":
        generate = section_revision_jobs._generate_candidate

        async def change_source_during_generation(*args, **kwargs):
            generated = await generate(*args, **kwargs)
            section = await test_db.get(MemoirSection, section_id)
            section.version += 1
            await test_db.commit()
            return generated

        monkeypatch.setattr(section_revision_jobs, "_generate_candidate", change_source_during_generation)
        _, stale_preview, state = await submit("再调整一次。", proposal_id, succeeds=False)
        assert state["status"] == "failed"
        assert stale_preview["status"] == "stale"
        assert not stale_preview["can_apply"]

    action = "dismiss" if ending == "dismiss" else "apply"
    saved = await client.post(f"{path}/proposals/{proposal_id}/{action}")
    assert saved.status_code == (409 if ending.startswith("stale") else 200), saved.text
    versions = list((await test_db.scalars(select(MemoirBookChapterDraftVersion).where(
        MemoirBookChapterDraftVersion.draft_id == draft_id,
    ).order_by(MemoirBookChapterDraftVersion.version))).all())
    assert versions[0].id == baseline_id
    assert versions[0].content == original_content
    assert len(versions) == (2 if ending in {"apply", "failure"} else 1)
    if len(versions) == 2:
        assert versions[-1].section_snapshot_json[0]["content"] == fake.section_revision_content
        assert versions[-1].section_snapshot_json[0]["title"] == "校园"
    await test_db.refresh(utterance)
    assert utterance.transcript_raw == original_transcript
