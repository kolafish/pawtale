from io import BytesIO

from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import select

from app.main import app
from app.models.memoir import MemoirBookChapterDraftVersion, MemoirChapterShare, MemoirExcerptShare
from app.models.projects import Project
from app.services.memoir import reading_share_card
from tests.test_memoir_story_shares import saved_stories
from tests.test_memoir_share_likes import _family_member


async def test_excerpt_is_a_snapshot_and_cannot_authorize_full_reading(client, test_db, monkeypatch):
    project_id, chapter, draft, version, _, _, _ = await saved_stories(client, test_db)
    path = f"/api/mobile/memoir/projects/{project_id}/chapters/{chapter.id}/excerpt-shares"
    payload = {"version_id": version.id, "content": "院子里的正文"}
    created = await client.post(path, json=payload)
    assert created.status_code == 200, created.text
    result = created.json()
    token = result.pop("token")
    assert token.startswith("x1.")
    assert set(result) == {"book_title", "chapter_title", "content"}
    assert await test_db.get(MemoirChapterShare, chapter.id) is None
    project = await test_db.get(Project, project_id)
    project.title, chapter.title = "新书名", "新章节名"
    test_db.add(MemoirBookChapterDraftVersion(project_id=project_id, chapter_id=chapter.id, draft_id=draft.id,
        version=2, content="改写后的正文", section_snapshot_json=version.section_snapshot_json))
    await test_db.commit()
    captured = []
    render = reading_share_card.render_excerpt_share_card
    monkeypatch.setattr(reading_share_card, "render_excerpt_share_card", lambda **kwargs: captured.append(kwargs) or render(**kwargs))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as guest:
        response = await guest.post("/api/mobile/memoir/excerpt-shares/read", json={"token": token})
        assert response.json() == result
        assert "另一则私密正文" not in response.text
        assert response.headers["cache-control"] == "no-store"
        image = await guest.get(f"/api/mobile/memoir/excerpt-shares/{token}/card.jpg", params={"content": "另一则私密正文"})
        assert image.status_code == 200
        assert captured[-1] == result
        with Image.open(BytesIO(image.content)) as card:
            assert card.format == "JPEG" and card.size == (1000, 800)
        assert (await guest.post("/api/mobile/memoir/book-shares/read", json={"token": token})).status_code == 404
        assert (await guest.get(f"/api/mobile/memoir/book-shares/{token}/card.jpg")).status_code == 404
        assert (await guest.post("/api/mobile/memoir/book-shares/audio/plan", json={"token": token, "version_id": version.id})).status_code == 404
        assert (await guest.post("/api/mobile/memoir/excerpt-shares/read", json={"token": token + "x"})).status_code == 404
    # Old source versions may be cleaned up; the explicit published snapshot remains exact.
    await test_db.delete(version)
    await test_db.commit()
    stored = await test_db.scalar(select(MemoirExcerptShare))
    await test_db.refresh(stored)
    assert stored.version_id is None
    assert (await client.post("/api/mobile/memoir/excerpt-shares/read", json={"token": token})).json() == result


async def test_excerpt_creation_requires_owner_current_version_and_exact_bounded_text(client, test_db):
    project_id, chapter, _, version, section, _, _ = await saved_stories(client, test_db)
    path = f"/api/mobile/memoir/projects/{project_id}/chapters/{chapter.id}/excerpt-shares"
    payload = {"version_id": version.id, "content": "院子里的正文"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as guest:
        assert (await guest.post(path, json=payload)).status_code == 401
    async with await _family_member(test_db, "另一位用户") as other:
        assert (await other.post(path, json=payload)).status_code in (403, 404)
    for content in ("", " \n ", "未选中的伪造文字", "字" * 513):
        assert (await client.post(path, json={**payload, "content": content})).status_code == 422
    assert (await client.post(path, json={**payload, "version_id": "old"})).status_code == 409
    section.include_in_book = False
    await test_db.commit()
    assert (await client.post(path, json=payload)).status_code == 409
    assert await test_db.scalar(select(MemoirExcerptShare)) is None


async def test_excerpt_and_chapter_likes_share_one_record_per_person(client, test_db):
    project_id, chapter, _, version, _, _, _ = await saved_stories(client, test_db)
    path = f"/api/mobile/memoir/projects/{project_id}/chapters/{chapter.id}/excerpt-shares"
    tokens = [(await client.post(path, json={"version_id": version.id, "content": text})).json()["token"]
              for text in ("院子里的正文", "另一则私密正文")]
    share_path = f"/api/mobile/memoir/projects/{project_id}/book-output/share"
    chapter_token = (await client.put(share_path, params={"chapter_id": chapter.id})).json()["token"]
    async with await _family_member(test_db, "三姑") as aunt, await _family_member(test_db, "小叔") as uncle:
        for token in [*tokens, chapter_token]:
            liked = await aunt.put("/api/mobile/memoir/share-likes", json={"reading_token": token, "liked": True})
            assert liked.status_code == 200, liked.text
            assert liked.json()["count"] == 1
        assert (await uncle.put("/api/mobile/memoir/share-likes", json={"reading_token": tokens[1], "liked": True})).json()["count"] == 2
        assert (await client.get(share_path, params={"chapter_id": chapter.id})).json()["likes"]["count"] == 2
        assert (await aunt.put("/api/mobile/memoir/share-likes", json={"reading_token": chapter_token, "liked": False})).json()["count"] == 1
        state = (await aunt.post("/api/mobile/memoir/share-likes/read", json={"reading_token": tokens[0]})).json()
        assert state["count"] == 1 and state["liked"] is False


def test_maximum_excerpt_card_renders_without_extra_assets():
    data = reading_share_card.render_excerpt_share_card(book_title="长书名" * 20, chapter_title="长章节名" * 20, content="选中的真实原文。" * 64)
    with Image.open(BytesIO(data)) as card:
        assert card.size == (1000, 800)


async def test_512_character_snapshot_stays_complete_but_card_only_renders_80(client, test_db, monkeypatch):
    project_id, chapter, _, version, _, _, _ = await saved_stories(client, test_db)
    selected = "回忆🌿。" * 128
    version.content = selected + "不公开的文字"
    await test_db.commit()
    path = f"/api/mobile/memoir/projects/{project_id}/chapters/{chapter.id}/excerpt-shares"
    rejected = await client.post(path, json={"version_id": version.id, "content": selected + "不"})
    assert rejected.status_code == 422
    created = await client.post(path, json={"version_id": version.id, "content": selected})
    assert created.status_code == 200, created.text
    token = created.json()["token"]
    stored = await test_db.scalar(select(MemoirExcerptShare))
    assert stored.content == selected and len(stored.content) == 512
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as guest:
        read = await guest.post("/api/mobile/memoir/excerpt-shares/read", json={"token": token})
        assert read.json()["content"] == selected
        assert "不公开的文字" not in read.text
        drawn = []
        original = reading_share_card._text
        monkeypatch.setattr(reading_share_card, "_text", lambda image, text, box, **kwargs: drawn.append(text) or original(image, text, box, **kwargs))
        reading_share_card.render_excerpt_share_card.cache_clear()
        card = await guest.get(f"/api/mobile/memoir/excerpt-shares/{token}/card.jpg")
        assert card.status_code == 200
        assert selected[:80] + "…" in drawn
        assert selected not in drawn
        assert (await guest.post("/api/mobile/memoir/excerpt-shares/read", json={"token": token})).json()["content"] == selected
