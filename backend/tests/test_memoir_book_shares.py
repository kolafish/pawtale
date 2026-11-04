import secrets
from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.auth import session_token_hash
from app.clock import utcnow_naive
from app.config import get_settings
from app.services.memoir import book_shares
from app.main import app
from app.models.projects import Project
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirBookOutput,
    MemoirBookOutputVersion,
    MemoirBookShare,
    MemoirChapterShare,
    MemoirBookPlanChapter,
)
from app.services.memoir.book_output_snapshots import chapter_snapshot
from tests.memoir_helpers import (
    add_test_section_materials,
    create_test_book_plan,
    create_test_chapter_draft,
    create_test_voice_transcript,
    memoir_source_ref,
    ensure_mobile_memoir_project,
)


async def _shareable_output(test_db, project_id: str, content: str):
    plan, chapter = await create_test_book_plan(test_db, project_id=project_id)
    draft = await create_test_chapter_draft(
        test_db, project_id=project_id, chapter_id=chapter.id, content=content,
    )
    draft_version = await test_db.scalar(
        select(MemoirBookChapterDraftVersion).where(MemoirBookChapterDraftVersion.draft_id == draft.id)
    )
    output = MemoirBookOutput(project_id=project_id, plan_id=plan.id, title="外婆回忆录")
    test_db.add(output)
    await test_db.flush()
    snapshot = [chapter_snapshot(
        chapter,
        draft_version,
        chapter_index=1,
        source_refs=[{"utterance_id": "private-utterance-id", "quote": "未入书的原话"}],
    )]
    version = MemoirBookOutputVersion(
        output_id=output.id,
        project_id=project_id,
        plan_id=plan.id,
        version=1,
        content=content,
        chapter_snapshot_json=snapshot,
    )
    test_db.add(version)
    await test_db.commit()
    return output, version


async def test_chapter_share_uses_saved_book_narrator_including_explicit_blank(client, test_db):
    from app.services.memoir.book_output_manuscript import manuscript_from_snapshot

    project_id = await ensure_mobile_memoir_project(client)
    _, version = await _shareable_output(test_db, project_id, "本章正文")
    project = await test_db.get(Project, project_id)
    project.metadata_json = {**project.metadata_json, "memoir_profile": {"narrator_name": "访谈资料姓名"}}
    manuscript = manuscript_from_snapshot(title="回忆录", content=version.content,
        snapshot=version.chapter_snapshot_json, metadata={"narrator_name": "作者保存的署名"})
    version.manuscript_json = manuscript.model_dump(mode="json")
    await test_db.commit()
    path = f"/api/mobile/memoir/projects/{project_id}/book-output/share/preview"
    params = {"chapter_id": version.chapter_snapshot_json[0]["chapter_id"]}
    response = await client.get(path, params=params)
    assert response.status_code == 200, response.text
    assert response.json()["latest_version"]["manuscript"]["profile"]["narrator_name"] == "作者保存的署名"

    manuscript.profile.narrator_name = ""
    version.manuscript_json = manuscript.model_dump(mode="json")
    await test_db.commit()
    cleared = await client.get(path, params=params)
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["latest_version"]["manuscript"]["profile"]["narrator_name"] == ""


@pytest.mark.asyncio
async def test_book_share_is_public_read_only_auto_updated_and_revocable(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    output, first = await _shareable_output(test_db, project_id, "第一版，只给家人阅读。")

    share_path = f"/api/mobile/memoir/projects/{project_id}/book-output/share"
    initial_status = await client.get(share_path)
    assert initial_status.headers["cache-control"] == "no-store"
    assert initial_status.json() == {
        "card_selection": None,
        "active": False, "expires_at": None, "token": None, "stale": False, "current": False,
        "likes": {"count": 0, "likers": []},
    }
    created = await client.post(share_path)
    assert created.status_code == 200
    assert created.headers["cache-control"] == "no-store"
    assert created.json()["expires_at"] is None
    first_token = created.json()["token"]
    assert (await client.put(share_path)).json()["token"] == first_token
    assert first_token.startswith(f"v1.{output.id}.")
    recovered = await client.get(share_path)
    assert recovered.headers["cache-control"] == "no-store"
    assert recovered.json()["active"] is True
    assert recovered.json()["current"] is True
    assert recovered.json()["token"] == first_token
    assert (await client.get(share_path)).json()["token"] == first_token

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as guest:
        async def read(token):
            return await guest.post("/api/mobile/memoir/book-shares/read", json={"token": token})

        assert (await guest.get(share_path)).status_code == 401
        assert (await guest.get(f"/api/mobile/memoir/projects/{project_id}/book-output")).status_code == 401
        first_read = await read(first_token)
        assert first_read.status_code == 200
        assert first_read.headers["cache-control"] == "no-store"
        assert first_read.json()["latest_version"]["content"] == "第一版，只给家人阅读。"
        assert first_read.json()["latest_version"]["manuscript"]["chapters"][0]["sections"][0]["content"] == "第一版，只给家人阅读。"
        assert "private-utterance-id" not in first_read.text
        assert "未入书的原话" not in first_read.text
        assert len(first_read.json()["versions"]) == 1
        assert (await read(first_token[:-1] + ("0" if first_token[-1] != "0" else "1"))).status_code == 404

        second = MemoirBookOutputVersion(
            output_id=output.id,
            project_id=project_id,
            plan_id=first.plan_id,
            version=2,
            content="第二版，新增了内容。",
            chapter_snapshot_json=first.chapter_snapshot_json,
        )
        test_db.add(second)
        await test_db.commit()
        assert (await read(first_token)).json()["latest_version"]["content"] == second.content
        assert (await client.get(share_path)).json()["current"] is True

        updated = await client.put(share_path)
        assert updated.status_code == 200
        assert updated.json()["token"] == first_token
        assert (await read(first_token)).json()["latest_version"]["content"] == second.content

        rotated = await client.post(share_path)
        assert rotated.status_code == 200
        second_token = rotated.json()["token"]
        assert second_token != first_token
        assert (await client.get(share_path)).json()["token"] == second_token
        assert (await read(first_token)).status_code == 404
        assert (await read(second_token)).json()["latest_version"]["content"] == second.content

        share = await test_db.get(MemoirBookShare, output.id)
        share.expires_at = utcnow_naive() - timedelta(seconds=1)
        await test_db.commit()
        assert (await read(second_token)).status_code == 404
        assert (await client.get(share_path)).json() == {
            "card_selection": None,
            "active": False, "expires_at": None, "token": None, "stale": False, "current": False,
            "likes": {"count": 0, "likers": []},
        }

        renewed = await client.post(share_path)
        third_token = renewed.json()["token"]
        assert third_token != second_token
        assert (await read(third_token)).status_code == 200
        assert (await client.delete(share_path)).status_code == 204
        assert (await client.get(share_path)).json()["active"] is False
        assert (await read(third_token)).status_code == 404


@pytest.mark.asyncio
async def test_book_share_keeps_existing_random_tokens_readable(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    output, version = await _shareable_output(test_db, project_id, "家人可读的第一版。")
    legacy_token = secrets.token_urlsafe(32)
    test_db.add(MemoirBookShare(
        output_id=output.id,
        version_id=version.id,
        token_hash=session_token_hash(legacy_token),
        expires_at=utcnow_naive() + timedelta(days=7),
    ))
    await test_db.commit()

    share_path = f"/api/mobile/memoir/projects/{project_id}/book-output/share"
    recovered_token = (await client.get(share_path)).json()["token"]
    assert recovered_token.startswith(f"v1.{output.id}.")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as guest:
        async def read(token):
            return await guest.post("/api/mobile/memoir/book-shares/read", json={"token": token})

        assert (await read(legacy_token)).json()["latest_version"]["content"] == version.content
        assert (await read(recovered_token)).json()["latest_version"]["content"] == version.content

        replaced = await client.post(share_path)
        assert replaced.json()["token"] != recovered_token
        assert (await read(legacy_token)).status_code == 404
        assert (await read(recovered_token)).status_code == 404


@pytest.mark.asyncio
async def test_book_share_keeps_saved_text_during_unassembled_chapter_update(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    output, first = await _shareable_output(test_db, project_id, "旧稿")
    share_path = f"/api/mobile/memoir/projects/{project_id}/book-output/share"
    token = (await client.post(share_path)).json()["token"]

    draft = await test_db.scalar(select(MemoirBookChapterDraft).where(
        MemoirBookChapterDraft.project_id == project_id,
    ))
    test_db.add(MemoirBookChapterDraftVersion(
        draft_id=draft.id,
        project_id=project_id,
        chapter_id=draft.chapter_id,
        version=2,
        content="新章节内容，尚未汇入全书稿",
    ))
    await test_db.commit()

    status = (await client.get(share_path)).json()
    assert status["active"] is True
    assert status["stale"] is False
    assert status["current"] is True
    assert (await client.put(share_path)).json()["token"] == token
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as guest:
        response = await guest.post("/api/mobile/memoir/book-shares/read", json={"token": token})
        assert response.json()["latest_version"]["content"] == "旧稿"
    assert (await test_db.get(MemoirBookShare, output.id)).version_id == first.id


@pytest.mark.asyncio
async def test_chapter_share_without_book_is_scoped_versioned_and_independently_revocable(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    plan, chapter = await create_test_book_plan(test_db, project_id=project_id)
    draft = await create_test_chapter_draft(test_db, project_id=project_id, chapter_id=chapter.id, content="本章第一版")
    first = await test_db.scalar(select(MemoirBookChapterDraftVersion).where(MemoirBookChapterDraftVersion.draft_id == draft.id))
    other = MemoirBookPlanChapter(project_id=project_id, plan_id=plan.id, title="不分享的另一章", sort_order=1)
    test_db.add(other)
    await test_db.commit()
    share_path = f"/api/mobile/memoir/projects/{project_id}/book-output/share?chapter_id={chapter.id}"
    status = await client.get(share_path)
    assert status.status_code == 200
    assert status.json()["active"] is False
    created = await client.post(share_path)
    assert created.status_code == 200, created.text
    token = created.json()["token"]
    assert token.startswith(f"c1.{chapter.id}.")
    assert (await client.get(share_path)).json()["token"] == token
    assert await test_db.scalar(select(MemoirBookOutput).where(MemoirBookOutput.project_id == project_id)) is None

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as guest:
        async def read(link):
            return await guest.post("/api/mobile/memoir/book-shares/read", json={"token": link})

        assert (await guest.post(share_path)).status_code == 401
        response = await read(token)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        body = response.json()
        assert body["title"] == chapter.title
        assert body["latest_version"]["manuscript"]["profile"]["title"] == chapter.title
        assert body["latest_version"]["content"] == "本章第一版"
        assert [item["chapter_id"] for item in body["latest_version"]["manuscript"]["chapters"]] == [chapter.id]
        assert [item["chapter_id"] for item in body["latest_version"]["manuscript"]["table_of_contents"]] == [chapter.id]
        assert other.id not in response.text
        assert "不分享的另一章" not in response.text
        assert (await read(token.replace("c1.", "v1.", 1))).status_code == 404
        assert (await read(token.replace(chapter.id, other.id))).status_code == 404
        missing_path = share_path.replace(chapter.id, "missing")
        assert (await client.post(missing_path)).status_code == 404

        second = MemoirBookChapterDraftVersion(
            draft_id=draft.id, project_id=project_id, chapter_id=chapter.id, version=2,
            content="本章第二版", created_by=first.created_by,
            section_snapshot_json=[{**item, "content": "本章第二版"} for item in first.section_snapshot_json],
            material_snapshot_json=first.material_snapshot_json,
            used_material_ids_json=first.used_material_ids_json,
        )
        test_db.add(second)
        await test_db.commit()
        assert (await read(token)).json()["latest_version"]["content"] == "本章第二版"
        assert (await client.get(share_path)).json()["current"] is True
        updated = await client.put(share_path)
        assert updated.status_code == 200, updated.text
        assert updated.json() == created.json()
        assert (await read(token)).json()["latest_version"]["content"] == "本章第二版"

        # A full-book link can coexist and has an independent lifetime.
        output = MemoirBookOutput(project_id=project_id, plan_id=plan.id, title="全书")
        test_db.add(output)
        await test_db.flush()
        test_db.add(MemoirBookOutputVersion(output_id=output.id, project_id=project_id, plan_id=plan.id, version=1,
            content=second.content, chapter_snapshot_json=[chapter_snapshot(chapter, second, chapter_index=1)]))
        await test_db.commit()
        book_path = share_path.split("?")[0]
        book_token = (await client.post(book_path)).json()["token"]
        assert (await read(book_token)).status_code == 200
        rotated = (await client.post(share_path)).json()["token"]
        assert (await read(token)).status_code == 404
        assert (await read(rotated)).status_code == 200
        assert (await client.delete(share_path)).status_code == 204
        assert (await read(rotated)).status_code == 404
        assert (await read(book_token)).status_code == 200
        renewed = (await client.post(share_path)).json()["token"]
        share = await test_db.get(MemoirChapterShare, chapter.id)
        share.expires_at = utcnow_naive() - timedelta(seconds=1)
        await test_db.commit()
        assert (await read(renewed)).status_code == 404
        assert (await client.get(share_path)).json()["active"] is False


@pytest.mark.asyncio
async def test_chapter_share_pauses_when_a_saved_source_is_excluded(client, test_db):
    from app.models.memoir import MemoirSectionMaterial
    from app.services.memoir import chapter_generation

    project_id = await ensure_mobile_memoir_project(client)
    source = await create_test_voice_transcript(client, test_db, project_id=project_id)
    _, chapter = await create_test_book_plan(test_db, project_id=project_id, source_ref=memoir_source_ref(source))
    draft = await create_test_chapter_draft(test_db, project_id=project_id, chapter_id=chapter.id, content="需要更新的章节")
    context = await chapter_generation.load_chapter_generation_context(test_db, project_id=project_id, chapter_id=chapter.id)
    version = await test_db.scalar(select(MemoirBookChapterDraftVersion).where(MemoirBookChapterDraftVersion.draft_id == draft.id))
    version.input_signature = context.input_signature
    version.material_snapshot_json = chapter_generation._snapshot(context, used_material_ids={source["utterance_id"]})
    version.section_snapshot_json = [
        {**item, "input_signature": chapter_generation._section_input_signature(context, section)}
        for item, section in zip(version.section_snapshot_json, context.sections, strict=True)
    ]
    await test_db.commit()
    path = f"/api/mobile/memoir/projects/{project_id}/book-output/share?chapter_id={chapter.id}"
    token = (await client.post(path)).json()["token"]
    material = await test_db.scalar(select(MemoirSectionMaterial).where(MemoirSectionMaterial.utterance_id == source["utterance_id"]))
    material.include_in_book = False
    await test_db.commit()
    assert (await client.get(path)).json()["stale"] is True
    assert (await client.post(path)).status_code == 409
    assert (await client.put(path)).status_code == 409
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as guest:
        assert (await guest.post("/api/mobile/memoir/book-shares/read", json={"token": token})).status_code == 409


@pytest.mark.asyncio
async def test_book_share_withdraws_a_removed_chapter_before_reassembly(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    _, version = await _shareable_output(test_db, project_id, "后来决定不分享的章节")
    path = f"/api/mobile/memoir/projects/{project_id}/book-output/share"
    token = (await client.put(path)).json()["token"]
    assert (await client.put(path)).json()["token"] == token
    chapter = await test_db.get(MemoirBookPlanChapter, version.chapter_snapshot_json[0]["chapter_id"])
    chapter.include_in_book = False
    await test_db.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as guest:
        assert (await guest.post("/api/mobile/memoir/book-shares/read", json={"token": token})).status_code == 409
    assert (await client.get(path)).json()["stale"] is True


async def test_share_migration_preserves_live_tokens_without_reviving_expired(client, test_db):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    project_id = await ensure_mobile_memoir_project(client)
    output, version = await _shareable_output(test_db, project_id, "旧版家书")
    draft = await test_db.scalar(select(MemoirBookChapterDraftVersion).where(
        MemoirBookChapterDraftVersion.project_id == project_id))
    live = MemoirBookShare(output_id=output.id, version_id=version.id,
        token_hash=session_token_hash("old-live"), expires_at=utcnow_naive() + timedelta(days=1))
    expired = MemoirChapterShare(chapter_id=draft.chapter_id, version_id=draft.id,
        token_hash=session_token_hash("old-expired"), expires_at=utcnow_naive() - timedelta(days=1))
    test_db.add_all([live, expired])
    await test_db.commit()
    token = (await client.get(f"/api/mobile/memoir/projects/{project_id}/book-output/share")).json()["token"]
    spec = importlib.util.spec_from_file_location("scoped_shares", Path(__file__).parents[1] / "alembic/versions/f8a3c5e7b209_persistent_scoped_shares.py")
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def migrate(session):
        with Operations.context(MigrationContext.configure(session.connection())):
            migration.downgrade()
            migration.upgrade()

    await test_db.run_sync(migrate)
    await test_db.commit()
    await test_db.refresh(live)
    await test_db.refresh(expired)
    assert live.expires_at is None
    assert expired.expires_at < utcnow_naive()
    assert (await client.get(f"/api/mobile/memoir/projects/{project_id}/book-output/share")).json()["token"] == token
    assert (await client.get(f"/api/mobile/memoir/projects/{project_id}/book-output/share",
                            params={"chapter_id": draft.chapter_id})).json()["active"] is False


@pytest.mark.asyncio
async def test_friend_preview_and_public_media_have_identical_scope_without_publishing(client, test_db, tmp_path, monkeypatch):
    from PIL import Image
    from app.models.memoir import MemoirImageAsset, MemoirBookImagePlacement
    from app.services.memoir import image_assets
    from app.services.memoir.book_output_manuscript import manuscript_from_snapshot

    project_id = await ensure_mobile_memoir_project(client)
    output, version = await _shareable_output(test_db, project_id, "我们家的故事")
    manuscript = manuscript_from_snapshot(title=output.title, content=version.content, snapshot=version.chapter_snapshot_json,
        metadata={"narrator_name": "外婆", "prepared_by": "家人整理"})
    manuscript.front_matter = [item.model_copy(update={"content": "书中的前言", "enabled": True}) for item in manuscript.front_matter if item.kind == "preface"]
    manuscript.back_matter.afterword.content = "书中的后记"
    manuscript.back_matter.afterword.enabled = True
    version.manuscript_json = manuscript.model_dump(mode="json")
    user_id = (await client.get("/api/auth/me")).json()["id"]
    asset = MemoirImageAsset(project_id=project_id, user_id=user_id, source_type="manual_upload", source_signature="shared-cover",
        storage_path="cover.png", mime_type="image/png", status="ready", source_quote="秘密原话", caption="外婆家的院子")
    test_db.add(asset)
    await test_db.flush()
    placement = MemoirBookImagePlacement(project_id=project_id, image_asset_id=asset.id, placement_type="cover")
    test_db.add(placement)
    source = await create_test_voice_transcript(client, test_db, project_id=project_id)
    chapter_id = version.chapter_snapshot_json[0]["chapter_id"]
    await add_test_section_materials(test_db, project_id=project_id, chapter_id=chapter_id,
                                     source_refs=[memoir_source_ref(source)])
    inline = MemoirImageAsset(project_id=project_id, user_id=user_id, source_type="utterance_illustration",
        caption="秘密口述素材", alt_text="秘密口述素材", source_quote="秘密口述素材",
        source_signature="inline", storage_path="inline.png", mime_type="image/png", status="ready")
    test_db.add(inline)
    await test_db.flush()
    test_db.add(MemoirBookImagePlacement(project_id=project_id, image_asset_id=inline.id, chapter_id=chapter_id,
        placement_type="chapter_inline", source_utterance_ids_json=[source["utterance_id"]]))
    settings = get_settings().model_copy(update={"memoir_demo_project_ids": project_id})
    monkeypatch.setattr(book_shares, "get_settings", lambda: settings)
    await test_db.commit()
    monkeypatch.setattr(image_assets, "DATA_DIR", tmp_path)
    Image.new("RGB", (2000, 1500), "white").save(tmp_path / "cover.png")
    Image.new("RGB", (400, 300), "white").save(tmp_path / "inline.png")
    path = f"/api/mobile/memoir/projects/{project_id}/book-output/share"
    preview = await client.get(f"{path}/preview")
    assert preview.status_code == 200, preview.text
    assert preview.headers["cache-control"] == "no-store"
    assert (await client.get(path)).json()["active"] is False
    assert await test_db.get(MemoirBookShare, output.id) is None
    assert (await client.get("/api/mobile/memoir/book-shares/featured")).json() == []
    book = preview.json()["latest_version"]["manuscript"]
    assert book["profile"]["narrator_name"] == "外婆"
    assert book["profile"]["prepared_by"] == "家人整理"
    assert book["front_matter"][0]["content"] == "书中的前言"
    assert book["back_matter"]["afterword"]["content"] == "书中的后记"
    cover = book["render_settings"]["cover_image"]
    assert cover["storage_path"] == cover["source_quote"] == ""
    assert cover["image_url"].endswith(f"/{asset.id}/file?variant=reading")
    assert "秘密原话" not in preview.text
    token = (await client.put(path)).json()["token"]
    chapter_id = version.chapter_snapshot_json[0]["chapter_id"]
    chapter_preview = (await client.get(f"{path}/preview", params={"chapter_id": chapter_id})).json()
    assert chapter_preview["share_scope"] == "chapter"
    assert chapter_preview["latest_version"]["manuscript"]["render_settings"]["cover_image"] is None
    assert chapter_preview["latest_version"]["manuscript"]["front_matter"] == []
    chapter_token = (await client.put(path, params={"chapter_id": chapter_id})).json()["token"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as guest:
        assert (await guest.get(f"{path}/preview")).status_code == 401
        catalog = await guest.get("/api/mobile/memoir/book-shares/featured")
        assert catalog.headers["cache-control"] == "no-store"
        assert catalog.json()[0]["token"] == token
        public = (await guest.post("/api/mobile/memoir/book-shares/read", json={"token": catalog.json()[0]["token"]})).json()
        public_book = public["latest_version"]["manuscript"]
        assert public_book["chapters"][0]["content"] == book["chapters"][0]["content"]
        illustration = public_book["chapters"][0]["illustrations"][0]
        assert "秘密口述素材" not in str(public)
        assert illustration["caption"] == illustration["alt_text"] == "AI 意象插图"
        assert public_book["render_settings"]["cover_image"]["caption"] == "外婆家的院子"
        assert illustration["image_asset_id"] == inline.id
        assert illustration["storage_path"] == illustration["source_quote"] == ""
        assert (await guest.get(illustration["image_url"])).status_code == 200
        assert public_book["profile"] == book["profile"]
        manuscript.back_matter.afterword.enabled = False
        version.manuscript_json = manuscript.model_dump(mode="json")
        await test_db.commit()
        disabled = await guest.post("/api/mobile/memoir/book-shares/read", json={"token": token})
        assert "书中的后记" not in disabled.text
        media_url = public_book["render_settings"]["cover_image"]["image_url"]
        image = await guest.get(media_url)
        assert image.status_code == 200, image.text
        assert image.headers["cache-control"] == "no-store"
        from io import BytesIO
        with Image.open(BytesIO(image.content)) as displayed:
            assert displayed.format == "JPEG"
            assert max(displayed.size) <= 960
        assert (await guest.get(media_url.replace(token, chapter_token))).status_code == 404
        placement.enabled = False
        await test_db.commit()
        assert (await guest.get(media_url)).status_code == 404
        placement.enabled = True
        await test_db.commit()
        await client.delete(path)
        assert (await guest.get(media_url)).status_code == 404
        assert (await guest.get(illustration["image_url"])).status_code == 404
        assert (await guest.get("/api/mobile/memoir/book-shares/featured")).json() == []


async def test_featured_books_keep_order_and_only_list_configured_active_shares(client, test_db, monkeypatch):
    projects = []
    for title in ("我的奶奶—李桂芳", "余仲尤的往事", "未上首页的书"):
        project_id = (await client.post("/api/mobile/memoir/projects", json={"title": title})).json()["id"]
        await _shareable_output(test_db, project_id, title)
        share = await client.put(f"/api/mobile/memoir/projects/{project_id}/book-output/share")
        assert share.status_code == 200
        projects.append(project_id)
    settings = get_settings().model_copy(update={"memoir_demo_project_ids": f"{projects[0]},missing,{projects[1]},{projects[0]}"})
    monkeypatch.setattr(book_shares, "get_settings", lambda: settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as guest:
        listed = (await guest.get("/api/mobile/memoir/book-shares/featured")).json()
        assert [book["id"] for book in listed] == projects[:2]
        assert all(book["book_chapter_count"] == 1 for book in listed)
        await client.delete(f"/api/mobile/memoir/projects/{projects[0]}/book-output/share")
        assert [book["id"] for book in (await guest.get("/api/mobile/memoir/book-shares/featured")).json()] == [projects[1]]
