from copy import deepcopy

from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import select

from app.main import app
from app.models.memoir import (
    MemoirBookChapterDraftVersion, MemoirBookImagePlacement, MemoirBookOutput, MemoirBookPlanChapter,
    MemoirImageAsset, MemoirSection, MemoirSectionMaterial, MemoirSectionShare,
)
from app.services.memoir import image_assets
from tests.memoir_helpers import create_test_book_plan, create_test_chapter_draft, create_test_voice_transcript, ensure_mobile_memoir_project, memoir_source_ref
from tests.test_memoir_share_likes import _family_member


async def saved_stories(client, db, *, with_sources=False):
    project_id = await ensure_mobile_memoir_project(client)
    sources = []
    if with_sources:
        sources = [await create_test_voice_transcript(client, db, project_id=project_id, text=text)
                   for text in ["院子里的回忆", "另一则私密回忆"]]
    plan, chapter = await create_test_book_plan(db, project_id=project_id,
        source_refs=[memoir_source_ref(source) for source in sources])
    draft = await create_test_chapter_draft(db, project_id=project_id, chapter_id=chapter.id, content="院子里的正文")
    first = await db.scalar(select(MemoirSection).where(MemoirSection.chapter_id == chapter.id))
    other = MemoirSection(project_id=project_id, chapter_id=chapter.id, title="不公开的小节", sort_order=1)
    db.add(other)
    await db.flush()
    version = await db.scalar(select(MemoirBookChapterDraftVersion).where(MemoirBookChapterDraftVersion.draft_id == draft.id))
    snapshots = deepcopy(version.section_snapshot_json)
    snapshots[0].update(title="院子里的回忆", used_material_ids=[sources[0]["utterance_id"]] if sources else [])
    snapshots.append({"section_id": other.id, "title": other.title, "content": "另一则私密正文", "used_material_ids": [sources[1]["utterance_id"]] if sources else []})
    version.section_snapshot_json = snapshots
    version.content = "院子里的正文\n\n另一则私密正文"
    if sources:
        material = await db.scalar(select(MemoirSectionMaterial).where(MemoirSectionMaterial.utterance_id == sources[1]["utterance_id"]))
        material.section_id, material.sort_order = other.id, 0
    await db.commit()
    return project_id, chapter, draft, version, first, other, sources


async def test_story_share_follows_saved_section_and_keeps_likes_across_renewal(client, test_db):
    project_id, chapter, draft, version, section, other, _ = await saved_stories(client, test_db)
    path = f"/api/mobile/memoir/projects/{project_id}/book-output/share"
    params = {"chapter_id": chapter.id, "section_id": section.id}
    preview = await client.get(f"{path}/preview", params=params)
    assert preview.status_code == 200, preview.text
    assert await test_db.get(MemoirSectionShare, section.id) is None
    assert await test_db.scalar(select(MemoirBookOutput).where(MemoirBookOutput.project_id == project_id)) is None
    assert (await client.get(path, params=params)).json()["active"] is False
    token = (await client.put(path, params=params)).json()["token"]
    assert token.startswith(f"s1.{section.id}.")
    assert (await client.put(path, params=params)).json()["token"] == token
    aunt = await _family_member(test_db, "三姑")
    async with aunt, AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as guest:
        async def read(link=token):
            return await guest.post("/api/mobile/memoir/book-shares/read", json={"token": link})
        assert (await guest.put(path, params=params)).status_code == 401
        body = await read()
        assert body.status_code == 200, body.text
        assert body.json()["share_scope"] == "story"
        assert body.json()["title"] == "院子里的回忆"
        assert body.json()["latest_version"]["content"] == "院子里的正文"
        assert "另一则私密正文" not in body.text and other.title not in body.text
        assert (await read(token.replace("s1.", "c1.", 1))).status_code == 404
        target = {"reading_token": token}
        assert (await aunt.put("/api/mobile/memoir/share-likes", json={**target, "liked": True})).json()["count"] == 1
        activity = (await client.get(f"/api/mobile/memoir/projects/{project_id}/share-likes")).json()["items"]
        assert activity[0]["target_kind"] == "story"
        # Reordering and another section being merged do not change the shared identity.
        section.sort_order = 2
        other.status = "archived"
        await test_db.commit()
        assert (await read()).json()["latest_version"]["content"] == "院子里的正文"
        snapshots = deepcopy(version.section_snapshot_json)
        snapshots[0]["content"] = "院子里的新版正文"
        test_db.add(MemoirBookChapterDraftVersion(draft_id=draft.id, project_id=project_id, chapter_id=chapter.id,
            version=2, content="院子里的新版正文", section_snapshot_json=[snapshots[0]]))
        await test_db.commit()
        assert (await read()).json()["latest_version"]["content"] == "院子里的新版正文"
        await client.delete(path, params=params)
        assert (await read()).status_code == 404
        renewed = (await client.put(path, params=params)).json()["token"]
        assert renewed != token
        assert (await read()).status_code == 404
        assert (await aunt.post("/api/mobile/memoir/share-likes/read", json={"reading_token": renewed})).json()["liked"] is True
        section.status = "archived"
        await test_db.commit()
        assert (await read(renewed)).status_code == 404
        await test_db.delete(section)
        await test_db.commit()
        assert await test_db.scalar(select(MemoirSectionShare).where(MemoirSectionShare.section_id == section.id)) is None
        assert (await read(renewed)).status_code == 404


async def test_story_images_and_material_removal_are_limited_to_the_saved_section(client, test_db, tmp_path, monkeypatch):
    project_id, chapter, _, _, section, other, sources = await saved_stories(client, test_db, with_sources=True)
    monkeypatch.setattr(image_assets, "DATA_DIR", tmp_path)
    assets = []
    for index, source in enumerate(sources):
        filename = f"story-{index}.png"
        Image.new("RGB", (16, 16), "white").save(tmp_path / filename)
        asset = MemoirImageAsset(project_id=project_id, user_id=source["user_id"], source_type="manual_upload",
            source_signature=filename, storage_path=filename, status="ready", mime_type="image/png")
        test_db.add(asset)
        await test_db.flush()
        test_db.add(MemoirBookImagePlacement(project_id=project_id, image_asset_id=asset.id, chapter_id=chapter.id,
            placement_type="chapter_inline", source_utterance_ids_json=[source["utterance_id"]]))
        assets.append(asset)
    await test_db.commit()
    path = f"/api/mobile/memoir/projects/{project_id}/book-output/share"
    token = (await client.put(path, params={"section_id": section.id})).json()["token"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as guest:
        body = await guest.post("/api/mobile/memoir/book-shares/read", json={"token": token})
        assert body.status_code == 200, body.text
        images = body.json()["latest_version"]["manuscript"]["chapters"][0]["illustrations"]
        assert [image["image_asset_id"] for image in images] == [assets[0].id]
        image_url = images[0]["image_url"]
        assert (await guest.get(image_url)).status_code == 200
        assert (await guest.get(image_url.replace(assets[0].id, assets[1].id))).status_code == 404
        # Withdrawn source data must not remain visible through the old saved version.
        material = await test_db.scalar(select(MemoirSectionMaterial).where(MemoirSectionMaterial.utterance_id == sources[0]["utterance_id"]))
        material.include_in_book = False
        await test_db.commit()
        assert (await guest.post("/api/mobile/memoir/book-shares/read", json={"token": token})).status_code == 409
        assert (await guest.get(image_url)).status_code == 409
    # A chapter selector cannot be used to smuggle a section from another scope.
    mismatch = await client.put(path, params={"chapter_id": other.id, "section_id": section.id})
    assert mismatch.status_code == 404


async def test_story_link_survives_merging_its_chapter(client, test_db):
    project_id, chapter, _, version, section, _, _ = await saved_stories(client, test_db)
    snapshot = deepcopy(version.section_snapshot_json[0])
    destination = MemoirBookPlanChapter(project_id=project_id, plan_id=chapter.plan_id, title="合并后的章节", sort_order=1)
    test_db.add(destination)
    await test_db.commit()
    token = (await client.put(f"/api/mobile/memoir/projects/{project_id}/book-output/share",
        params={"section_id": section.id})).json()["token"]
    merged = await client.post(f"/api/mobile/memoir/projects/{project_id}/book-plan/structure-commands", json={
        "type": "merge_chapters", "chapter_id": chapter.id, "target_chapter_id": destination.id,
    })
    assert merged.status_code == 200, merged.text
    assert await test_db.get(MemoirBookPlanChapter, chapter.id, populate_existing=True) is None
    assert await test_db.get(MemoirBookChapterDraftVersion, version.id, populate_existing=True) is None
    assert await test_db.get(MemoirSectionShare, section.id, populate_existing=True) is not None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as guest:
        assert (await guest.post("/api/mobile/memoir/book-shares/read", json={"token": token})).status_code == 409
        draft = await create_test_chapter_draft(test_db, project_id=project_id, chapter_id=destination.id, content="合章后保存的故事")
        saved = await test_db.scalar(select(MemoirBookChapterDraftVersion).where(MemoirBookChapterDraftVersion.draft_id == draft.id))
        snapshot["content"] = "合章后保存的故事"
        saved.section_snapshot_json = [snapshot]
        await test_db.commit()
        read = await guest.post("/api/mobile/memoir/book-shares/read", json={"token": token})
        assert read.status_code == 200, read.text
        assert read.json()["latest_version"]["content"] == "合章后保存的故事"
