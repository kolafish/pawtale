import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config import get_settings
from app.main import app
from app.models.memoir import MemoirBookChapterDraftVersion, MemoirBookOutput, MemoirBookOutputVersion
from app.services.memoir import demo_story
from app.services.memoir.book_output_snapshots import chapter_snapshot
from app.services.memoir.book_outputs import _output_version_response
from tests.memoir_helpers import create_test_book_plan_with_chapters, ensure_mobile_memoir_project


@pytest.mark.asyncio
async def test_visitors_browse_the_demo_tree_but_read_only_its_open_chapters(client, test_db, monkeypatch):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as guest:
        empty = await guest.get("/api/mobile/memoir/demo-story")
        assert empty.status_code == 200
        assert empty.json()["chapters"] == []
        assert (await guest.get("/api/mobile/memoir/demo-book")).status_code == 404

        project_id = await ensure_mobile_memoir_project(client)
        plan, chapters = await create_test_book_plan_with_chapters(
            test_db,
            project_id=project_id,
            chapter_titles=["家里的人", "下乡那几年", "不收录的章节"],
            draft_contents=["父亲是个木匠。", "不公开的私密章节。", "不收录正文。"],
        )
        chapters[2].include_in_book = False
        drafts = {
            version.chapter_id: version
            for version in (await test_db.scalars(select(MemoirBookChapterDraftVersion))).all()
        }
        output = MemoirBookOutput(project_id=project_id, plan_id=plan.id, title="余仲尤往事")
        test_db.add(output)
        await test_db.flush()
        version = MemoirBookOutputVersion(
            output_id=output.id,
            project_id=project_id,
            plan_id=plan.id,
            version=1,
            content="父亲是个木匠。\n\n不公开的私密章节。",
            chapter_snapshot_json=[
                chapter_snapshot(chapter, drafts[chapter.id], chapter_index=index + 1)
                for index, chapter in enumerate(chapters)
            ],
        )
        test_db.add(version)
        await test_db.flush()
        version.manuscript_json = _output_version_response(version, fallback_title=output.title).manuscript.model_dump(mode="json")
        version.manuscript_json["render_settings"]["cover_image"] = {
            "image_asset_id": "private-cover", "storage_path": "private/cover.jpg",
            "source_utterance_id": "private-utterance",
        }
        await test_db.commit()
        settings = get_settings().model_copy(update={
            "memoir_demo_chapter_ids": chapters[0].id,
            "memoir_demo_video_codes": "0123456789abcdef0123",
        })
        monkeypatch.setattr(demo_story, "get_settings", lambda: settings)

        story = (await guest.get("/api/mobile/memoir/demo-story")).json()
        assert story["title"]
        assert [(chapter["title"], chapter["readable"]) for chapter in story["chapters"]] == [
            ("家里的人", True), ("下乡那几年", False),
        ]
        assert story["chapters"][0]["sections"][0]["title"] == "家里的人"
        # A video the author deleted drops out instead of breaking the sample.
        assert story["videos"] == []

        book = await guest.get("/api/mobile/memoir/demo-book")
        assert book.status_code == 200
        assert "父亲是个木匠" in book.text
        assert "不公开的私密章节" not in book.text
        assert "private/cover.jpg" not in book.text
        assert "private-utterance" not in book.text
        assert [chapter["title"] for chapter in book.json()["latest_version"]["manuscript"]["chapters"]] == ["家里的人"]
        assert [item["title"] for item in book.json()["latest_version"]["manuscript"]["table_of_contents"]] == ["家里的人"]

        # Removing the chapter from the book also removes it from the public sample,
        # even while a previous compiled book still contains it.
        chapters[0].include_in_book = False
        await test_db.commit()
        assert (await guest.get("/api/mobile/memoir/demo-book")).status_code == 404


@pytest.mark.asyncio
async def test_demo_does_not_advertise_an_uncompiled_chapter_as_readable(client, test_db, monkeypatch):
    project_id = await ensure_mobile_memoir_project(client)
    _, chapters = await create_test_book_plan_with_chapters(
        test_db, project_id=project_id, chapter_titles=["家里的人"], draft_contents=["已有章节草稿，尚未成书。"],
    )
    monkeypatch.setattr(demo_story, "get_settings", lambda: get_settings().model_copy(update={
        "memoir_demo_chapter_ids": chapters[0].id,
    }))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as guest:
        story = (await guest.get("/api/mobile/memoir/demo-story")).json()
        assert story["chapters"][0]["readable"] is False
        assert (await guest.get("/api/mobile/memoir/demo-book")).status_code == 404
