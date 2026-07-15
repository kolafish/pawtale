from sqlalchemy import select

from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirChapterSourceLinkStatus,
)
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirChapterSourceLink,
)
from tests.memoir_helpers import (
    create_test_book_plan_with_chapters,
    create_test_chapter_draft,
    create_test_voice_transcript,
    ensure_mobile_memoir_project,
    memoir_source_ref,
)


async def _chapter_source_links(test_db, *, project_id: str) -> list[MemoirChapterSourceLink]:
    return (
        await test_db.execute(
            select(MemoirChapterSourceLink)
            .where(MemoirChapterSourceLink.project_id == project_id)
            .order_by(MemoirChapterSourceLink.chapter_id, MemoirChapterSourceLink.utterance_id)
        )
    ).scalars().all()


async def test_memoir_chapter_source_editor_returns_plan_and_current_source_status(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    first = await create_test_voice_transcript(client, test_db, "小时候在外婆家院子里摘菜。", project_id=project_id)
    second = await create_test_voice_transcript(client, test_db, "后来我第一次离家去县城读书。", project_id=project_id)
    await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["外婆家的童年"],
        source_refs_by_chapter=[[memoir_source_ref(first)]],
    )

    response = await client.get(f"/api/memoir/projects/{project_id}/chapter-source-editor")

    assert response.status_code == 200
    data = response.json()
    assert data["plan_status"] == "confirmed"
    assert data["covered_source_count"] == 1
    assert data["uncovered_source_count"] == 1
    assert data["chapters"][0]["source_refs"][0]["utterance_id"] == first["utterance_id"]
    sources_by_id = {source["utterance_id"]: source for source in data["sources"]}
    assert sources_by_id[first["utterance_id"]]["current_chapter_title"] == "外婆家的童年"
    assert sources_by_id[first["utterance_id"]]["is_included"] is True
    assert sources_by_id[second["utterance_id"]]["current_chapter_id"] is None
    assert [source["utterance_id"] for source in data["uncovered_sources"]] == [second["utterance_id"]]


async def test_memoir_chapter_source_editor_assignment_marks_existing_draft_for_revision(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    first = await create_test_voice_transcript(client, test_db, "小时候在外婆家的院子里晒太阳。", project_id=project_id)
    later = await create_test_voice_transcript(client, test_db, "后来我又讲了第一次离家去县城读书。", project_id=project_id)
    _, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["童年和外婆家"],
        source_refs_by_chapter=[[memoir_source_ref(first)]],
    )
    chapter_id = chapters[0].id
    draft = await create_test_chapter_draft(test_db, project_id=project_id, chapter_id=chapter_id)

    response = await client.post(
        f"/api/memoir/projects/{project_id}/chapter-source-editor/assignments",
        json={
            "assignments": [
                {
                    "chapter_id": chapter_id,
                    "utterance_ids": [later["utterance_id"]],
                }
            ],
            "ai_model": "deepseek-v4-pro",
        },
    )

    assert response.status_code == 200
    data = response.json()
    chapter = data["chapters"][0]
    assert [ref["utterance_id"] for ref in chapter["source_refs"]] == [
        first["utterance_id"],
        later["utterance_id"],
    ]
    assert chapter["pending_source_count"] == 1
    assert data["uncovered_source_count"] == 0
    sources_by_id = {source["utterance_id"]: source for source in data["sources"]}
    assert sources_by_id[later["utterance_id"]]["current_chapter_id"] == chapter_id

    refreshed_draft = await test_db.scalar(
        select(MemoirBookChapterDraft).where(MemoirBookChapterDraft.id == draft.id)
    )
    assert refreshed_draft.status == MemoirBookChapterDraftStatus.DRAFT.value
    assert refreshed_draft.metadata_json["status_reason"] == "new_sources_assigned"
async def test_memoir_chapter_source_editor_move_returns_updated_current_chapter(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    first = await create_test_voice_transcript(client, test_db, "第一条年节记忆。", project_id=project_id)
    second = await create_test_voice_transcript(client, test_db, "第二条年节记忆。", project_id=project_id)
    third = await create_test_voice_transcript(client, test_db, "童年友情里的伙伴。", project_id=project_id)
    _, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["岁首欢聚记", "童年友情"],
        source_refs_by_chapter=[
            [memoir_source_ref(first), memoir_source_ref(second)],
            [memoir_source_ref(third)],
        ],
    )

    response = await client.post(
        f"/api/memoir/projects/{project_id}/chapter-source-editor/moves",
        json={
            "source_chapter_id": chapters[0].id,
            "target_chapter_id": chapters[1].id,
            "utterance_id": second["utterance_id"],
        },
    )

    assert response.status_code == 200
    data = response.json()
    chapters_by_id = {chapter["id"]: chapter for chapter in data["chapters"]}
    assert [ref["utterance_id"] for ref in chapters_by_id[chapters[0].id]["source_refs"]] == [first["utterance_id"]]
    assert [ref["utterance_id"] for ref in chapters_by_id[chapters[1].id]["source_refs"]] == [
        third["utterance_id"],
        second["utterance_id"],
    ]
    moved_source = next(source for source in data["sources"] if source["utterance_id"] == second["utterance_id"])
    assert moved_source["current_chapter_id"] == chapters[1].id
    assert moved_source["current_chapter_title"] == "童年友情"


async def test_memoir_chapter_source_editor_removal_marks_removed_link_and_revision(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    first = await create_test_voice_transcript(client, test_db, "第一条年节记忆。", project_id=project_id)
    second = await create_test_voice_transcript(client, test_db, "第二条年节记忆。", project_id=project_id)
    _, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["岁首欢聚记"],
        source_refs_by_chapter=[[memoir_source_ref(first), memoir_source_ref(second)]],
    )
    chapter_id = chapters[0].id
    draft = await create_test_chapter_draft(test_db, project_id=project_id, chapter_id=chapter_id)

    response = await client.post(
        f"/api/memoir/projects/{project_id}/chapter-source-editor/removals",
        json={
            "chapter_id": chapter_id,
            "utterance_ids": [second["utterance_id"]],
        },
    )

    assert response.status_code == 200
    data = response.json()
    chapter = data["chapters"][0]
    assert [ref["utterance_id"] for ref in chapter["source_refs"]] == [first["utterance_id"]]
    assert chapter["pending_source_count"] == 0
    assert chapter["removed_source_count"] == 1
    removed_source = next(source for source in data["sources"] if source["utterance_id"] == second["utterance_id"])
    assert removed_source["current_chapter_id"] is None
    assert data["uncovered_source_count"] == 1

    links = [
        link
        for link in await _chapter_source_links(test_db, project_id=project_id)
        if link.utterance_id == second["utterance_id"]
    ]
    assert len(links) == 1
    assert links[0].status == MemoirChapterSourceLinkStatus.REMOVED.value
    assert links[0].removed_at is not None

    refreshed_draft = await test_db.scalar(
        select(MemoirBookChapterDraft).where(MemoirBookChapterDraft.id == draft.id)
    )
    assert refreshed_draft.status == MemoirBookChapterDraftStatus.DRAFT.value
    assert refreshed_draft.metadata_json["status_reason"] == "sources_changed"
