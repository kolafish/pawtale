import pytest

from decimal import Decimal
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
    assert result["reading_link"] is None and result["image_url"] == ""
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
        assert captured[-1]["content"] == result["content"] and captured[-1]["title"] == result["title"]
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


async def test_512_character_snapshot_stays_complete_but_card_only_renders_teaser(client, test_db, monkeypatch):
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
        card = await guest.get(f"/api/mobile/memoir/excerpt-shares/{token}/card.jpg")
        assert card.status_code == 200
        assert selected[:64].rstrip("，。！？；：、,.;!?: ") + "…" in drawn
        assert selected not in drawn
        assert (await guest.post("/api/mobile/memoir/excerpt-shares/read", json={"token": token})).json()["content"] == selected


async def test_excerpt_title_uses_full_selection_and_does_not_publish(client, test_db, monkeypatch):
    import json
    from types import SimpleNamespace
    from app.services import ai_calls
    project_id, chapter, _, version, _, _, _ = await saved_stories(client, test_db)
    selected = "回忆🌿。" * 128
    version.content = selected + "私密结尾"
    await test_db.commit()
    received = []

    async def generate(_client, system, prompt, **kwargs):
        received.append((system, json.loads(prompt)))
        return SimpleNamespace(value=kwargs["output_model"](title="门口的那段往事"))

    monkeypatch.setattr(ai_calls, "generate_structured_with_client", generate)
    path = f"/api/mobile/memoir/projects/{project_id}/chapters/{chapter.id}/excerpt-shares"
    response = await client.post(path + "/title", json={"version_id": version.id, "content": selected})
    assert response.status_code == 200, response.text
    assert response.json()["title"] == "门口的那段往事"
    assert received[0][1] == {"selected_text": selected}
    assert "好友" in received[0][0]
    assert await test_db.scalar(select(MemoirExcerptShare)) is None
    assert (await client.post(path + "/title", json={"version_id": version.id, "content": "不在原文中"})).status_code == 422
    assert len(received) == 1
    created = await client.post(path, json={"version_id": version.id, "content": selected, "title": "我确认的标题"})
    assert created.json()["title"] == "我确认的标题"


async def test_excerpt_continuation_requires_current_book_or_chapter_permission(client, test_db):
    from app.models.memoir import MemoirBookOutput, MemoirBookOutputVersion
    from app.services.memoir.book_output_snapshots import chapter_snapshot
    project_id, chapter, _, version, section, _, _ = await saved_stories(client, test_db)
    path = f"/api/mobile/memoir/projects/{project_id}/book-output/share"
    created = await client.post(f"/api/mobile/memoir/projects/{project_id}/chapters/{chapter.id}/excerpt-shares",
                                json={"version_id": version.id, "content": "院子里的正文", "title": "院子里的故事"})
    token = created.json()["token"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as guest:
        async def link():
            return (await guest.post("/api/mobile/memoir/excerpt-shares/read", json={"token": token})).json()["reading_link"]
        assert await link() is None
        chapter_token = (await client.put(path, params={"chapter_id": chapter.id})).json()["token"]
        assert await link() == {"scope": "chapter", "token": chapter_token}
        output = MemoirBookOutput(project_id=project_id, plan_id=chapter.plan_id, title="回忆录")
        test_db.add(output)
        await test_db.flush()
        test_db.add(MemoirBookOutputVersion(output_id=output.id, project_id=project_id, plan_id=chapter.plan_id,
            version=1, content=version.content, chapter_snapshot_json=[chapter_snapshot(chapter, version, chapter_index=1)]))
        await test_db.commit()
        book_token = (await client.put(path)).json()["token"]
        assert await link() == {"scope": "book", "token": book_token}
        await client.delete(path)
        assert await link() == {"scope": "chapter", "token": chapter_token}
        await client.delete(path, params={"chapter_id": chapter.id})
        assert await link() is None
        # Re-authorizing a chapter cannot expose withdrawn material through a continuation.
        await client.put(path, params={"chapter_id": chapter.id})
        section.include_in_book = False
        await test_db.commit()
        assert await link() is None


async def test_excerpt_photo_uses_managed_asset_and_honors_deletion(client, test_db, tmp_path, monkeypatch):
    from app.models.memoir import MemoirImageAsset
    from app.services.memoir import image_assets
    from app.services import content_storage
    project_id, chapter, _, version, _, _, _ = await saved_stories(client, test_db)
    monkeypatch.setattr(image_assets, "DATA_DIR", tmp_path)
    monkeypatch.setattr(content_storage, "PROJECTS_DIR", tmp_path / "projects")
    buffer = BytesIO()
    Image.new("RGB", (500, 300), "#abcabc").save(buffer, "PNG")
    asset = (await client.post(f"/api/mobile/memoir/projects/{project_id}/image-assets",
                              files={"file": ("family.png", buffer.getvalue(), "image/png")})).json()
    path = f"/api/mobile/memoir/projects/{project_id}/chapters/{chapter.id}/excerpt-shares"
    selection = {"version_id": version.id, "content": "院子里的正文", "title": "一家人的院子", "image_asset_id": asset["id"]}
    created = await client.post(path, json=selection)
    assert created.status_code == 200, created.text
    snapshot = created.json()
    assert snapshot["image_url"] and snapshot["image_disclosure"] == ""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as guest:
        assert (await guest.get(snapshot["image_url"])).status_code == 200
        card = await guest.get(f'/api/mobile/memoir/excerpt-shares/{snapshot["token"]}/card.jpg')
        with Image.open(BytesIO(card.content)) as rendered:
            assert rendered.size == (1000, 800)
            assert all(abs(actual - expected) <= 2 for actual, expected in zip(rendered.getpixel((500, 300)), (171, 202, 188)))
        await client.delete(f'/api/mobile/memoir/projects/{project_id}/image-assets/{asset["id"]}')
        assert (await guest.get(snapshot["image_url"])).status_code == 404
        read = (await guest.post("/api/mobile/memoir/excerpt-shares/read", json={"token": snapshot["token"]})).json()
        assert read["image_url"] == "" and read["content"] == selection["content"]
        assert (await client.post(path, json=selection)).status_code == 422
    stored = await test_db.get(MemoirImageAsset, asset["id"])
    stored.status = "ready"
    other_project = Project(title="另一部私密回忆录", user_id=stored.user_id, type="memoir")
    test_db.add(other_project)
    await test_db.flush()
    stored.project_id = other_project.id
    await test_db.commit()
    assert (await client.post(path, json=selection)).status_code == 422


@pytest.mark.parametrize("background_note", ["", "我们是我和妹妹，那年我十岁"])
async def test_excerpt_image_reuses_jobs_styles_and_asset_management_with_single_flash_image(client, test_db, tmp_path, monkeypatch, background_note):
    from app.config import get_settings
    from app.services.memoir import material_image_generation, image_assets
    from app.services.memoir.share_video_backgrounds import VolcArkGeneratedImage, share_video_background_style_options
    from tests.test_memoir_image_assets import _png_bytes, register_memoir_editing_job_handlers, run_test_editing_job
    project_id, chapter, _, version, _, _, _ = await saved_stories(client, test_db)
    monkeypatch.setattr(material_image_generation, "DATA_DIR", tmp_path)
    monkeypatch.setattr(image_assets, "DATA_DIR", tmp_path)
    calls = []

    async def generate(service, prompt):
        calls.append((service.model, prompt))
        return VolcArkGeneratedImage(content=_png_bytes(), mime_type="image/png", model=service.model, usage={"generated_images": 1})

    monkeypatch.setattr(material_image_generation.VolcArkImageGenerationService, "generate", generate)
    register_memoir_editing_job_handlers()
    path = f"/api/mobile/memoir/projects/{project_id}/chapters/{chapter.id}/excerpt-shares"
    payload = {"version_id": version.id, "content": "院子里的正文", "style_id": "ink_paper", "background_note": background_note}
    pending = await client.post(path + "/image-jobs", json=payload)
    assert pending.status_code == 200, pending.text
    job = pending.json()
    assert (await client.post(path + "/image-jobs", json=payload)).json()["job_id"] == job["job_id"]
    assert (await client.post(path, json={**{k: v for k, v in payload.items() if k not in {"style_id", "background_note"}}, "image_asset_id": job["asset_id"]})).status_code == 422
    assert await run_test_editing_job(test_db, job["job_id"])
    completed = (await client.get(f'/api/mobile/memoir/image-generation/jobs/{job["job_id"]}')).json()
    assert completed["asset_status"] == "ready"
    assert len(calls) == 1 and calls[0][0] == get_settings().memoir_excerpt_image_model
    assert payload["content"] in calls[0][1]
    assert background_note in calls[0][1]
    assert calls[0][0] != get_settings().volc_ark_image_model
    assets = (await client.get(f"/api/mobile/memoir/projects/{project_id}/image-assets")).json()["items"]
    assert len(assets) == 1
    asset = assets[0]
    assert asset["source_chapter_id"] == chapter.id and asset["source_quote"] == payload["content"]
    assert asset["metadata_json"]["generation_estimated_cost"]["amount_rmb"] == 0.12
    assert not asset["include_in_book"]
    assert (await client.get("/api/mobile/memoir/image-generation/styles")).json() == share_video_background_style_options()
    shared = (await client.post(path, json={"version_id": version.id, "content": payload["content"], "image_asset_id": asset["id"], "title": "院子的往事"})).json()
    assert shared["image_disclosure"] == image_assets.AI_GENERATED_IMAGE_DISCLOSURE
    assert (await client.post(path + "/image-jobs", json=payload)).json()["job_id"] == job["job_id"]
    assert len(calls) == 1
    summary = (await client.get("/api/billing/ai-credit-summary")).json()
    assert Decimal(summary["used_rmb"]) == Decimal("0.12")
    [day] = (await client.get("/api/billing/ai-usage")).json()["days"]
    assert [(item["purpose"], Decimal(item["cost_rmb"])) for item in day["purposes"]] == [
        ("图片生成", Decimal("0.12")),
    ]

    changed = await client.post(path + "/image-jobs", json={**payload, "background_note": "我和哥哥，那年我八岁"})
    assert changed.status_code == 200
    assert changed.json()["asset_id"] != job["asset_id"]
    repeated = await client.post(path + "/image-jobs", json={**payload, "background_note": "  我们是我和妹妹，那年我十岁  " if background_note else "  "})
    assert repeated.json()["job_id"] == job["job_id"]
    assert (await client.post(path + "/image-jobs", json={**payload, "background_note": "我" * 301})).status_code == 422
