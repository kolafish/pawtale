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
