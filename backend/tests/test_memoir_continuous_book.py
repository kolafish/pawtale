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
    return project_id, chapter_id, section_id, fake, parent


async def _add_narration(client, db, project_id, section_id, fake=None):
    added = await create_test_voice_transcript(
        client, db, "后来我又想起了爬山路上的一件小事。", project_id=project_id,
    )
    await sections.assign_material_to_section(
        db, project_id=project_id, section_id=section_id, utterance_id=added["utterance_id"],
        assigned_by=MemoirSectionMaterialAssignedBy.USER, assignment_reason="user_supplement",
    )
    if fake is not None:
        # The fake controls citation count, while the real prompt contains
        # earlier unused material as well as this new narration.
        fake.draft_material_ids = ["include-all"] * 100
    await automatic_book_draft.schedule_book_draft(db, project_id=project_id)
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


@pytest.mark.parametrize("exclusion", ["chapter", "section", "material", "privacy"])
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
            project_id=project_id, chapter_id=chapter_id, scope="chapter", issue_type="privacy",
            channel="manual_review", question="是否保留这段私人经历？", priority="high",
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
        if "sections" in value:
            value["sections"][0]["content"] += "后来我又记起了爬山路上的一件小事。"
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


async def test_design_versions_keep_manual_body_protection(client, test_db, monkeypatch):
    project_id, _, _, _, parent = await _ready_book(client, test_db, monkeypatch)
    output = await test_db.get(MemoirBookOutput, parent.result_json["output_id"])
    latest = await test_db.get(MemoirBookOutputVersion, parent.result_json["output_version_id"])
    manual = await book_output_versions.append_book_output_version(
        test_db, output=output, content="我亲自编辑的全书正文。",
        chapter_snapshot_json=latest.chapter_snapshot_json, manuscript_json=latest.manuscript_json,
        created_by=MemoirCreatedBy.USER,
    )
    design = copy.deepcopy(manual.manuscript_json)
    design["profile"]["subtitle"] = "写给家人的书"
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
    await sections.assign_material_to_section(
        test_db,
        project_id=project_id,
        section_id=drafts[0][1].section_snapshot_json[0]["section_id"],
        utterance_id=added["utterance_id"],
        assigned_by=MemoirSectionMaterialAssignedBy.USER,
        assignment_reason="user_supplement",
    )
    await revise_chapter(0, "本章补充。", new_material_id=added["utterance_id"])
    protected = await assemble_book_output(test_db, project_id=project_id, automatic=True)
    assert protected.preserved_manual
    assert "本章补充。" not in protected.content
