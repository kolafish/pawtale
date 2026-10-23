from copy import deepcopy
from decimal import Decimal
from io import BytesIO

import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirCreatedBy,
    MemoirImageAssetSourceType,
    MemoirImageAssetStatus,
    MemoirOutputStatus,
    MemoirShareVideoJobStatus,
)
from app.models.memoir import (
    MemoirBookChapterDraftVersion,
    MemoirBookImagePlacement,
    MemoirBookOutput,
    MemoirBookOutputVersion,
    MemoirEditingJob,
    MemoirImageAsset,
    MemoirMaterialShareVideo,
)
from app.services.memoir import book_output_export, book_outputs, image_assets
from app.services.memoir import material_image_generation
from app.services.memoir.book_output_manuscript import manuscript_from_snapshot
from app.services.memoir.book_output_snapshots import chapter_snapshot
from app.services.memoir.image_assets import (
    RegisteredShareVideoBackground,
    register_share_video_background_asset,
)
from app.services.memoir.narrative.book_output_assembly import assemble_book_output
from app.services.memoir.share_video_backgrounds import ShareVideoBackgroundAsset
from app.services.memoir.share_video_backgrounds import (
    VolcArkGeneratedImage,
    build_share_video_background_prompt,
)
from app.services.memoir.editing_job_handlers import register_memoir_editing_job_handlers
from tests.memoir_helpers import (
    create_test_book_plan_with_chapters,
    create_test_voice_transcript,
    ensure_mobile_memoir_project,
    memoir_source_ref,
    run_test_editing_job,
)


def _png_bytes(size: tuple[int, int] = (1280, 720)) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, (232, 218, 196)).save(output, format="PNG")
    return output.getvalue()


def test_material_image_prompt_is_not_a_subtitle_background_prompt():
    selected_text = "小时候院子里有一棵葡萄树，夏天一家人都坐在下面乘凉。"

    style_id, illustration_prompt, _ = material_image_generation.build_material_image_prompt(
        selected_text,
        None,
    )
    video_prompt = build_share_video_background_prompt(selected_text, None).prompt

    assert style_id == "watercolor"
    assert selected_text in illustration_prompt
    assert selected_text in video_prompt
    assert "适合放进家庭回忆录" in illustration_prompt
    assert "构图完整，主体明确" in illustration_prompt
    assert "不冒充真实历史照片" in illustration_prompt
    assert "画面中部和右侧" not in illustration_prompt
    assert "不需要为字幕预留中部或右侧空白" not in illustration_prompt
    assert "下方约三分之一" in video_prompt
    assert "不要因为这是回忆录内容" not in illustration_prompt
    assert "不要因为这是回忆录内容" not in video_prompt


async def test_material_image_generation_creates_unplaced_toolbox_asset(
    client,
    test_db,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(material_image_generation, "DATA_DIR", tmp_path)
    monkeypatch.setattr(image_assets, "DATA_DIR", tmp_path)
    generated_prompts: list[str] = []

    async def fake_generate(_service, prompt: str) -> VolcArkGeneratedImage:
        generated_prompts.append(prompt)
        return VolcArkGeneratedImage(
            content=_png_bytes(),
            mime_type="image/png",
            model="doubao-seedream-5-0-260128",
            usage={"generated_images": 1},
        )

    monkeypatch.setattr(
        material_image_generation.VolcArkImageGenerationService,
        "generate",
        fake_generate,
    )
    register_memoir_editing_job_handlers()
    project_id = await ensure_mobile_memoir_project(client)
    text_value = "小时候院子里有一棵葡萄树，夏天一家人都坐在下面乘凉。"
    created = await create_test_voice_transcript(
        client,
        test_db,
        project_id=project_id,
        text=text_value,
    )

    response = await client.post(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/image-generation/jobs",
        json={
            "start_char": 0,
            "end_char": len(text_value),
            "style_id": "old_album",
        },
    )

    assert response.status_code == 202
    pending = response.json()
    assert pending["status"] == "pending"
    assert pending["asset_status"] == "pending"
    assert pending["selected_text"] == text_value
    assert pending["start_char"] == 0
    assert pending["end_char"] == len(text_value)

    context_response = await client.get(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/share-video/context"
    )
    assert context_response.status_code == 200
    assert context_response.json()["latest_image_job_id"] == pending["job_id"]

    list_response = await client.get(
        f"/api/mobile/memoir/projects/{project_id}/image-assets"
    )
    assert list_response.status_code == 200
    queued_asset = list_response.json()["items"][0]
    assert queued_asset["id"] == pending["asset_id"]
    assert queued_asset["source_type"] == "utterance_illustration"
    assert queued_asset["status"] == "pending"
    assert queued_asset["include_in_book"] is False

    assert await run_test_editing_job(test_db, pending["job_id"]) is True

    completed_response = await client.get(
        f"/api/mobile/memoir/image-generation/jobs/{pending['job_id']}"
    )
    assert completed_response.status_code == 200
    completed = completed_response.json()
    assert completed["status"] == "succeeded"
    assert completed["asset_status"] == "ready"
    assert completed["stage"] == "ready"
    assert generated_prompts
    assert "保留大面积干净留白" not in generated_prompts[0]

    ready_list = (
        await client.get(f"/api/mobile/memoir/projects/{project_id}/image-assets")
    ).json()
    ready_asset = ready_list["items"][0]
    assert ready_asset["status"] == "ready"
    assert ready_asset["include_in_book"] is False
    assert ready_asset["provider"] == "volc_ark"
    assert ready_asset["model"] == "doubao-seedream-5-0-260128"
    assert ready_asset["width"] == 1280
    assert ready_asset["height"] == 720
    assert ready_asset["metadata_json"]["generation_stage"] == "ready"
    assert ready_asset["metadata_json"]["disclosure"] == image_assets.AI_GENERATED_IMAGE_DISCLOSURE
    assert ready_asset["metadata_json"]["generation_estimated_cost"]["amount_rmb"] == 0.22

    summary = (await client.get("/api/billing/ai-credit-summary")).json()
    assert Decimal(summary["used_rmb"]) == Decimal("0.22")
    [day] = (await client.get("/api/billing/ai-usage")).json()["days"]
    assert [(item["purpose"], Decimal(item["cost_rmb"])) for item in day["purposes"]] == [
        ("图片生成", Decimal("0.22")),
    ]

    file_response = await client.get(ready_asset["file_url"])
    assert file_response.status_code == 200
    assert file_response.headers["content-type"].startswith("image/png")


async def test_material_image_generation_does_not_repeat_unknown_provider_call(
    client,
    test_db,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(material_image_generation, "DATA_DIR", tmp_path)
    provider_calls = 0

    async def unexpected_generate(_service, _prompt: str) -> VolcArkGeneratedImage:
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError("stale image jobs must not repeat an unknown provider call")

    monkeypatch.setattr(
        material_image_generation.VolcArkImageGenerationService,
        "generate",
        unexpected_generate,
    )
    register_memoir_editing_job_handlers()
    project_id = await ensure_mobile_memoir_project(client)
    text_value = "母亲常在窗边缝补衣服。"
    created = await create_test_voice_transcript(
        client,
        test_db,
        project_id=project_id,
        text=text_value,
    )
    response = await client.post(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/image-generation/jobs",
        json={"start_char": 0, "end_char": len(text_value), "style_id": "ink_paper"},
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    job = await test_db.get(MemoirEditingJob, job_id)
    assert job is not None
    job.attempts = 1
    await test_db.commit()

    assert await run_test_editing_job(test_db, job_id) is False
    assert provider_calls == 0

    failed_response = await client.get(
        f"/api/mobile/memoir/image-generation/jobs/{job_id}"
    )
    assert failed_response.status_code == 200
    failed = failed_response.json()
    assert failed["status"] == "failed"
    assert failed["asset_status"] == "failed"
    assert "避免重复付费" in failed["error_message"]

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


async def _register_another_video_image(test_db, tmp_path, *, asset_id: str, scene_index: int) -> str:
    source = await test_db.get(MemoirImageAsset, asset_id)
    assert source is not None and source.share_video_job_id is not None
    job = await test_db.get(MemoirMaterialShareVideo, source.share_video_job_id)
    assert job is not None
    storage_path = f"memoir/share-video-backgrounds/old_album/fake/asset-{scene_index}.png"
    image_path = tmp_path / storage_path
    image_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1280, 720), (232, 218, 196)).save(image_path)
    asset = await register_share_video_background_asset(
        test_db,
        job=job,
        background=ShareVideoBackgroundAsset(
            path=image_path,
            storage_path=storage_path,
            style_id="old_album",
            prompt_hash=f"prompt-hash-{scene_index}",
            prompt_template_version="memoir-share-video-background-v1",
            provider="fake",
            model="fake-image",
            cache_hit=False,
        ),
        context=RegisteredShareVideoBackground(
            source_quote="小时候院子里有一棵葡萄树。",
            scene_index=scene_index,
            scene_title=f"院子里的葡萄树 {scene_index}",
            evidence_quote="小时候院子里有一棵葡萄树。",
        ),
    )
    await test_db.commit()
    return asset.id


@pytest.mark.db_independent
@pytest.mark.parametrize('operation', ['create', 'update'])
async def test_image_write_does_not_invert_existing_plan_replacement_locks(
    client, test_db, tmp_path, monkeypatch, operation,
):
    import asyncio
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.domain.memoir.types import MemoirBookPlanStatus
    from app.schemas.memoir_images import MemoirBookImagePlacementCreateRequest, MemoirBookImagePlacementUpdateRequest
    from app.services.memoir import book_plan_write_workflow as plans

    user, pid, _, aid = await _seed_image_asset(client, test_db, tmp_path, monkeypatch)
    placement_id = None
    if operation == 'update':
        placed = await client.post(f'/api/mobile/memoir/projects/{pid}/image-assets/{aid}/placements',
            json={'placement_type': 'chapter_inline'})
        assert placed.status_code == 201
        placement_id = placed.json()['placements'][0]['id']
    await test_db.commit()
    paused, resume = asyncio.Event(), asyncio.Event()
    real_lock = plans.lock_project_chapters
    async with AsyncSession(test_db.bind, expire_on_commit=False) as replacement, AsyncSession(test_db.bind, expire_on_commit=False) as image:
        async def pause_replacement(db, **kwargs):
            if db is replacement:
                paused.set()
                await resume.wait()
            await real_lock(db, **kwargs)
        monkeypatch.setattr(plans, 'lock_project_chapters', pause_replacement)
        task = asyncio.create_task(plans._replace_plan(replacement, project_id=pid, title='保留当前目录', chapters=[],
            status=MemoirBookPlanStatus.CONFIRMED, created_by=MemoirCreatedBy.AI, model=None,
            prompt_snapshot='', only_if_missing=True))
        try:
            await asyncio.wait_for(paused.wait(), timeout=3)
            if operation == 'create':
                call = image_assets.create_image_placement(image, user_id=user['id'], project_id=pid, asset_id=aid,
                    req=MemoirBookImagePlacementCreateRequest(placement_type='chapter_inline'))
            else:
                call = image_assets.update_image_placement(image, user_id=user['id'], project_id=pid, asset_id=aid,
                    placement_id=placement_id, req=MemoirBookImagePlacementUpdateRequest(display_size='half_width'))
            result = await asyncio.wait_for(call, timeout=3)
            assert result.include_in_book
            resume.set()
            assert (await asyncio.wait_for(task, timeout=3)).outcome == 'reused'
        finally:
            resume.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await image.rollback()
            await replacement.rollback()


@pytest.mark.parametrize('split_sources', [False, True])
async def test_source_relocation_updates_preview_and_export_images_preserving_manual_body(
    client, test_db, tmp_path, monkeypatch, split_sources,
):
    from app.models.memoir import MemoirBookPlanChapter, MemoirSection, MemoirSectionMaterial
    from app.schemas.memoir_sections import MemoirSectionMaterialPlacementRequest
    from app.services.memoir import sections
    from app.services.memoir.book_output_versions import append_book_output_version, load_latest_book_output_version

    user, pid, cid, aid = await _seed_image_asset(client, test_db, tmp_path, monkeypatch)
    asset = await test_db.get(MemoirImageAsset, aid)
    original_section = await test_db.scalar(select(MemoirSection).where(MemoirSection.chapter_id == cid))
    extra = await create_test_voice_transcript(client, test_db, '后来葡萄架下还种了花。', project_id=pid)
    test_db.add(MemoirSectionMaterial(project_id=pid, section_id=original_section.id,
        utterance_id=extra['utterance_id'], sort_order=1))
    target_chapter = await test_db.get(MemoirBookPlanChapter, cid)
    if split_sources:
        target_chapter = MemoirBookPlanChapter(project_id=pid, plan_id=target_chapter.plan_id, title='另一章', sort_order=1)
        test_db.add(target_chapter)
        await test_db.flush()
    target = MemoirSection(project_id=pid, chapter_id=target_chapter.id, title='新的素材位置',
        sort_order=0 if split_sources else 1)
    test_db.add(target)
    await test_db.commit()
    source_ids = [asset.source_utterance_id, extra['utterance_id']]
    placed = await client.post(f'/api/mobile/memoir/projects/{pid}/image-assets/{aid}/placements', json={
        'placement_type': 'chapter_inline', 'source_utterance_ids': source_ids,
    })
    assert placed.status_code == 201, placed.text
    output = await test_db.scalar(select(MemoirBookOutput).where(MemoirBookOutput.project_id == pid))
    # Keep an older loaded relationship to catch stale snapshot reads after a new manual version.
    await test_db.refresh(output, attribute_names=['versions'])
    latest = await load_latest_book_output_version(test_db, output_id=output.id)
    manuscript = deepcopy(latest.manuscript_json)
    manuscript['chapters'][0]['content'] = '用户手写的全书正文，必须逐字保留。'
    manual = await append_book_output_version(test_db, output=output, content='用户手写的全书正文，必须逐字保留。',
        chapter_snapshot_json=latest.chapter_snapshot_json, manuscript_json=manuscript, created_by=MemoirCreatedBy.USER)
    await test_db.commit()
    for source_id in source_ids[:1] if split_sources else source_ids:
        await sections.place_material(test_db, user_id=user['id'], project_id=pid, utterance_id=source_id,
            req=MemoirSectionMaterialPlacementRequest(target_section_id=target.id))

    # Match the fresh identity map used by the next HTTP request in production.
    test_db.expunge_all()
    preview = (await client.get(f'/api/mobile/memoir/projects/{pid}/book-output')).json()['latest_version']
    assert preview['content'] == manual.content
    assert preview['manuscript']['chapters'][0]['content'] == manuscript['chapters'][0]['content']
    illustrations = preview['manuscript']['chapters'][0]['illustrations']
    if split_sources:
        assert illustrations == []
    else:
        assert [(item['image_asset_id'], item['section_id']) for item in illustrations] == [(aid, target.id)]
    prepared = await book_outputs.prepare_book_output_export(test_db, user_id=user['id'], project_id=pid,
        export_format=book_output_export.MemoirBookOutputExportFormat.PDF)
    assert prepared.content == manual.content
    assert [item.image_asset_id for item in prepared.manuscript.chapters[0].illustrations] == ([] if split_sources else [aid])
    version_count = await test_db.scalar(select(MemoirBookOutputVersion.version)
        .where(MemoirBookOutputVersion.output_id == output.id).order_by(MemoirBookOutputVersion.version.desc()).limit(1))
    await image_assets.reconcile_image_placements(test_db, project_id=pid)
    assert (await load_latest_book_output_version(test_db, output_id=output.id)).version == version_count


async def test_image_anchor_migration_keeps_single_sources_and_snapshots_legacy_chapter_sources(
    client, test_db, tmp_path, monkeypatch,
):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text
    from app.models.memoir import MemoirSection, MemoirSectionMaterial

    user, pid, chapter_id, asset_id = await _seed_image_asset(client, test_db, tmp_path, monkeypatch)
    asset = await test_db.get(MemoirImageAsset, asset_id)
    extra = await create_test_voice_transcript(client, test_db, '后来葡萄架下还种了花。', project_id=pid)
    section = await test_db.scalar(select(MemoirSection).where(MemoirSection.chapter_id == chapter_id))
    test_db.add(MemoirSectionMaterial(project_id=pid, section_id=section.id,
        utterance_id=extra['utterance_id'], sort_order=1))
    uploaded = MemoirImageAsset(project_id=pid, user_id=user['id'], source_type='manual_upload', source_signature='legacy-photo')
    test_db.add(uploaded)
    await test_db.flush()
    placements = [MemoirBookImagePlacement(project_id=pid, image_asset_id=aid,
        placement_type=kind, chapter_id=chapter_id if kind != 'cover' else None)
        for aid, kind in [(asset_id, 'chapter_inline'), (uploaded.id, 'chapter_opening'), (uploaded.id, 'cover')]]
    test_db.add_all(placements)
    await test_db.flush()
    expected = {
        placements[0].id: [asset.source_utterance_id],
        placements[1].id: sorted([asset.source_utterance_id, extra['utterance_id']]),
        placements[2].id: [],
    }
    savepoint = await test_db.begin_nested()
    try:
        await test_db.execute(text('ALTER TABLE memoir_book_image_placements DROP COLUMN source_utterance_ids_json'))
        spec = importlib.util.spec_from_file_location('image_anchor_migration', Path(__file__).resolve().parents[1]
            / 'alembic/versions/b2d4f6a8c103_image_placement_material_anchors.py')
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        def upgrade(connection):
            migration.op = Operations(MigrationContext.configure(connection))
            migration.upgrade()
        await (await test_db.connection()).run_sync(upgrade)
        rows = (await test_db.execute(text('SELECT id, source_utterance_ids_json FROM memoir_book_image_placements WHERE project_id=:pid'),
            {'pid': pid})).all()
        assert dict(rows) == expected
    finally:
        await savepoint.rollback()


async def test_automatic_book_selects_one_video_image_and_updates_unchanged_manuscript(
    client,
    test_db,
    tmp_path,
    monkeypatch,
):
    _, project_id, chapter_id, first_asset_id = await _seed_image_asset(
        client, test_db, tmp_path, monkeypatch,
    )
    first_asset = await test_db.get(MemoirImageAsset, first_asset_id)
    assert first_asset is not None and first_asset.share_video_job_id is not None
    job = await test_db.get(MemoirMaterialShareVideo, first_asset.share_video_job_id)
    assert job is not None
    job.status = MemoirShareVideoJobStatus.PENDING.value
    await test_db.commit()

    baseline = await assemble_book_output(test_db, project_id=project_id, automatic=True)
    await test_db.commit()
    baseline_version = await test_db.get(MemoirBookOutputVersion, baseline.output_version_id)
    assert baseline_version is not None
    assert baseline_version.manuscript_json["chapters"][0]["illustrations"] == []

    job.status = MemoirShareVideoJobStatus.SUCCEEDED.value
    await test_db.commit()
    second_asset_id = await _register_another_video_image(
        test_db, tmp_path, asset_id=first_asset_id, scene_index=1,
    )
    assembled = await assemble_book_output(test_db, project_id=project_id, automatic=True)
    await test_db.commit()
    version = await test_db.get(MemoirBookOutputVersion, assembled.output_version_id)
    assert version is not None
    assert assembled.output_version_id != baseline.output_version_id
    assert version.content == baseline_version.content
    assert version.chapter_snapshot_json == baseline_version.chapter_snapshot_json
    assert version.manuscript_json["chapters"][0]["chapter_id"] == chapter_id
    illustrations = version.manuscript_json["chapters"][0]["illustrations"]
    assert len(illustrations) == 1
    assert illustrations[0]["image_asset_id"] in {first_asset_id, second_asset_id}
    assert illustrations[0]["section_id"] == version.manuscript_json["chapters"][0]["sections"][0]["section_id"]

    repeated = await assemble_book_output(test_db, project_id=project_id, automatic=True)
    assert repeated.output_version_id == assembled.output_version_id
    placements = (await test_db.scalars(select(MemoirBookImagePlacement).where(
        MemoirBookImagePlacement.project_id == project_id,
        MemoirBookImagePlacement.enabled.is_(True),
    ))).all()
    assert len(placements) == 1


async def test_automatic_book_adds_video_image_without_rewriting_protected_book(
    client,
    test_db,
    tmp_path,
    monkeypatch,
):
    _, project_id, _, asset_id = await _seed_image_asset(
        client, test_db, tmp_path, monkeypatch,
    )
    original = await test_db.scalar(
        select(MemoirBookOutputVersion).where(
            MemoirBookOutputVersion.project_id == project_id,
        )
    )
    assert original is not None
    original.created_by = MemoirCreatedBy.USER.value
    await test_db.commit()

    assembled = await assemble_book_output(test_db, project_id=project_id, automatic=True)
    await test_db.commit()
    version = await test_db.get(MemoirBookOutputVersion, assembled.output_version_id)
    assert version is not None
    assert assembled.preserved_manual is True
    assert version.id != original.id
    assert version.content == original.content
    assert version.chapter_snapshot_json == original.chapter_snapshot_json
    assert [item["image_asset_id"] for item in version.manuscript_json["chapters"][0]["illustrations"]] == [asset_id]


@pytest.mark.parametrize("operation", ["create", "update", "delete"])
async def test_manual_image_placement_stops_later_automatic_selection(
    client,
    test_db,
    tmp_path,
    monkeypatch,
    operation,
):
    _, project_id, _, first_asset_id = await _seed_image_asset(
        client, test_db, tmp_path, monkeypatch,
    )
    first = await assemble_book_output(test_db, project_id=project_id, automatic=True)
    await test_db.commit()
    first_version = await test_db.get(MemoirBookOutputVersion, first.output_version_id)
    assert first_version is not None
    assert [item["image_asset_id"] for item in first_version.manuscript_json["chapters"][0]["illustrations"]] == [first_asset_id]
    auto_placement = await test_db.scalar(select(MemoirBookImagePlacement).where(
        MemoirBookImagePlacement.project_id == project_id,
        MemoirBookImagePlacement.image_asset_id == first_asset_id,
        MemoirBookImagePlacement.enabled.is_(True),
    ))
    assert auto_placement is not None
    second_asset_id = await _register_another_video_image(
        test_db, tmp_path, asset_id=first_asset_id, scene_index=2,
    )

    if operation == "create":
        response = await client.post(
            f"/api/mobile/memoir/projects/{project_id}/image-assets/{second_asset_id}/placements",
            json={"placement_type": "chapter_inline", "display_size": "full_width"},
        )
        expected_asset_ids = [second_asset_id]
    elif operation == "update":
        response = await client.patch(
            f"/api/mobile/memoir/projects/{project_id}/image-assets/{first_asset_id}/placements/{auto_placement.id}",
            json={"enabled": False},
        )
        expected_asset_ids = []
    else:
        response = await client.delete(
            f"/api/mobile/memoir/projects/{project_id}/image-assets/{first_asset_id}/placements/{auto_placement.id}",
        )
        expected_asset_ids = []
    assert response.status_code in {200, 201}

    assembled = await assemble_book_output(test_db, project_id=project_id, automatic=True)
    await test_db.commit()
    version = await test_db.get(MemoirBookOutputVersion, assembled.output_version_id)
    assert version is not None
    assert [item["image_asset_id"] for item in version.manuscript_json["chapters"][0]["illustrations"]] == expected_asset_ids
    enabled = (await test_db.scalars(select(MemoirBookImagePlacement.image_asset_id).where(
        MemoirBookImagePlacement.project_id == project_id,
        MemoirBookImagePlacement.enabled.is_(True),
    ))).all()
    assert enabled == expected_asset_ids


async def test_automatic_book_skips_failed_video_and_custom_uploaded_background(
    client,
    test_db,
    tmp_path,
    monkeypatch,
):
    user, project_id, _, asset_id = await _seed_image_asset(
        client, test_db, tmp_path, monkeypatch,
    )
    source = await test_db.get(MemoirImageAsset, asset_id)
    assert source is not None and source.share_video_job_id is not None
    job = await test_db.get(MemoirMaterialShareVideo, source.share_video_job_id)
    assert job is not None
    job.status = MemoirShareVideoJobStatus.FAILED.value
    await test_db.commit()

    failed_video = await assemble_book_output(test_db, project_id=project_id, automatic=True)
    await test_db.commit()
    failed_version = await test_db.get(MemoirBookOutputVersion, failed_video.output_version_id)
    assert failed_version is not None
    assert failed_version.manuscript_json["chapters"][0]["illustrations"] == []

    source.status = MemoirImageAssetStatus.DELETED.value
    job.status = MemoirShareVideoJobStatus.SUCCEEDED.value
    test_db.add(MemoirImageAsset(
        project_id=project_id,
        user_id=user["id"],
        status="ready",
        source_type=MemoirImageAssetSourceType.SHARE_VIDEO_BACKGROUND.value,
        source_signature="test-custom-upload-background",
        storage_path=source.storage_path,
        provider="user_upload",
        source_utterance_id=source.source_utterance_id,
        share_video_job_id=job.id,
        metadata_json={"share_video_background_source": "custom_upload"},
    ))
    await test_db.commit()

    assembled = await assemble_book_output(test_db, project_id=project_id, automatic=True)
    await test_db.commit()
    version = await test_db.get(MemoirBookOutputVersion, assembled.output_version_id)
    assert version is not None
    assert version.manuscript_json["chapters"][0]["illustrations"] == []
    assert (await test_db.scalars(select(MemoirBookImagePlacement).where(
        MemoirBookImagePlacement.project_id == project_id,
    ))).all() == []


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
    assert placed["placements"][0]["source_utterance_ids"] == [placed["source_utterance_id"]]

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

    prepared = await book_outputs.prepare_book_output_export(
        test_db,
        user_id=user["id"],
        project_id=project_id,
        export_format=book_output_export.MemoirBookOutputExportFormat.PDF,
    )
    exported = await book_outputs.export_book_output_file(prepared)
    assert exported.media_type == "application/pdf"
    assert exported.content.startswith(b"%PDF")






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
    assert thumbnail_response.headers["content-type"].startswith("image/jpeg")
    assert thumbnail_response.content != source_bytes
    with Image.open(BytesIO(thumbnail_response.content)) as thumbnail:
        assert thumbnail.format == "JPEG"
        assert thumbnail.size == (960, 540)
    thumbnail_files = list(source_path.parent.glob("asset.memoir-thumb-v1.jpg"))
    assert len(thumbnail_files) == 1
    assert source_path.read_bytes() == source_bytes

    asset = await test_db.get(MemoirImageAsset, asset_id)
    assert asset is not None
    await test_db.refresh(asset)
    assert asset.thumbnail_storage_path.endswith("asset.memoir-thumb-v1.jpg")

    cached_response = await client.get(
        f"/api/mobile/memoir/projects/{project_id}/image-assets/{asset_id}/file?variant=thumbnail"
    )
    assert cached_response.status_code == 200
    assert cached_response.content == thumbnail_response.content


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


async def test_uploaded_cover_is_independent_of_auto_body_images_and_templates(client, test_db, tmp_path, monkeypatch):
    _, project_id, _, body_asset_id = await _seed_image_asset(client, test_db, tmp_path, monkeypatch)
    await assemble_book_output(test_db, project_id=project_id, automatic=True)
    await test_db.commit()
    latest = await test_db.scalar(select(MemoirBookOutputVersion).where(
        MemoirBookOutputVersion.project_id == project_id,
    ).order_by(MemoirBookOutputVersion.version.desc()))
    # A pre-v4 preset could carry unused custom controls. Adding a cover must
    # preserve its effective typography when upgrading the snapshot.
    legacy = deepcopy(latest.manuscript_json)
    legacy["schema_version"] = 3
    legacy["render_settings"]["custom_layout"] = {"font_scale": "large", "line_spacing": "relaxed", "page_margin": "wide"}
    latest.manuscript_json = legacy
    flag_modified(latest, "manuscript_json")
    await test_db.commit()
    base = (await client.get(f"/api/mobile/memoir/projects/{project_id}/book-output")).json()["latest_version"]["manuscript"]
    uploaded = await client.post(f"/api/mobile/memoir/projects/{project_id}/image-assets", files={"file": ("photo.png", _png_bytes((480, 640)), "image/png")})
    assert uploaded.status_code == 201
    asset = uploaded.json()
    assert (asset["width"], asset["height"]) == (480, 640)
    assert asset["source_type"] == "manual_upload"
    duplicate = await client.post(f"/api/mobile/memoir/projects/{project_id}/image-assets", files={"file": ("photo.png", _png_bytes((480, 640)), "image/png")})
    assert duplicate.json()["id"] == asset["id"]
    selected = await client.post(f"/api/mobile/memoir/projects/{project_id}/image-assets/{asset['id']}/placements", json={"placement_type": "cover"})
    assert selected.status_code == 201
    cover_placement = next(p for p in selected.json()["placements"] if p["placement_type"] == "cover")
    assert cover_placement["display_size"] == "cover_frame"
    placement_path = f"/api/mobile/memoir/projects/{project_id}/image-assets/{asset['id']}/placements/{cover_placement['id']}"
    full_bleed = await client.patch(placement_path, json={"display_size": "cover_full_bleed"})
    assert full_bleed.status_code == 200
    mismatched = await client.patch(placement_path, json={"display_size": "half_width"})
    assert mismatched.status_code == 400
    framed = (await client.get(f"/api/mobile/memoir/projects/{project_id}/book-output")).json()["latest_version"]["manuscript"]
    assert framed["render_settings"]["cover_image"]["display_size"] == "cover_full_bleed"
    body_placement = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/image-assets/{asset['id']}/placements",
        json={"placement_type": "appendix", "display_size": "cover_frame"},
    )
    assert body_placement.status_code == 400
    after_upload = (await client.get(f"/api/mobile/memoir/projects/{project_id}/book-output")).json()["latest_version"]["manuscript"]
    assert after_upload["schema_version"] == 5
    assert after_upload["render_settings"]["custom_layout"]["font_scale"] == "standard"
    replacement = (await client.post(f"/api/mobile/memoir/projects/{project_id}/image-assets", files={"file": ("new.png", _png_bytes((600, 800)), "image/png")})).json()
    placed = await client.post(f"/api/mobile/memoir/projects/{project_id}/image-assets/{replacement['id']}/placements", json={"placement_type": "cover"})
    assert placed.status_code == 201
    reenabled = await client.patch(f"/api/mobile/memoir/projects/{project_id}/image-assets/{asset['id']}/placements/{cover_placement['id']}", json={"enabled": True})
    assert reenabled.status_code == 200
    covers = (await test_db.scalars(select(MemoirBookImagePlacement).where(
        MemoirBookImagePlacement.project_id == project_id,
        MemoirBookImagePlacement.placement_type == "cover",
        MemoirBookImagePlacement.enabled.is_(True),
    ))).all()
    assert [cover.image_asset_id for cover in covers] == [asset["id"]]
    for template in ("heritage_classic", "warm_journal", "modern_minimal", "custom"):
        changed = await client.patch(f"/api/mobile/memoir/projects/{project_id}/book-output", json={"manuscript_template": template})
        assert changed.status_code == 200
        manuscript = changed.json()["latest_version"]["manuscript"]
        assert manuscript["render_settings"]["cover_image"]["image_asset_id"] == asset["id"]
        assert manuscript["chapters"] == base["chapters"]
        assert manuscript["render_settings"]["back_cover_quote"] == base["render_settings"]["back_cover_quote"]
    assembled = await assemble_book_output(test_db, project_id=project_id, automatic=True)
    await test_db.commit()
    version = await test_db.get(MemoirBookOutputVersion, assembled.output_version_id)
    assert version.manuscript_json["render_settings"]["cover_image"]["image_asset_id"] == asset["id"]
    assert [image["image_asset_id"] for image in version.manuscript_json["chapters"][0]["illustrations"]] == [body_asset_id]
    restored = await client.delete(f"/api/mobile/memoir/projects/{project_id}/image-assets/{asset['id']}/placements/{cover_placement['id']}")
    assert restored.status_code == 200
    current = (await client.get(f"/api/mobile/memoir/projects/{project_id}/book-output")).json()["latest_version"]["manuscript"]
    assert current["render_settings"]["cover_image"] is None
    assert current["chapters"] == base["chapters"]
    from app.models.projects import Project
    project = await test_db.get(Project, project_id)
    await test_db.refresh(project)
    assert project.memoir_book_image_selection_manual is False
    invalid = await client.post(f"/api/mobile/memoir/projects/{project_id}/image-assets", files={"file": ("photo.png", b"invalid image", "image/png")})
    assert invalid.status_code == 400
