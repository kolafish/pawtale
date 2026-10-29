from copy import deepcopy

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config import get_settings
from app.main import app
from app.models.memoir import MemoirBookChapterDraftVersion, MemoirBookOutput, MemoirBookOutputVersion, MemoirBookPlanChapter, MemoirSection
from app.services.memoir import demo_story
from app.services.memoir.book_output_snapshots import chapter_snapshot
from app.services.memoir.book_outputs import _output_version_response
from tests.memoir_helpers import create_test_book_plan_with_chapters, ensure_mobile_memoir_project


@pytest.mark.asyncio
async def test_visitors_read_the_whole_configured_book_but_not_excluded_chapters(client, test_db, monkeypatch):
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
            draft_contents=["父亲是个木匠。", "下乡的故事。", "不收录正文。"],
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
            content="父亲是个木匠。\n\n下乡的故事。",
            chapter_snapshot_json=[
                chapter_snapshot(chapter, drafts[chapter.id], chapter_index=index + 3)
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
            "memoir_demo_project_ids": project_id,
            "memoir_demo_video_codes": "0123456789abcdef0123",
        })
        monkeypatch.setattr(demo_story, "get_settings", lambda: settings)

        # A newer working tree must not change what the public book advertises.
        chapters[0].title = "尚未编入书稿的新章名"
        test_db.add(MemoirSection(project_id=project_id, chapter_id=chapters[0].id,
                                  title="未成书的新故事", sort_order=99))
        await test_db.commit()

        story = (await guest.get("/api/mobile/memoir/demo-story")).json()
        assert story["title"]
        assert [(chapter["title"], chapter["readable"]) for chapter in story["chapters"]] == [
            ("家里的人", True), ("下乡那几年", True),
        ]
        assert story["chapters"][0]["sections"][0]["title"] == "家里的人"
        assert len(story["chapters"][0]["sections"]) == 1
        assert story["chapters"][0]["char_count"] == len("父亲是个木匠。")
        # A video the author deleted drops out instead of breaking the sample.
        assert story["videos"] == []

        book = await guest.get("/api/mobile/memoir/demo-book")
        assert book.status_code == 200
        assert "父亲是个木匠" in book.text
        assert "下乡的故事" in book.text
        assert "不收录正文" not in book.text
        assert "private/cover.jpg" not in book.text
        assert "private-utterance" not in book.text
        assert [chapter["title"] for chapter in book.json()["latest_version"]["manuscript"]["chapters"]] == ["家里的人", "下乡那几年"]
        assert [item["title"] for item in book.json()["latest_version"]["manuscript"]["table_of_contents"]] == ["家里的人", "下乡那几年"]
        assert book.json()["latest_version"]["manuscript"]["chapters"][0]["index"] == 1
        assert book.json()["latest_version"]["manuscript"]["table_of_contents"][0]["index"] == 1

        # Removing the chapter from the book also removes it from the public sample,
        # even while a previous compiled book still contains it.
        chapters[0].include_in_book = False
        await test_db.commit()
        changed = await guest.get("/api/mobile/memoir/demo-book")
        assert changed.status_code == 200
        assert "父亲是个木匠" not in changed.json()["latest_version"]["content"]
        assert "下乡的故事" in changed.text


@pytest.mark.asyncio
async def test_demo_does_not_advertise_an_uncompiled_chapter_as_readable(client, test_db, monkeypatch):
    project_id = await ensure_mobile_memoir_project(client)
    _, chapters = await create_test_book_plan_with_chapters(
        test_db, project_id=project_id, chapter_titles=["家里的人"], draft_contents=["已有章节草稿，尚未成书。"],
    )
    monkeypatch.setattr(demo_story, "get_settings", lambda: get_settings().model_copy(update={
        "memoir_demo_project_ids": project_id,
    }))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as guest:
        story = (await guest.get("/api/mobile/memoir/demo-story")).json()
        assert story["chapters"][0]["readable"] is False
        assert (await guest.get("/api/mobile/memoir/demo-book")).status_code == 404


@pytest.mark.asyncio
async def test_public_books_follow_configured_order_and_future_compiled_chapters(client, test_db, monkeypatch):
    books = []
    for title in ("我的奶奶—李桂芳", "余仲尤的往事", "未公开的回忆录"):
        response = await client.post("/api/mobile/memoir/projects", json={"title": title})
        assert response.status_code == 201
        project_id = response.json()["id"]
        plan, chapters = await create_test_book_plan_with_chapters(
            test_db, project_id=project_id, chapter_titles=[title], draft_contents=[title + "正文"],
        )
        draft = await test_db.scalar(select(MemoirBookChapterDraftVersion).where(
            MemoirBookChapterDraftVersion.chapter_id == chapters[0].id,
        ))
        output = MemoirBookOutput(project_id=project_id, plan_id=plan.id, title=title)
        test_db.add(output)
        await test_db.flush()
        version = MemoirBookOutputVersion(
            output_id=output.id, project_id=project_id, plan_id=plan.id, version=1,
            content=title + "正文", chapter_snapshot_json=[chapter_snapshot(chapters[0], draft, chapter_index=1)],
        )
        test_db.add(version)
        await test_db.flush()
        version.manuscript_json = _output_version_response(version, fallback_title=title).manuscript.model_dump(mode="json")
        version.manuscript_json["profile"]["title"] = title
        books.append((output, version))
    await test_db.commit()
    grandma, yu, private = [book[0] for book in books]
    settings = get_settings().model_copy(update={"memoir_demo_project_ids": f"{grandma.project_id},{yu.project_id}"})
    monkeypatch.setattr(demo_story, "get_settings", lambda: settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as guest:
        catalog = await guest.get("/api/mobile/memoir/demo-story")
        assert catalog.headers["cache-control"] == "no-store"
        assert [book["id"] for book in catalog.json()["books"]] == [grandma.project_id, yu.project_id]
        default = await guest.get("/api/mobile/memoir/demo-book")
        assert default.json()["title"] == grandma.title
        for output in (grandma, yu):
            selected = await guest.get("/api/mobile/memoir/demo-book", params={"project_id": output.project_id})
            assert selected.status_code == 200
            assert selected.json()["title"] == output.title
        for endpoint in ("demo-story", "demo-book"):
            for project_id in (private.project_id, "unknown"):
                assert (await guest.get(f"/api/mobile/memoir/{endpoint}", params={"project_id": project_id})).status_code == 404

        # A newly added chapter is private until compiled, then joins the full preview
        # without editing the public project configuration.
        chapter = MemoirBookPlanChapter(project_id=grandma.project_id, plan_id=grandma.plan_id, title="后来", sort_order=2)
        test_db.add(chapter)
        await test_db.commit()
        before = await guest.get("/api/mobile/memoir/demo-book")
        assert len(before.json()["latest_version"]["manuscript"]["chapters"]) == 1
        manuscript = deepcopy(books[0][1].manuscript_json)
        extra = deepcopy(manuscript["chapters"][0])
        extra.update(chapter_id=chapter.id, title="后来", content="新成书的一章", index=2, sections=[])
        manuscript["chapters"].append(extra)
        test_db.add(MemoirBookOutputVersion(
            output_id=grandma.id, project_id=grandma.project_id, plan_id=grandma.plan_id, version=2,
            content=grandma.title + "正文\n\n新成书的一章", manuscript_json=manuscript,
        ))
        await test_db.commit()
        after = await guest.get("/api/mobile/memoir/demo-book")
        assert after.json()["latest_version"]["version"] == 2
        assert "新成书的一章" in after.text
        assert (await guest.get("/api/mobile/memoir/demo-story")).json()["books"][0]["book_chapter_count"] == 2

        # Revoking a book removes its card and its direct anonymous URL.
        settings.memoir_demo_project_ids = grandma.project_id
        assert len((await guest.get("/api/mobile/memoir/demo-story")).json()["books"]) == 1
        assert (await guest.get("/api/mobile/memoir/demo-book", params={"project_id": yu.project_id})).status_code == 404
