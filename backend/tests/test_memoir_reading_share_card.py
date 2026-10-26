from io import BytesIO

from httpx import ASGITransport, AsyncClient
from PIL import Image

from app.main import app
from app.services.memoir import reading_share_card
from tests.memoir_helpers import ensure_mobile_memoir_project
from tests.test_memoir_book_shares import _shareable_output
from tests.test_memoir_story_shares import saved_stories


async def test_book_card_is_public_jpeg_with_real_metadata_and_revocable(client, test_db, monkeypatch):
    project_id = await ensure_mobile_memoir_project(client)
    _, version = await _shareable_output(test_db, project_id, "# 外婆回忆录\n\n真实的书稿正文。")
    path = f"/api/mobile/memoir/projects/{project_id}/book-output/share"
    token = (await client.put(path)).json()["token"]
    captured = []
    render = reading_share_card.render_reading_share_card

    def capture(**kwargs):
        captured.append(kwargs)
        return render(**kwargs)

    monkeypatch.setattr(reading_share_card, "render_reading_share_card", capture)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as guest:
        url = f"/api/mobile/memoir/book-shares/{token}/card.jpg"
        response = await guest.get(url)
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"
        assert response.headers["cache-control"] == "no-store"
        with Image.open(BytesIO(response.content)) as image:
            assert image.size == (1000, 800)
        assert captured[-1] == dict(title="外婆回忆录", book_title="外婆回忆录", is_book=True,
                                   chapter_count=1, char_count=len(version.content), excerpt="")
        chapter = version.chapter_snapshot_json[0]
        chapter_token = (await client.put(path, params={"chapter_id": chapter["chapter_id"]})).json()["token"]
        chapter_response = await guest.get(f"/api/mobile/memoir/book-shares/{chapter_token}/card.jpg")
        assert chapter_response.status_code == 200
        assert captured[-1]["is_book"] is False
        assert captured[-1]["title"] == chapter["chapter_title"]
        assert captured[-1]["book_title"] == "外婆回忆录"
        assert captured[-1]["excerpt"] == ""
        await client.delete(path)
        assert (await guest.get(url)).status_code == 404
        assert len(captured) == 2


async def test_story_card_only_excerpts_its_confirmed_scoped_version(client, test_db, monkeypatch):
    project_id, chapter, _, version, section, _, _ = await saved_stories(client, test_db)
    path = f"/api/mobile/memoir/projects/{project_id}/book-output/share"
    token = (await client.put(path, params={"chapter_id": chapter.id, "section_id": section.id})).json()["token"]
    captured = []
    monkeypatch.setattr(reading_share_card, "render_reading_share_card", lambda **kwargs: captured.append(kwargs) or b"jpeg")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as guest:
        url = f"/api/mobile/memoir/book-shares/{token}/card.jpg"
        assert (await guest.get(url)).status_code == 200
        assert captured[-1]["excerpt"] == ""
        params = {"version_id": version.id, "excerpt_start": 0, "excerpt_length": 48}
        assert (await guest.get(url, params=params)).status_code == 200
        assert captured[-1]["title"] == "院子里的回忆"
        assert captured[-1]["is_book"] is False
        assert captured[-1]["excerpt"] == "院子里的正文"
        # The adjacent private story and newly changed versions are never used.
        assert (await guest.get(url, params={**params, "version_id": "old-version"})).status_code == 200
        assert captured[-1]["excerpt"] == ""
        assert (await guest.get(url, params={**params, "excerpt_start": 10})).status_code == 200
        assert captured[-1]["excerpt"] == ""
        assert (await guest.get(url, params={**params, "excerpt_length": 49})).status_code == 422
        section.include_in_book = False
        await test_db.commit()
        assert (await guest.get(url, params=params)).status_code == 409


def test_card_renderer_handles_long_titles_and_no_excerpt():
    for is_book in (True, False):
        data = reading_share_card.render_reading_share_card(title="很长的真实书名" * 20,
            book_title="家人的回忆录" * 20, is_book=is_book)
        with Image.open(BytesIO(data)) as image:
            assert image.size == (1000, 800)
            assert image.format == "JPEG"
