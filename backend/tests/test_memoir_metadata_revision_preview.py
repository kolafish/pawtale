"""Metadata-only preview uses the existing task and leaves the manuscript intact."""
from copy import deepcopy
from dataclasses import replace

import pytest
from sqlalchemy import select

from app.domain.memoir.types import MemoirBookChapterDraftStatus
from app.models.memoir import MemoirBookOutputVersion, MemoirBookRevisionProposal
from app.services.memoir import book_revision_generation as generation
from app.services.memoir.editing_job_handlers import register_memoir_editing_job_handlers
from app.services.memoir.editing_jobs import clear_editing_job_handlers
from tests.memoir_helpers import run_test_editing_job
from tests.test_memoir_book_completion import LayeredBookAI, _book, _create_current_draft, _install_ai, _run_book_review


@pytest.mark.ci_smoke
@pytest.mark.parametrize("case", ["apply", "stale", "noop", "mixed", "extra_field"])
async def test_metadata_only_selection_previews_without_rewriting_the_book(client, test_db, monkeypatch, case):
    project_id, chapters, _ = await _book(client, test_db)
    fake = LayeredBookAI(book_actions=True, book_profile_action=True)
    _install_ai(monkeypatch, fake)
    await _create_current_draft(test_db, project_id=project_id, chapter_id=chapters[0].id,
                               status=MemoirBookChapterDraftStatus.CONFIRMED)
    # The review must see the same book metadata as the preview baseline.
    assert (await client.post(f"/api/memoir/projects/{project_id}/book-output/assemble")).status_code == 200
    review = await _run_book_review(client, test_db, project_id=project_id)
    actions = review.json()["review"]["editorial_actions"]
    selected = [a["id"] for a in actions if a["action_type"] == "revise_book_profile"]
    if case == "mixed":
        selected += [a["id"] for a in actions if a["action_type"] == "improve_transition"]
    assembled = await client.post(f"/api/memoir/projects/{project_id}/book-output/assemble")
    baseline = await test_db.get(MemoirBookOutputVersion, assembled.json()["output_version_id"])
    manuscript = deepcopy(baseline.manuscript_json)
    manuscript["chapters"][0]["intro"] = "这段导读是家人逐字写下的，请完整保留。"
    baseline.manuscript_json = manuscript
    await test_db.commit()
    before_body = deepcopy(manuscript["chapters"])
    baseline_id, before_snapshot, before_content = baseline.id, deepcopy(baseline.chapter_snapshot_json), baseline.content
    if case == "extra_field":
        generate = generation.generate_revision_plan
        async def extra(*args, **kwargs):
            result = await generate(*args, **kwargs)
            return replace(result, plan=result.plan.model_copy(update={
                "book_metadata_patch": {**result.plan.book_metadata_patch, "title": "未选择的改名"},
            }))
        monkeypatch.setattr(generation, "generate_revision_plan", extra)
    generated_body = generation._generate_revisions
    async def forbid_body_generation(*args, **kwargs):
        raise AssertionError("Metadata selection must never call the body writer")
    monkeypatch.setattr(generation, "_generate_revisions", forbid_body_generation)
    queued = await client.post(f"/api/memoir/projects/{project_id}/book-review/revision/jobs", json={
        "include_answers": False, "action_ids": selected,
    })
    assert queued.status_code == 202, queued.text
    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        assert await run_test_editing_job(test_db, queued.json()["job_id"])
    finally:
        clear_editing_job_handlers()
        monkeypatch.setattr(generation, "_generate_revisions", generated_body)
    proposal = await test_db.scalar(select(MemoirBookRevisionProposal).where(
        MemoirBookRevisionProposal.project_id == project_id,
    ))
    path = f"/api/memoir/projects/{project_id}/book-review/revision-proposals/{proposal.id}"
    preview = (await client.get(path)).json()
    assert preview["phase"] == ("plan_ready" if case in {"mixed", "extra_field"} else "draft_ready")
    await test_db.refresh(baseline)
    assert baseline.content == before_content
    assert baseline.chapter_snapshot_json == before_snapshot
    assert baseline.manuscript_json["chapters"] == before_body
    if case in {"mixed", "extra_field"}:
        assert not preview["can_apply"]
        return
    assert preview["can_apply"]
    assert preview["comparison"]["changed_section_count"] == 0
    assert preview["after"]["manuscript"]["chapters"] == before_body
    if case == "stale":
        # Updating the proposal baseline while it is open must still block save.
        newer = MemoirBookOutputVersion(
            output_id=baseline.output_id, project_id=project_id, plan_id=baseline.plan_id, version=baseline.version+1,
            content=baseline.content, chapter_snapshot_json=before_snapshot, manuscript_json=manuscript,
            created_by="user",
        )
        test_db.add(newer)
        await test_db.commit()
    elif case == "noop":
        proposal.revision_plan_json = {**proposal.revision_plan_json, "book_metadata_patch": {}}
        await test_db.commit()
    applied = await client.post(path+"/apply")
    assert applied.status_code == (409 if case in {"stale", "noop"} else 200), applied.text
    if case == "apply":
        final = await test_db.get(MemoirBookOutputVersion, applied.json()["result_output_version_id"])
        assert final.id != baseline_id
        assert final.chapter_snapshot_json == before_snapshot
        assert final.content == before_content
        assert final.manuscript_json["chapters"] == before_body
        assert final.manuscript_json["back_matter"]["afterword"]["content"] != manuscript["back_matter"]["afterword"]["content"]


@pytest.mark.parametrize("change", ["automatic_during", "automatic_after", "user_during", "user_then_automatic", "design", "body"])
async def test_revision_accepts_only_copy_updates_and_applies_on_latest_version(client, test_db, monkeypatch, change):
    from app.models.memoir import MemoirEditingJob
    from app.services.memoir import book_output_versions
    project_id, chapters, _ = await _book(client, test_db)
    _install_ai(monkeypatch, LayeredBookAI(book_actions=True, book_profile_action=True))
    await _create_current_draft(test_db, project_id=project_id, chapter_id=chapters[0].id,
                               status=MemoirBookChapterDraftStatus.CONFIRMED)
    assert (await client.post(f"/api/memoir/projects/{project_id}/book-output/assemble")).status_code == 200
    review = (await _run_book_review(client, test_db, project_id=project_id)).json()["review"]
    selected = [a["id"] for a in review["editorial_actions"] if a["action_type"] == "revise_book_profile"]
    assembled = await client.post(f"/api/memoir/projects/{project_id}/book-output/assemble")
    baseline = await test_db.get(MemoirBookOutputVersion, assembled.json()["output_version_id"])
    output_id = baseline.output_id
    async def update():
        latest = await book_output_versions.load_latest_book_output_version(test_db, output_id=output_id)
        manuscript = deepcopy(latest.manuscript_json)
        manuscript["chapters"][0]["intro"] = "多年以后，操场上奔跑的身影依然清晰。"
        if change == "design":
            manuscript["render_settings"]["font_scale"] = "large"
        newer = MemoirBookOutputVersion(output_id=output_id, project_id=project_id, plan_id=latest.plan_id,
            version=latest.version+1, content=latest.content + ("正文变化" if change == "body" else ""),
            chapter_snapshot_json=deepcopy(latest.chapter_snapshot_json), manuscript_json=manuscript,
            created_by="user" if change in {"user_during", "user_then_automatic"} else "system")
        test_db.add(newer)
        await test_db.flush()
        if change == "user_then_automatic":
            test_db.add(MemoirBookOutputVersion(output_id=output_id, project_id=project_id, plan_id=latest.plan_id,
                version=newer.version+1, content=newer.content, chapter_snapshot_json=deepcopy(newer.chapter_snapshot_json),
                manuscript_json=deepcopy(manuscript), created_by="system"))
        await test_db.commit()
    generate = generation.generate_revision_plan
    async def during(*args, **kwargs):
        result = await generate(*args, **kwargs)
        if change != "automatic_after":
            await update()
        return result
    monkeypatch.setattr(generation, "generate_revision_plan", during)
    queued = await client.post(f"/api/memoir/projects/{project_id}/book-review/revision/jobs", json={
        "include_answers": False, "action_ids": selected,
    })
    assert queued.status_code == 202
    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        succeeded = await run_test_editing_job(test_db, queued.json()["job_id"])
    finally:
        clear_editing_job_handlers()
    if not change.startswith("automatic"):
        job = await test_db.get(MemoirEditingJob, queued.json()["job_id"])
        assert not succeeded and job.status == "failed"
        assert "全书内容已经变化" in job.error_message
        return
    assert succeeded
    proposal = await test_db.scalar(select(MemoirBookRevisionProposal).where(MemoirBookRevisionProposal.project_id == project_id))
    if change == "automatic_after":
        await update()
    path = f"/api/memoir/projects/{project_id}/book-review/revision-proposals/{proposal.id}"
    preview = (await client.get(path)).json()
    assert preview["can_apply"]
    assert preview["after"]["manuscript"]["chapters"][0]["intro"] == "多年以后，操场上奔跑的身影依然清晰。"
    applied = await client.post(path+"/apply")
    assert applied.status_code == 200, applied.text
    latest = await book_output_versions.load_latest_book_output_version(test_db, output_id=output_id)
    assert latest.manuscript_json["chapters"][0]["intro"] == "多年以后，操场上奔跑的身影依然清晰。"
