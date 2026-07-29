from PIL import Image
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirCreatedBy,
    MemoirImageAssetSourceType,
    MemoirOutputStatus,
    MemoirShareVideoJobStatus,
)
from app.migrations.memoir_images import migrate_memoir_image_schema
from app.migrations.runtime import migrate_runtime_schema
from app.models.memoir import (
    MemoirBookChapterDraftVersion,
    MemoirBookImagePlacement,
    MemoirBookOutput,
    MemoirBookOutputVersion,
    MemoirImageAsset,
    MemoirMaterialShareVideo,
)
from app.services.memoir import book_output_export, book_outputs, image_assets
from app.services.memoir.book_output_manuscript import manuscript_from_snapshot
from app.services.memoir.book_output_snapshots import chapter_snapshot
from app.services.memoir.image_assets import (
    RegisteredShareVideoBackground,
    register_share_video_background_asset,
)
from app.services.memoir.share_video_backgrounds import ShareVideoBackgroundAsset
from tests.memoir_helpers import (
    create_test_book_plan_with_chapters,
    create_test_voice_transcript,
    ensure_mobile_memoir_project,
    memoir_source_ref,
)

async def _seed_image_asset(client, test_db, tmp_path, monkeypatch):
    monkeypatch.setattr(image_assets, "DATA_DIR", tmp_path)
    monkeypatch.setattr(book_output_export, "DATA_DIR", tmp_path)
    user = (await client.get("/api/auth/me")).json()
    project_id = await ensure_mobile_memoir_project(client)
    created = await create_test_voice_transcript(
        client,
        test_db,
        project_id=project_id,
        text="小时候院子里有一棵葡萄树，夏天一家人都坐在下面乘凉。",
    )
    plan, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["院子里的夏天"],
        source_refs_by_chapter=[[memoir_source_ref(created, "小时候院子里有一棵葡萄树。")]],
        draft_contents=["夏天的时候，院子里的葡萄树撑起一片阴凉，家人常坐在下面聊天。"],
        draft_statuses=[MemoirBookChapterDraftStatus.CONFIRMED],
    )
    chapter = chapters[0]
    draft_version = await test_db.scalar(
        select(MemoirBookChapterDraftVersion)
        .where(MemoirBookChapterDraftVersion.chapter_id == chapter.id)
        .order_by(MemoirBookChapterDraftVersion.version.desc())
    )
    assert draft_version is not None
    snapshot = [
        chapter_snapshot(
            chapter,
            draft_version,
            chapter_index=1,
            source_refs=[memoir_source_ref(created, "小时候院子里有一棵葡萄树。")],
        )
    ]
    title = "我的回忆录"
    content = f"{title}\n\n目录\n1. {chapter.title}\n\n第1章 {chapter.title}\n\n{draft_version.content}"
    output = MemoirBookOutput(
        project_id=project_id,
        plan_id=plan.id,
        title=title,
        status=MemoirOutputStatus.DRAFT.value,
        metadata_json={},
    )
    test_db.add(output)
    await test_db.flush()
    manuscript = manuscript_from_snapshot(
        title=title,
        content=content,
        snapshot=snapshot,
    )
    test_db.add(
        MemoirBookOutputVersion(
            output_id=output.id,
            project_id=project_id,
            plan_id=plan.id,
            version=1,
            content=content,
            chapter_snapshot_json=snapshot,
            manuscript_json=manuscript.model_dump(mode="json"),
            created_by=MemoirCreatedBy.SYSTEM.value,
        )
    )
    image_path = tmp_path / "memoir" / "share-video-backgrounds" / "old_album" / "fake" / "asset.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1280, 720), (232, 218, 196)).save(image_path)
    job = MemoirMaterialShareVideo(
        project_id=project_id,
        utterance_id=created["utterance_id"],
        audio_file_id=created["audio_file_id"],
        user_id=user["id"],
        status=MemoirShareVideoJobStatus.SUCCEEDED.value,
        text_source="edited",
        text_hash="text-hash",
        selected_text="小时候院子里有一棵葡萄树。",
        start_char=0,
        end_char=14,
        start_ms=0,
        end_ms=5000,
        metadata_json={},
    )
    test_db.add(job)
    await test_db.flush()
    asset = await register_share_video_background_asset(
        test_db,
        job=job,
        background=ShareVideoBackgroundAsset(
            path=image_path,
            storage_path="memoir/share-video-backgrounds/old_album/fake/asset.png",
            style_id="old_album",
            prompt_hash="prompt-hash",
            prompt_template_version="memoir-share-video-background-v1",
            provider="fake",
            model="fake-image",
            cache_hit=False,
        ),
        context=RegisteredShareVideoBackground(
            source_quote="小时候院子里有一棵葡萄树。",
            scene_title="院子里的葡萄树",
            evidence_quote="小时候院子里有一棵葡萄树。",
        ),
    )
    await test_db.commit()
    return user, project_id, chapters[0].id, asset.id


async def test_image_asset_api_places_image_near_source_and_syncs_latest_manuscript(
    client,
    test_db,
    tmp_path,
    monkeypatch,
):
    user, project_id, chapter_id, asset_id = await _seed_image_asset(client, test_db, tmp_path, monkeypatch)

    output = await test_db.scalar(
        select(MemoirBookOutput).where(MemoirBookOutput.project_id == project_id)
    )
    assert output is not None
    output.status = MemoirOutputStatus.ACCEPTED.value
    await test_db.commit()

    list_response = await client.get(f"/api/mobile/memoir/projects/{project_id}/image-assets")
    assert list_response.status_code == 200
    payload = list_response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["title"] == "院子里的葡萄树"
    assert payload["items"][0]["include_in_book"] is False

    place_response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/image-assets/{asset_id}/placements",
        json={
            "placement_type": "chapter_inline",
            "display_size": "full_width",
        },
    )
    assert place_response.status_code == 201
    placed = place_response.json()
    assert placed["include_in_book"] is True
    assert placed["placements"][0]["chapter_id"] == chapter_id
    assert placed["placements"][0]["placement_type"] == "chapter_inline"

    caption_response = await client.patch(
        f"/api/mobile/memoir/projects/{project_id}/image-assets/{asset_id}",
        json={"caption": "院子里的葡萄树"},
    )
    assert caption_response.status_code == 200

    output_response = await client.get(f"/api/mobile/memoir/projects/{project_id}/book-output")
    assert output_response.status_code == 200
    assert output_response.json()["status"] == MemoirOutputStatus.DRAFT.value
    manuscript = output_response.json()["latest_version"]["manuscript"]
    illustrations = manuscript["chapters"][0]["illustrations"]
    assert illustrations[0]["image_asset_id"] == asset_id
    assert illustrations[0]["caption"] == "院子里的葡萄树"
    assert illustrations[0]["storage_path"].endswith("asset.png")
    assert illustrations[0]["source_utterance_id"]
    assert illustrations[0]["section_id"] == manuscript["chapters"][0]["sections"][0]["section_id"]
    assert illustrations[0]["source_utterance_id"] in (
        manuscript["chapters"][0]["sections"][0]["source_ref_ids"]
    )

    exported = await book_outputs.export_book_output_file(
        test_db,
        user_id=user["id"],
        project_id=project_id,
        export_format=book_output_export.MemoirBookOutputExportFormat.PDF,
    )
    assert exported.media_type == "application/pdf"
    assert exported.content.startswith(b"%PDF")


async def test_image_migration_relocates_legacy_source_linked_appendix_and_resyncs_output(
    client,
    test_db,
    tmp_path,
    monkeypatch,
):
    _, project_id, chapter_id, asset_id = await _seed_image_asset(
        client,
        test_db,
        tmp_path,
        monkeypatch,
    )
    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/image-assets/{asset_id}/placements",
        json={
            "placement_type": "chapter_inline",
            "display_size": "full_width",
        },
    )
    assert response.status_code == 201
    placement_id = response.json()["placements"][0]["id"]

    await test_db.execute(
        text(
            """
            UPDATE memoir_book_image_placements
            SET placement_type = 'appendix', chapter_id = NULL
            WHERE id = :placement_id
            """
        ),
        {"placement_id": placement_id},
    )
    output_version = await test_db.scalar(
        select(MemoirBookOutputVersion)
        .where(MemoirBookOutputVersion.project_id == project_id)
        .order_by(MemoirBookOutputVersion.version.desc())
    )
    assert output_version is not None
    manuscript = dict(output_version.manuscript_json)
    chapters = [dict(chapter) for chapter in manuscript["chapters"]]
    legacy_illustration = dict(chapters[0]["illustrations"][0])
    legacy_illustration["placement"] = "appendix"
    legacy_illustration.pop("section_id", None)
    chapters[0]["illustrations"] = []
    manuscript["chapters"] = chapters
    manuscript["image_appendix"] = [legacy_illustration]
    manuscript["render_settings"] = {
        **manuscript["render_settings"],
        "photo_mode": "appendix",
    }
    output_version.manuscript_json = manuscript
    await test_db.commit()

    result = await migrate_memoir_image_schema(test_db.bind)
    assert result["relocated_source_linked_placements"] == 1
    changed_outputs = await image_assets.resync_all_latest_book_output_image_snapshots(
        test_db
    )
    await test_db.commit()

    placement = (
        await test_db.execute(
            text(
                """
                SELECT placement_type, chapter_id
                FROM memoir_book_image_placements
                WHERE id = :placement_id
                """
            ),
            {"placement_id": placement_id},
        )
    ).one()
    assert placement.placement_type == "chapter_inline"
    assert placement.chapter_id == chapter_id
    assert changed_outputs == 1
    await test_db.refresh(output_version)
    migrated_manuscript = output_version.manuscript_json
    assert migrated_manuscript["image_appendix"] == []
    migrated_illustration = migrated_manuscript["chapters"][0]["illustrations"][0]
    assert migrated_illustration["placement"] == "chapter_inline"
    assert migrated_illustration["section_id"]


async def test_image_migration_disables_redundant_source_linked_appendix(
    client,
    test_db,
    tmp_path,
    monkeypatch,
):
    _, project_id, chapter_id, asset_id = await _seed_image_asset(
        client,
        test_db,
        tmp_path,
        monkeypatch,
    )
    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/image-assets/{asset_id}/placements",
        json={
            "placement_type": "chapter_inline",
            "display_size": "full_width",
        },
    )
    assert response.status_code == 201
    legacy_appendix = MemoirBookImagePlacement(
        project_id=project_id,
        image_asset_id=asset_id,
        placement_type="appendix",
        chapter_id=None,
        sort_order=1,
        enabled=True,
    )
    test_db.add(legacy_appendix)
    await test_db.commit()

    result = await migrate_memoir_image_schema(test_db.bind)
    assert result["relocated_source_linked_placements"] == 0
    enabled_rows = (
        await test_db.execute(
            text(
                """
                SELECT placement_type, chapter_id
                FROM memoir_book_image_placements
                WHERE image_asset_id = :asset_id AND enabled = 1
                """
            ),
            {"asset_id": asset_id},
        )
    ).all()

    assert enabled_rows == [("chapter_inline", chapter_id)]
    chapter_images, appendix_images = await image_assets.manuscript_image_snapshots(
        test_db,
        project_id=project_id,
    )
    assert len(chapter_images[chapter_id]) == 1
    assert appendix_images == []


async def test_generated_image_download_is_branded_without_changing_clean_source(
    client,
    test_db,
    tmp_path,
    monkeypatch,
):
    _, project_id, _, asset_id = await _seed_image_asset(client, test_db, tmp_path, monkeypatch)
    source_path = tmp_path / "memoir" / "share-video-backgrounds" / "old_album" / "fake" / "asset.png"
    source_bytes = source_path.read_bytes()

    response = await client.get(f"/api/mobile/memoir/projects/{project_id}/image-assets/{asset_id}/file")
    assert response.status_code == 200
    assert response.content != source_bytes
    assert source_path.read_bytes() == source_bytes
    assert list(source_path.parent.glob("asset.memoir-brand-v2.png"))

    thumbnail_response = await client.get(
        f"/api/mobile/memoir/projects/{project_id}/image-assets/{asset_id}/file?variant=thumbnail"
    )
    assert thumbnail_response.status_code == 200
    assert thumbnail_response.content == source_bytes


def test_user_uploaded_image_file_is_never_branded(tmp_path, monkeypatch):
    monkeypatch.setattr(image_assets, "DATA_DIR", tmp_path)
    source_path = tmp_path / "custom.png"
    Image.new("RGB", (1280, 720), (232, 218, 196)).save(source_path)
    asset = MemoirImageAsset(
        source_type=MemoirImageAssetSourceType.SHARE_VIDEO_BACKGROUND.value,
        storage_path="custom.png",
        provider="user_upload",
        metadata_json={"share_video_background_source": "custom_upload"},
    )

    assert image_assets.image_asset_file_path(asset, branded=True) == source_path
    assert not list(tmp_path.glob("*.memoir-brand-v2.png"))


async def test_image_assets_are_project_owner_scoped(client, test_db, tmp_path, monkeypatch):
    _, project_id, _, asset_id = await _seed_image_asset(client, test_db, tmp_path, monkeypatch)
    await client.post("/api/auth/logout")
    other_response = await client.post(
        "/api/auth/register",
        json={
            "email": "image-assets-other@example.com",
            "password": "password123",
            "display_name": "其他用户",
        },
    )
    assert other_response.status_code == 201

    response = await client.get(f"/api/mobile/memoir/projects/{project_id}/image-assets")
    assert response.status_code == 404
    response = await client.patch(
        f"/api/mobile/memoir/projects/{project_id}/image-assets/{asset_id}",
        json={"caption": "不应该能改"},
    )
    assert response.status_code == 404


async def test_runtime_migration_creates_memoir_image_tables():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        result = await migrate_runtime_schema(engine)
        assert "memoir_images" in result
        async with engine.begin() as conn:
            rows = (
                await conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'memoir_%image%'")
                )
            ).scalars().all()
        assert "memoir_image_assets" in rows
        assert "memoir_book_image_placements" in rows
    finally:
        await engine.dispose()
