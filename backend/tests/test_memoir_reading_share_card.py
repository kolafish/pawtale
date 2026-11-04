import asyncio
from io import BytesIO

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.main import app
from app.models.memoir import MemoirBookChapterDraftVersion, MemoirBookPlanChapter, MemoirSection, MemoirSectionShare
from app.models.projects import Project
from app.schemas.memoir_book import MemoirReadingShareCardRequest
from app.services.memoir import book_shares, reading_share_card, sections
from tests.memoir_helpers import ensure_mobile_memoir_project
from tests.test_memoir_book_shares import _shareable_output
from tests.test_memoir_story_shares import saved_stories
from tests.test_memoir_share_likes import _family_member


async def test_mobile_template_covers_are_public_cacheable_jpegs():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as guest:
        for style in ("heritage_classic", "warm_journal", "modern_minimal", "moonlit_courtyard", "navy_gilt"):
            response = await guest.get(f"/api/mobile/memoir/book-covers/{style}.jpg")
            assert response.status_code == 200
            assert response.headers["content-type"] == "image/jpeg"
            assert "public" in response.headers["cache-control"]
            with Image.open(BytesIO(response.content)) as image:
                assert image.format == "JPEG"
                image.verify()
        assert (await guest.get("/api/mobile/memoir/book-covers/classic_plain.jpg")).status_code == 404


async def test_book_card_is_public_jpeg_with_real_metadata_and_revocable(client, test_db, monkeypatch):
    project_id = await ensure_mobile_memoir_project(client)
    _, version = await _shareable_output(test_db, project_id, "# 外婆回忆录\n\n真实的书稿正文。")
    path = f"/api/mobile/memoir/projects/{project_id}/book-output/share"
    token = (await client.put(path)).json()["token"]
    captured = []
    def capture(renderer):
        def render(**kwargs):
            captured.append(kwargs)
            return renderer(**kwargs)
        return render

    for name in ("render_book_share_card", "render_chapter_share_card"):
        monkeypatch.setattr(reading_share_card, name, capture(getattr(reading_share_card, name)))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as guest:
        url = f"/api/mobile/memoir/book-shares/{token}/card.jpg"
        response = await guest.get(url)
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"
        assert response.headers["cache-control"] == "no-store"
        with Image.open(BytesIO(response.content)) as image:
            assert image.size == (1000, 800)
        assert captured[-1] == dict(title="外婆回忆录", tagline="", image_content=None, picture_label="",
                                   chapter_count=1, char_count=len(version.content))
        chapter = version.chapter_snapshot_json[0]
        chapter_token = (await client.put(path, params={"chapter_id": chapter["chapter_id"]})).json()["token"]
        chapter_response = await guest.get(f"/api/mobile/memoir/book-shares/{chapter_token}/card.jpg")
        assert chapter_response.status_code == 200
        assert captured[-1]["title"] == chapter["chapter_title"]
        assert captured[-1]["book_title"] == "外婆回忆录"
        assert captured[-1]["excerpt"] == ""
        await client.delete(path)
        assert (await guest.get(url)).status_code == 404
        assert len(captured) == 2


@pytest.mark.parametrize(("subtitle", "quote", "expected"), [
    ("一个基层干部的口述人生", "记得家乡的河。", "一个基层干部的口述人生"),
    ("  ", "记得家乡的河。", "记得家乡的河。"),
    ("", "", ""),
])
async def test_book_card_uses_saved_subtitle_then_back_cover_quote(client, test_db, monkeypatch, subtitle, quote, expected):
    from app.services.memoir.book_output_manuscript import manuscript_from_snapshot

    project_id = await ensure_mobile_memoir_project(client)
    output, version = await _shareable_output(test_db, project_id, "已经入书的正文。")
    manuscript = manuscript_from_snapshot(title=output.title, content=version.content, snapshot=version.chapter_snapshot_json)
    manuscript.profile.subtitle = subtitle
    manuscript.render_settings.back_cover_quote = quote
    version.manuscript_json = manuscript.model_dump(mode="json")
    await test_db.commit()
    token = (await client.put(f"/api/mobile/memoir/projects/{project_id}/book-output/share")).json()["token"]
    monkeypatch.setattr(reading_share_card, "render_book_share_card", lambda **kw: kw["tagline"].encode())
    response = await client.get(f"/api/mobile/memoir/book-shares/{token}/card.jpg")
    assert response.status_code == 200 and response.text == expected


async def test_story_card_only_excerpts_its_confirmed_scoped_version(client, test_db, monkeypatch):
    project_id, chapter, _, version, section, _, _ = await saved_stories(client, test_db)
    path = f"/api/mobile/memoir/projects/{project_id}/book-output/share"
    token = (await client.put(path, params={"chapter_id": chapter.id, "section_id": section.id})).json()["token"]
    captured = []
    monkeypatch.setattr(reading_share_card, "render_chapter_share_card", lambda **kwargs: captured.append(kwargs) or b"jpeg")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as guest:
        url = f"/api/mobile/memoir/book-shares/{token}/card.jpg"
        assert (await guest.get(url)).status_code == 200
        assert captured[-1]["excerpt"] == ""
        params = {"version_id": version.id, "excerpt_start": 0, "excerpt_length": len("院子里的正文")}
        assert (await guest.get(url, params=params)).status_code == 200
        assert captured[-1]["excerpt"] == ""
        target = {"chapter_id": chapter.id, "section_id": section.id}
        choice = {"token": token, **params}
        assert (await client.patch(path, params=target, json=choice)).status_code == 200
        assert (await guest.get(url, params=params)).status_code == 200
        assert captured[-1]["title"] == "院子里的回忆"
        assert captured[-1]["excerpt"] == "院子里的正文"
        # The adjacent private story and newly changed versions are never used.
        assert (await guest.get(url, params={**params, "version_id": "old-version"})).status_code == 200
        assert captured[-1]["excerpt"] == ""
        assert (await guest.get(url, params={**params, "excerpt_start": 10})).status_code == 200
        assert captured[-1]["excerpt"] == ""
        assert (await guest.get(url, params={**params, "excerpt_length": 49})).status_code == 422
        assert (await client.patch(path, params=target, json={**choice, "excerpt_length": 0})).status_code == 200
        assert (await guest.get(url, params=params)).status_code == 200
        assert captured[-1]["excerpt"] == ""
        section.include_in_book = False
        await test_db.commit()
        assert (await guest.get(url, params=params)).status_code == 409


@pytest.mark.parametrize("scope", ["chapter", "story"])
async def test_confirmed_card_selection_survives_status_reads_and_clears_on_link_rotation(client, test_db, scope):
    project_id, chapter, _, version, section, other, _ = await saved_stories(client, test_db)
    path = f"/api/mobile/memoir/projects/{project_id}/book-output/share"
    params = {"chapter_id": chapter.id, **({"section_id": section.id} if scope == "story" else {})}
    token = (await client.put(path, params=params)).json()["token"]
    choice = {"version_id": version.id, "excerpt_start": 0, "excerpt_length": 6}
    saved = await client.patch(path, params=params, json={"token": token, **choice})
    assert saved.status_code == 200, saved.text
    assert saved.json() == choice
    # A later page/device reads persisted state; idempotent preparation retains it.
    assert (await client.put(path, params=params)).json()["token"] == token
    assert (await client.get(path, params=params)).json()["card_selection"] == choice
    other_params = {"chapter_id": chapter.id, "section_id": other.id}
    assert (await client.get(path, params=other_params)).json()["card_selection"] is None
    choice["excerpt_length"] = 0
    assert (await client.patch(path, params=params, json={"token": token, **choice})).status_code == 200
    assert (await client.get(path, params=params)).json()["card_selection"] == choice
    rotated = (await client.post(path, params=params)).json()["token"]
    assert rotated != token
    assert (await client.get(path, params=params)).json()["card_selection"] is None
    assert (await client.patch(path, params=params, json={"token": token, **choice})).status_code == 409
    await client.delete(path, params=params)
    assert (await client.patch(path, params=params, json={"token": rotated, **choice})).status_code == 409


@pytest.mark.parametrize("scope", ["chapter", "story"])
async def test_card_choice_requires_owner_current_version_and_scoped_bounds(client, test_db, scope):
    project_id, chapter, draft, version, section, other, _ = await saved_stories(client, test_db)
    path = f"/api/mobile/memoir/projects/{project_id}/book-output/share"
    params = {"chapter_id": chapter.id, **({"section_id": section.id} if scope == "story" else {})}
    token = (await client.put(path, params=params)).json()["token"]
    payload = {"token": token, "version_id": version.id, "excerpt_start": 0, "excerpt_length": 6}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as guest:
        assert (await guest.patch(path, params=params, json=payload)).status_code == 401
    async with await _family_member(test_db, "另一个账号") as other_user:
        assert (await other_user.patch(path, params=params, json=payload)).status_code in (403, 404)
    wrong_scope = {"chapter_id": chapter.id, "section_id": other.id}
    assert (await client.patch(path, params=wrong_scope, json=payload)).status_code == 409
    for invalid in ({"excerpt_start": -1}, {"excerpt_length": 49}, {"excerpt_start": 999}, {"version_id": "old"}):
        assert (await client.patch(path, params=params, json={**payload, **invalid})).status_code in (409, 422)
    if scope == "story":
        # Offsets into adjacent chapter prose cannot be saved for this story.
        assert (await client.patch(path, params=params, json={**payload, "excerpt_start": 8})).status_code == 422
    assert (await client.patch(path, params=params, json=payload)).status_code == 200
    test_db.add(MemoirBookChapterDraftVersion(project_id=project_id, chapter_id=chapter.id, draft_id=draft.id,
        version=2, content="更新后的正文", section_snapshot_json=version.section_snapshot_json))
    await test_db.commit()
    # The old version stays explicit in status so the owner can reconfirm; it
    # cannot authorize a new version, and an in-flight old confirmation fails.
    assert (await client.get(path, params=params)).json()["card_selection"]["version_id"] == version.id
    assert (await client.patch(path, params=params, json=payload)).status_code == 409


@pytest.mark.parametrize("change,expected_status", [("withdraw", 409), ("archive", 404), ("move", 404)])
async def test_card_selection_rechecks_story_after_waiting_for_directory_lock(client, test_db, monkeypatch, change, expected_status):
    project_id, chapter, _, version, section, _, _ = await saved_stories(client, test_db)
    destination = MemoirBookPlanChapter(project_id=project_id, plan_id=chapter.plan_id, title="另一章", sort_order=1)
    test_db.add(destination)
    await test_db.commit()
    user_id = await test_db.scalar(select(Project.user_id).where(Project.id == project_id))
    path = f"/api/mobile/memoir/projects/{project_id}/book-output/share"
    token = (await client.put(path, params={"chapter_id": chapter.id, "section_id": section.id})).json()["token"]
    sessions = async_sessionmaker(test_db.bind, expire_on_commit=False)
    async with sessions() as editor, sessions() as sharer:
        await sections.lock_project_chapters(editor, project_id=project_id)
        patch = {"withdraw": {"include_in_book": False}, "archive": {"status": "archived"}, "move": {"chapter_id": destination.id}}[change]
        await editor.execute(update(MemoirSection).where(MemoirSection.id == section.id).values(**patch))
        waiting = asyncio.Event()
        lock = sections.lock_project_chapters

        async def signal_wait(db, **kwargs):
            if db is sharer:
                waiting.set()
            await lock(db, **kwargs)

        monkeypatch.setattr(sections, "lock_project_chapters", signal_wait)
        pending = asyncio.create_task(book_shares.save_card_selection(sharer, user_id=user_id, project_id=project_id,
            chapter_id=chapter.id, section_id=section.id,
            selection=MemoirReadingShareCardRequest(token=token, version_id=version.id, excerpt_length=6)))
        try:
            await asyncio.wait_for(waiting.wait(), timeout=5)
            # The PATCH has read the old ORM object and is waiting for our lock.
            await editor.commit()
            with pytest.raises(HTTPException) as error:
                await asyncio.wait_for(pending, timeout=5)
            assert error.value.status_code == expected_status
        finally:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            await sharer.rollback()
    saved = await test_db.get(MemoirSectionShare, section.id, populate_existing=True)
    assert saved.card_version_id is None


def test_card_renderer_handles_long_titles_and_no_excerpt():
    title = "很长的真实书名" * 20
    for data in (reading_share_card.render_book_share_card(title=title, chapter_count=8, char_count=12000),
                 reading_share_card.render_chapter_share_card(title=title, book_title="家人的回忆录" * 20)):
        with Image.open(BytesIO(data)) as image:
            assert image.size == (1000, 800)
            assert image.format == "JPEG"


def test_identically_sized_books_have_their_own_title_on_the_cover():
    def cover(title):
        return reading_share_card.render_book_share_card(title=title, chapter_count=5, char_count=5016)

    assert cover("外婆的小院") != cover("爷爷的木工房")
