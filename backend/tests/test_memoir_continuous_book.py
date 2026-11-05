"""Continuous narration must preserve readable work and surface protected revisions."""
import copy
import json

import pytest
from sqlalchemy import select

from app.domain.memoir.types import MemoirCreatedBy, MemoirSectionMaterialAssignedBy
from app.models.memoir import (
    MemoirBookChapterDraftVersion, MemoirBookOutput, MemoirBookOutputVersion,
    MemoirBookPlanChapter, MemoirCompletionIssue, MemoirEditingJob, MemoirSection,
    MemoirSectionMaterial,
)
from app.schemas.memoir_book import (
    MemoirBookOutputChapterEditorialCopyUpdateRequest,
    MemoirBookOutputUpdateRequest,
)
from app.services.memoir import automatic_book_draft, book_outputs, book_output_versions, chapter_generation, sections
from app.services.memoir.narrative.book_output_assembly import assemble_book_output
from tests.memoir_helpers import create_test_voice_transcript, run_test_editing_job
from tests.test_memoir_automatic_book_draft import _setup


async def _ready_book(client, db, monkeypatch):
    project_id, chapter_id, section_id, fake, parent_id = await _setup(client, db, monkeypatch)
    original = fake._call_llm

    async def without_optional_questions(*args, **kwargs):
        value = json.loads(await original(*args, **kwargs))
        if "candidate_issues" in value:
            value["candidate_issues"] = []
        return json.dumps(value, ensure_ascii=False)

    monkeypatch.setattr(fake, "_call_llm", without_optional_questions)
    assert not await run_test_editing_job(db, parent_id)
    parent = await db.get(MemoirEditingJob, parent_id)
    assert await run_test_editing_job(db, parent.result_json["chapters"][chapter_id]["job_id"])
    assert not await run_test_editing_job(db, parent_id)
    assert await run_test_editing_job(db, parent.result_json["review_job_id"])
    assert await run_test_editing_job(db, parent_id)
    copy_job = await db.scalar(select(MemoirEditingJob).where(
        MemoirEditingJob.dedupe_key == f"automatic-editorial-copy:{project_id}"))
    if copy_job is not None and copy_job.status == 'pending':
        await run_test_editing_job(db, copy_job.id)
        await db.refresh(parent)
        if parent.status == 'pending':
            assert not await run_test_editing_job(db, parent_id)
            assert await run_test_editing_job(db, parent.result_json['review_job_id'])
            assert await run_test_editing_job(db, parent_id)
    return project_id, chapter_id, section_id, fake, parent


async def _add_narration(client, db, project_id, section_id, fake=None):
    added = await create_test_voice_transcript(
        client, db, "后来我又想起了爬山路上的一件小事。", project_id=project_id,
    )
    _, changed_ids = await sections.assign_material_to_section(
        db, project_id=project_id, section_id=section_id, utterance_id=added["utterance_id"],
        assigned_by=MemoirSectionMaterialAssignedBy.USER, assignment_reason="user_supplement",
    )
    if fake is not None:
        # The fake controls citation count, while the real prompt contains
        # earlier unused material as well as this new narration.
        fake.draft_material_ids = ["include-all"] * 100
    await sections.finalize_section_changes(db, project_id=project_id, section_ids=changed_ids)
    await db.commit()


async def _snapshot(client):
    response = await client.get("/api/mobile/memoir/story-snapshot")
    assert response.status_code == 200, response.text
    return response.json()


async def test_supplement_keeps_previous_book_during_generation_and_failure(client, test_db, monkeypatch):
    project_id, chapter_id, section_id, fake, parent = await _ready_book(client, test_db, monkeypatch)
    old = await test_db.get(MemoirBookOutputVersion, parent.result_json["output_version_id"])
    await _add_narration(client, test_db, project_id, section_id, fake)
    assert not await run_test_editing_job(test_db, parent.id)
    data = await _snapshot(client)
    assert data["book_readable"] and data["book_chapter_count"] == 1
    assert data["progress"]["readable_chapter_count"] == 1
    current = await test_db.get(MemoirBookOutputVersion, parent.result_json["output_version_id"])
    assert current.content == old.content
    child = await test_db.get(MemoirEditingJob, parent.result_json["chapters"][chapter_id]["job_id"])
    child.status, child.error_message = "failed", "模型调用失败"
    await test_db.commit()
    assert await run_test_editing_job(test_db, parent.id)
    assert (await _snapshot(client))["book_readable"]


@pytest.mark.parametrize("exclusion", ["chapter", "section", "material", "conflict"])
async def test_retaining_old_draft_never_reintroduces_excluded_sources(client, test_db, monkeypatch, exclusion):
    project_id, chapter_id, section_id, _, parent = await _ready_book(client, test_db, monkeypatch)
    if exclusion == "chapter":
        (await test_db.get(MemoirBookPlanChapter, chapter_id)).include_in_book = False
        test_db.add(MemoirCompletionIssue(
            project_id=project_id, scope="book", issue_type="meaning", channel="interview",
            question="旧书稿中的补充问题", priority="medium", fingerprint="d" * 64, input_signature="old-review",
        ))
    elif exclusion == "section":
        (await test_db.get(MemoirSection, section_id)).include_in_book = False
    elif exclusion == "material":
        latest = await chapter_generation.latest_chapter_draft_version(test_db, project_id=project_id, chapter_id=chapter_id)
        material = await test_db.scalar(select(MemoirSectionMaterial).where(
            MemoirSectionMaterial.section_id == section_id,
            MemoirSectionMaterial.utterance_id == latest.used_material_ids_json[0],
        ))
        material.include_in_book = False
    else:
        test_db.add(MemoirCompletionIssue(
            project_id=project_id, chapter_id=chapter_id, scope="chapter", issue_type="conflict",
            channel="manual_review", question="这段经历的时间冲突如何处理？", priority="high",
            fingerprint="c" * 64, input_signature="review",
        ))
    await automatic_book_draft.schedule_book_draft(test_db, project_id=project_id)
    await test_db.commit()
    await run_test_editing_job(test_db, parent.id)
    data = await _snapshot(client)
    assert not data["book_readable"] and data["book_chapter_count"] == 0
    assert data["book_issues"]["supplement_count"] == 0


@pytest.mark.parametrize("metadata", [{"title": "给家人看的回忆"}, {"cover_style": "warm_gray"}])
async def test_book_design_changes_allow_future_automatic_prose(client, test_db, monkeypatch, metadata):
    project_id, chapter_id, section_id, fake, parent = await _ready_book(client, test_db, monkeypatch)
    parent_id = parent.id
    user_id = (await client.get("/api/auth/me")).json()["id"]
    await book_outputs.update_book_output(test_db, user_id=user_id, project_id=project_id,
        req=MemoirBookOutputUpdateRequest(**metadata))
    original = fake._call_llm

    async def new_prose(*args, **kwargs):
        value = json.loads(await original(*args, **kwargs))
        if "content" in value:
            value["content"] += "后来我又记起了爬山路上的一件小事。"
        return json.dumps(value, ensure_ascii=False)

    monkeypatch.setattr(fake, "_call_llm", new_prose)
    await _add_narration(client, test_db, project_id, section_id, fake)
    assert not await run_test_editing_job(test_db, parent.id)
    assert await run_test_editing_job(test_db, parent.result_json["chapters"][chapter_id]["job_id"])
    await run_test_editing_job(test_db, parent_id)
    data = await _snapshot(client)
    assert not data["book_manual_edit_preserved"]
    latest = await test_db.get(MemoirBookOutputVersion, parent.result_json["output_version_id"])
    assert "后来我又记起了爬山路上的一件小事。" in latest.content
    if "title" in metadata:
        assert latest.manuscript_json["profile"]["title"] == metadata["title"]
    else:
        assert latest.manuscript_json["render_settings"]["cover_style"] == metadata["cover_style"]


@pytest.mark.parametrize("change", ["accept", "source", "manual_edit"])
async def test_candidate_is_actionable_on_home_and_disappears_when_resolved_or_obsolete(client, test_db, monkeypatch, change):
    project_id, chapter_id, section_id, fake, parent = await _ready_book(client, test_db, monkeypatch)
    parent_id = parent.id
    latest = await chapter_generation.latest_chapter_draft_version(test_db, project_id=project_id, chapter_id=chapter_id)
    latest.created_by = "user"
    await _add_narration(client, test_db, project_id, section_id, fake)
    assert not await run_test_editing_job(test_db, parent.id)
    child_id = parent.result_json["chapters"][chapter_id]["job_id"]
    assert await run_test_editing_job(test_db, child_id)
    assert await run_test_editing_job(test_db, parent_id)
    data = await _snapshot(client)
    chapter = data["chapters"][0]
    assert chapter["state"] == "attention" and chapter["candidate_job_id"] == child_id
    assert chapter["issues"] == {"review_count": 0, "supplement_count": 0}
    assert data["book_readable"]
    if change == "accept":
        response = await client.post(f"/api/memoir/projects/{project_id}/book-completion/chapters/{chapter_id}/jobs/{child_id}/candidate/accept")
        assert response.status_code == 200, response.text
    elif change == "source":
        await _add_narration(client, test_db, project_id, section_id, fake)
    else:
        await test_db.refresh(latest)
        values = {key: copy.deepcopy(getattr(latest, key)) for key in (
            "draft_id", "project_id", "chapter_id", "input_signature", "used_material_ids_json",
            "material_snapshot_json", "section_snapshot_json",
        )}
        test_db.add(MemoirBookChapterDraftVersion(**values, version=latest.version + 1, content="我又改了一遍。", created_by="user"))
        await test_db.commit()
    assert (await _snapshot(client))["chapters"][0]["candidate_job_id"] is None


@pytest.mark.parametrize("copy_field", ["subtitle", "intro"])
async def test_design_versions_keep_manual_body_protection(client, test_db, monkeypatch, copy_field):
    project_id, _, _, _, parent = await _ready_book(client, test_db, monkeypatch)
    output = await test_db.get(MemoirBookOutput, parent.result_json["output_id"])
    latest = await test_db.get(MemoirBookOutputVersion, parent.result_json["output_version_id"])
    manual = await book_output_versions.append_book_output_version(
        test_db, output=output, content="我亲自编辑的全书正文。",
        chapter_snapshot_json=latest.chapter_snapshot_json, manuscript_json=latest.manuscript_json,
        created_by=MemoirCreatedBy.USER,
    )
    design = copy.deepcopy(manual.manuscript_json)
    if copy_field == "subtitle":
        design["profile"]["subtitle"] = "写给家人的书"
    else:
        design["chapters"][0]["intro"] = "我另写了一段导语"
    changed = await book_output_versions.append_book_output_version(
        test_db, output=output, content=manual.content,
        chapter_snapshot_json=manual.chapter_snapshot_json, manuscript_json=design, created_by=MemoirCreatedBy.AI,
    )
    await test_db.commit()
    result = await assemble_book_output(test_db, project_id=project_id, automatic=True)
    assert result.preserved_manual and result.output_version_id == changed.id
    assert result.content == manual.content


@pytest.mark.parametrize("marker", ["book_review_answer_application", "book_editorial_revision_application"])
async def test_design_changes_preserve_accepted_book_revisions(client, test_db, monkeypatch, marker):
    project_id, _, _, _, parent = await _ready_book(client, test_db, monkeypatch)
    output = await test_db.get(MemoirBookOutput, parent.result_json["output_id"])
    output.metadata_json = {**output.metadata_json, marker: {"output_version_id": parent.result_json["output_version_id"]}}
    await test_db.commit()
    user_id = (await client.get("/api/auth/me")).json()["id"]
    updated = await book_outputs.update_book_output(test_db, user_id=user_id, project_id=project_id,
        req=MemoirBookOutputUpdateRequest(title="给家人看的回忆"))
    result = await assemble_book_output(test_db, project_id=project_id, automatic=True)
    assert result.preserved_manual and result.output_version_id == updated.latest_version.id


async def test_automatic_book_keeps_manual_chapter_copy_across_later_updates(client, test_db):
    from app.domain.memoir.types import MemoirBookChapterDraftStatus
    from tests.test_memoir_book_completion import _book, _create_current_draft

    project_id, chapters, _ = await _book(client, test_db, chapter_count=2)
    drafts = [
        await _create_current_draft(
            test_db,
            project_id=project_id,
            chapter_id=chapter.id,
            status=MemoirBookChapterDraftStatus.DRAFT,
        )
        for chapter in chapters
    ]
    await assemble_book_output(test_db, project_id=project_id, automatic=True)
    user_id = (await client.get("/api/auth/me")).json()["id"]
    await book_outputs.update_book_output_chapter_editorial_copy(
        test_db,
        user_id=user_id,
        project_id=project_id,
        chapter_id=chapters[0].id,
        req=MemoirBookOutputChapterEditorialCopyUpdateRequest(
            intro="我亲自写的章首语。",
            pull_quote="那年，我参加了第1次学校比赛。",
        ),
    )

    async def revise_chapter(index: int, suffix: str, *, new_material_id: str | None = None):
        draft, old = drafts[index]
        section_snapshots = copy.deepcopy(old.section_snapshot_json)
        section_snapshots[0]["content"] += suffix
        used_material_ids = copy.deepcopy(old.used_material_ids_json)
        if new_material_id:
            section_snapshots[0]["used_material_ids"].append(new_material_id)
            used_material_ids.append(new_material_id)
        updated = MemoirBookChapterDraftVersion(
            draft_id=draft.id,
            project_id=project_id,
            chapter_id=chapters[index].id,
            version=old.version + 1,
            content=old.content + suffix,
            created_by=MemoirCreatedBy.AI.value,
            input_signature=old.input_signature,
            used_material_ids_json=used_material_ids,
            material_snapshot_json=copy.deepcopy(old.material_snapshot_json),
            section_snapshot_json=section_snapshots,
        )
        test_db.add(updated)
        await test_db.commit()
        drafts[index] = (draft, updated)

    for suffix in ("第一次补充。", "第二次补充。"):
        await revise_chapter(1, suffix)
        result = await assemble_book_output(test_db, project_id=project_id, automatic=True)
        assert not result.preserved_manual
        version = await test_db.get(MemoirBookOutputVersion, result.output_version_id)
        chapter = version.manuscript_json["chapters"][0]
        assert chapter["intro"] == "我亲自写的章首语。"
        assert chapter["pull_quotes"][0]["text"] == "那年，我参加了第1次学校比赛。"
        assert suffix in result.content

    await revise_chapter(0, "本章统一润色。")
    polished = await assemble_book_output(test_db, project_id=project_id, automatic=True)
    polished_version = await test_db.get(MemoirBookOutputVersion, polished.output_version_id)
    assert "本章统一润色。" in polished.content
    assert polished_version.manuscript_json["chapters"][0]["intro"] == "我亲自写的章首语。"

    added = await create_test_voice_transcript(
        client, test_db, "后来我又讲了一次比赛经历。", project_id=project_id,
    )
    _, changed_ids = await sections.assign_material_to_section(
        test_db,
        project_id=project_id,
        section_id=drafts[0][1].section_snapshot_json[0]["section_id"],
        utterance_id=added["utterance_id"],
        assigned_by=MemoirSectionMaterialAssignedBy.USER,
        assignment_reason="user_supplement",
    )
    await sections.finalize_section_changes(test_db, project_id=project_id, section_ids=changed_ids)
    await revise_chapter(0, "本章补充。", new_material_id=added["utterance_id"])
    updated = await assemble_book_output(test_db, project_id=project_id, automatic=True)
    assert not updated.preserved_manual
    assert "本章补充。" in updated.content
    version = await test_db.get(MemoirBookOutputVersion, updated.output_version_id)
    assert version.manuscript_json["chapters"][0]["intro"] == "我亲自写的章首语。"
    assert version.manuscript_json["chapters"][0]["pull_quotes"][0]["text"] == "那年，我参加了第1次学校比赛。"


@pytest.mark.parametrize("protection", ["user", "accepted_revision"])
async def test_saved_sections_update_protected_book_and_existing_share(client, test_db, protection):
    from app.domain.memoir.types import MemoirBookChapterDraftStatus
    from app.services.memoir import book_shares
    from app.services.memoir.book_output_manuscript import manuscript_from_snapshot
    from app.services.memoir.book_output_snapshots import book_content_from_chapter_snapshots
    from tests.test_memoir_book_completion import _book, _create_current_draft

    project_id, chapters, _ = await _book(client, test_db, chapter_count=2)
    extra = MemoirSection(project_id=project_id, chapter_id=chapters[0].id,
        title="另一个故事", section_type="life_fragment", sort_order=1)
    test_db.add(extra)
    await test_db.flush()
    await _add_narration(client, test_db, project_id, extra.id)
    drafts = [await _create_current_draft(test_db, project_id=project_id, chapter_id=chapter.id,
        status=MemoirBookChapterDraftStatus.DRAFT) for chapter in chapters]
    # The helper supplies one paragraph per section; retain a consistent chapter body.
    first = drafts[0][1]
    first.content = "\n\n".join(s["content"] for s in first.section_snapshot_json)
    await test_db.commit()
    assembled = await assemble_book_output(test_db, project_id=project_id, automatic=True)
    output = await test_db.get(MemoirBookOutput, assembled.output_id)
    initial = await test_db.get(MemoirBookOutputVersion, assembled.output_version_id)
    snapshots = copy.deepcopy(initial.chapter_snapshot_json)
    snapshots[0]["sections"][1]["content"] = "已校订的小节，不能被旧章节稿覆盖。"
    snapshots[0]["content"] = "\n\n".join(s["content"] for s in snapshots[0]["sections"])
    snapshots[1]["sections"][0]["content"] = "另一章已校订的正文。"
    snapshots[1]["content"] = snapshots[1]["sections"][0]["content"]
    content = book_content_from_chapter_snapshots(title=output.title, snapshots=snapshots)
    manuscript = manuscript_from_snapshot(title=output.title, content=content, snapshot=snapshots,
        metadata={"preface": "我亲自写的序言。"}).model_dump(mode="json")
    manuscript["chapters"][0]["illustrations"] = [{"image_asset_id": "retained-image",
        "storage_path": "memoir/retained.png", "placement": "chapter_opening"}]
    protected = await book_output_versions.append_book_output_version(test_db, output=output,
        content=content, chapter_snapshot_json=snapshots, manuscript_json=manuscript,
        created_by=MemoirCreatedBy.USER if protection == "user" else MemoirCreatedBy.AI)
    if protection == "accepted_revision":
        output.metadata_json = {**output.metadata_json, "book_editorial_revision_application": {"output_version_id": protected.id}}
    await test_db.commit()
    user_id = (await client.get("/api/auth/me")).json()["id"]
    share = await book_shares.create_share(test_db, user_id=user_id, project_id=project_id)
    new_text = "我在 Web 后台刚刚修改的正文。"
    response = await client.post(f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapters[0].id}/draft/revise",
        json={"expected_version_id": first.id, "sections": [
            {"section_id": s["section_id"], "content": new_text if i == 0 else s["content"]}
            for i, s in enumerate(first.section_snapshot_json)]})
    assert response.status_code == 200, response.text
    for reading in (await book_outputs.get_book_output(test_db, user_id=user_id, project_id=project_id),
                    await book_shares.read_share(test_db, share.token)):
        assert not reading.is_stale
        assert new_text in reading.latest_version.content
        assert "已校订的小节，不能被旧章节稿覆盖。" in reading.latest_version.content
        assert "另一章已校订的正文。" in reading.latest_version.content
        assert reading.latest_version.manuscript.chapters[0].sections[0].content == new_text
    latest = await book_output_versions.load_latest_book_output_version(test_db, output_id=output.id)
    assert latest.manuscript_json["front_matter"] == manuscript["front_matter"]
    assert latest.manuscript_json["chapters"][1] == manuscript["chapters"][1]
    assert latest.manuscript_json["chapters"][0]["sections"][1] == manuscript["chapters"][0]["sections"][1]
    assert latest.manuscript_json["chapters"][0]["illustrations"] == manuscript["chapters"][0]["illustrations"]
    assert latest.manuscript_json["render_settings"] == manuscript["render_settings"]
    assert latest.manuscript_json["back_matter"] == manuscript["back_matter"]
    assert protected.content == content
    request = {"expected_version_id": first.id, "sections": [
        {"section_id": s["section_id"], "content": s["content"]}
        for s in response.json()["latest_version"]["sections"]]}
    endpoint = f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapters[0].id}/draft/revise"
    assert (await client.post(endpoint, json=request)).status_code == 409
    request["expected_version_id"] = response.json()["latest_version"]["id"]
    assert (await client.post(endpoint, json=request)).status_code == 200
    assert (await book_output_versions.load_latest_book_output_version(test_db, output_id=output.id)).id == latest.id
    # A subsequent automatic assembly must not roll back the edit or the retained prose.
    assembled_again = await assemble_book_output(test_db, project_id=project_id, automatic=True)
    assert assembled_again.output_version_id == latest.id
