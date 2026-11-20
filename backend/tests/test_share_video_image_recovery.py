import asyncio
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from threading import Event, get_ident
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import delete, select, text

from app.database import async_session_maker
from app.models.ai_credits import AICreditTransaction
from app.models.ai_usage import AIUsageRecord
from app.models.memoir import MemoirImageAsset, MemoirMaterialShareVideo, MemoirShareVideoImageReceipt
from app.models.projects import Project
from app.models.users import User
from app.services import account_deletion
from app.services.ai_usage_records import AIUsageContext
from app.services.memoir import project_deletion, share_video_background_orchestration as orchestration
from app.services.memoir import share_video_backgrounds as backgrounds, share_video_jobs as jobs
from tests.memoir_helpers import create_test_voice_transcript


STORY = "我们去县城读书。后来又回到老房子。"


async def create_image_job(client, db, *, project_id=None):
    created = await create_test_voice_transcript(client, db, text=STORY, project_id=project_id)
    response = await client.post(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/share-video/jobs",
        json={"start_char": 0, "end_char": len(STORY), "background_source": "ai", "background_mode": "single"},
    )
    assert response.status_code == 201
    job_id = response.json()["id"]
    assert await jobs.claim_share_video_job(db, job_id, lease_seconds=60, max_attempts=3)
    return job_id, created["session"]["project_id"]


@pytest.fixture
async def image_job(client, test_db, tmp_path, monkeypatch):
    settings = backgrounds.get_settings().model_copy(update={"volc_ark_api_key": "test", "memoir_share_video_ai_background_enabled": True})
    monkeypatch.setattr(backgrounds, "get_settings", lambda: settings)
    monkeypatch.setattr(jobs, "get_settings", lambda: settings)
    job_id, project_id = await create_image_job(client, test_db)
    monkeypatch.setattr(project_deletion, "DATA_DIR", tmp_path)
    monkeypatch.setattr(backgrounds, "MEMOIR_SHARE_VIDEO_BACKGROUND_DIR", tmp_path / "memoir/share-video-backgrounds")
    buffer = BytesIO()
    Image.new("RGB", (16, 24)).save(buffer, "PNG")
    generate = AsyncMock(return_value=backgrounds.VolcArkGeneratedImage(buffer.getvalue(), "image/png", settings.volc_ark_image_model, {}))
    monkeypatch.setattr(backgrounds.VolcArkImageGenerationService, "generate", generate)
    return job_id, project_id, generate


async def prepare(job_id, *, after_progress=None):
    async with async_session_maker() as db:
        job = await db.get(MemoirMaterialShareVideo, job_id)
        metadata = orchestration.ShareVideoJobMetadata.from_json(job.metadata_json)

        async def persist(current):
            job.metadata_json = current.to_json()
            await db.commit()
            if after_progress is not None:
                after_progress(current)

        return await orchestration.prepare_share_video_background(
            db, job=job, metadata=metadata, persist_progress=persist,
            ai_background_enabled=True, volc_ark_api_key="test",
            usage_context=AIUsageContext(str(uuid4()), "memoir.share_video_generation", job.user_id, job.project_id),
        )


@pytest.mark.parametrize("interruption", ["disk", "billing", "progress"])
async def test_interrupted_image_returns_original_and_debits_once(image_job, test_db, monkeypatch, interruption):
    job_id, project_id, generate = image_job
    debit = select(AICreditTransaction.amount_rmb).join(
        AIUsageRecord, AIUsageRecord.id == AICreditTransaction.usage_record_id,
    ).where(AIUsageRecord.operation == "memoir.share_video_generation")

    with monkeypatch.context() as patch:
        if interruption == "disk":
            def full_disk(*args):
                raise OSError("disk full")
            patch.setattr(Path, "hardlink_to", full_disk)
        else:
            module, name = ((backgrounds, "record_ai_usage") if interruption == "billing" else
                            (orchestration, "get_or_generate_share_video_background"))
            original = getattr(module, name)

            async def stop_after(*args, **kwargs):
                await original(*args, **kwargs)
                raise asyncio.CancelledError
            patch.setattr(module, name, stop_after)
        with pytest.raises((OSError, asyncio.CancelledError)):
            await prepare(job_id)
    charge = Decimal("-0.18")
    assert (await test_db.scalars(debit)).all() == ([charge] if interruption == "progress" else [])
    project = await test_db.get(Project, project_id)
    project.metadata_json = {**project.metadata_json, "memoir_profile": {"narrator_gender": "female"}}
    job = await test_db.get(MemoirMaterialShareVideo, job_id, populate_existing=True)
    original_path = job.metadata_json["single_image_request"]["storage_path"]
    job.lease_token = str(uuid4())
    await test_db.commit()

    # Two deliveries may both try to settle an already generated frame.
    original = orchestration.recover_share_video_background
    async def duplicate_delivery(key, slot, **kwargs):
        first, second = await asyncio.gather(original(key, slot, **kwargs), original(key, slot, **kwargs))
        assert first.path == second.path
        return first
    monkeypatch.setattr(orchestration, "recover_share_video_background", duplicate_delivery)
    recovered = await prepare(job_id)
    assert recovered.image_path == backgrounds.share_video_background_file_path(original_path)
    assert recovered.image_path.read_bytes() == generate.return_value.content
    assert (await test_db.scalars(debit)).all() == [charge]
    assert generate.await_count == 1
    receipt = await test_db.get(MemoirShareVideoImageReceipt, (job_id, "single"))
    assert receipt.pending_content is None


async def use_two_shots(db, job_id, *, people=()):
    job = await db.get(MemoirMaterialShareVideo, job_id, populate_existing=True)
    split = job.selected_text.index("。") + 1
    job.metadata_json = {**job.metadata_json, "background_mode": "smart_multi", "background_scene_plan": {
        "version": orchestration.SHARE_VIDEO_SCENE_PLAN_VERSION,
        "scenes": [{"index": i, "start_char": start, "end_char": end, "start_ms": i * 3000, "end_ms": (i + 1) * 3000,
                    "characters": list(people), "clear_faces": list(people) if i else []}
                   for i, (start, end) in enumerate([(0, split), (split, len(job.selected_text))])],
    }}
    await db.commit()
    return job


@pytest.mark.parametrize("retained_job", [False, True])
async def test_cancelled_reference_cannot_reappear_after_project_deletion(image_job, client, test_db, tmp_path, monkeypatch, retained_job):
    job_id, project_id, _ = image_job
    assert (await client.post("/api/mobile/memoir/projects", json={"title": "保留的回忆录"})).is_success
    await use_two_shots(test_db, job_id)
    loop = asyncio.get_running_loop()
    loop_thread = get_ident()
    deleted, finished = Event(), Event()
    original = backgrounds.ShareVideoImageReference.save
    def cancel_during_publication(reference, path):
        loop.call_soon_threadsafe(worker.cancel)
        try:
            if get_ident() != loop_thread:
                # A detached writer can outlive the DB session and the entire project.
                assert deleted.wait(10)
            original(reference, path)
        finally:
            finished.set()
    monkeypatch.setattr(backgrounds.ShareVideoImageReference, "save", cancel_during_publication)
    worker = asyncio.create_task(prepare(job_id))
    try:
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(worker, 10)
        if not retained_job:
            test_db.expunge_all()
        assert (await client.delete(f"/api/mobile/memoir/projects/{project_id}")).status_code == 204
    finally:
        deleted.set()
    assert await asyncio.to_thread(finished.wait, 5)
    assert not (tmp_path / f"memoir/share-video-backgrounds/references/jobs/{job_id}.jpg").exists()
    assert not (await test_db.scalars(select(MemoirShareVideoImageReceipt.slot).where(
        MemoirShareVideoImageReceipt.job_id == job_id,
    ))).all()


async def test_cast_sheet_and_each_shot_have_separate_debits_and_shared_cache_is_free(image_job, test_db):
    job_id, _, generate = image_job
    job = await use_two_shots(test_db, job_id, people=["我", "父亲"])
    first = await prepare(job_id)
    assert len(first.segments) == 2
    assert (await prepare(job_id)).segments == first.segments
    receipts = (await test_db.scalars(select(MemoirShareVideoImageReceipt))).all()
    assert {receipt.slot for receipt in receipts} == {"cast", "scene-0", "scene-1"}
    assert all(receipt.pending_content is None for receipt in receipts)
    # A different slot with the same prompt can use the existing shared image.
    key = backgrounds.ShareVideoBackgroundCacheKey.model_validate(receipts[0].cache_key)
    slot = backgrounds.ShareVideoImageSlot(jobs.JobLease(job.id, job.lease_token), "single",
                                          AIUsageContext(str(uuid4()), "memoir.share_video_generation", job.user_id, job.project_id))
    assert (await backgrounds.recover_share_video_background(key.for_slot(slot), slot)).cache_hit
    assert generate.await_count == 3
    assert (await test_db.scalars(select(AICreditTransaction.amount_rmb))).all() == [Decimal("-0.18")] * 3



@pytest.mark.parametrize("legacy_pending", [False, True])
async def test_recovering_first_job_preserves_second_jobs_original_image(image_job, client, test_db, monkeypatch, legacy_pending):
    first_id, _, generate = image_job
    second_project = await client.post("/api/mobile/memoir/projects", json={"title": "另一份回忆录"})
    second_id, second_project_id = await create_image_job(client, test_db, project_id=second_project.json()["id"])
    first_image = generate.return_value

    with monkeypatch.context() as patch:
        def disk_full(*args, **kwargs):
            raise backgrounds.ShareVideoBackgroundGenerationError("disk full")
        patch.setattr(backgrounds, "_write_background", disk_full)
        with pytest.raises(OSError, match="尚未写入缓存"):
            await prepare(first_id)

    if legacy_pending:
        first_job = await test_db.get(MemoirMaterialShareVideo, first_id, populate_existing=True)
        receipt = await test_db.get(MemoirShareVideoImageReceipt, (first_id, "single"))
        old_key = {key: value for key, value in receipt.cache_key.items() if key != "cache_lookup_path"}
        old_key["storage_path"] = receipt.cache_key["cache_lookup_path"]
        first_job.metadata_json = {**first_job.metadata_json, "single_image_request": old_key}
        receipt.cache_key = old_key
        await test_db.commit()

    buffer = BytesIO()
    Image.new("RGB", (16, 24), "red").save(buffer, "PNG")
    second_image = backgrounds.VolcArkGeneratedImage(buffer.getvalue(), "image/png", first_image.model, {})
    generate.return_value = second_image
    second = await prepare(second_id)
    third_project = await client.post("/api/mobile/memoir/projects", json={"title": "复用配图的回忆录"})
    third_id, _ = await create_image_job(client, test_db, project_id=third_project.json()["id"])
    third = await prepare(third_id)
    first = await prepare(first_id)
    assert second.image_path.read_bytes() == second_image.content
    assert first.image_path.read_bytes() == first_image.content
    assert len({first.image_path, second.image_path, third.image_path}) == 3
    assert third.image_path.read_bytes() == second_image.content
    saved = dict((await test_db.execute(select(MemoirImageAsset.share_video_job_id, MemoirImageAsset.storage_path))).all())
    assert backgrounds.share_video_background_file_path(saved[second_id]) == second.image_path
    assert backgrounds.share_video_background_file_path(saved[first_id]) == first.image_path
    assert backgrounds.share_video_background_file_path(saved[third_id]) == third.image_path
    assert generate.await_count == 2
    assert (await test_db.scalars(select(AICreditTransaction.amount_rmb))).all() == [Decimal("-0.18")] * 2
    assert (await client.delete(f"/api/mobile/memoir/projects/{second_project_id}")).status_code == 204
    assert not second.image_path.exists()
    assert third.image_path.read_bytes() == second_image.content
    assert first.image_path.read_bytes() == first_image.content


@pytest.mark.parametrize("has_receipt", [False, True])
async def test_resume_keeps_legacy_registered_image_path(image_job, test_db, has_receipt):
    job_id, _, generate = image_job
    job = await test_db.get(MemoirMaterialShareVideo, job_id, populate_existing=True)
    key = backgrounds.prepare_share_video_image_request(job.selected_text, "watercolor").key
    path = backgrounds.share_video_background_file_path(key.storage_path)
    path.parent.mkdir(parents=True)
    path.write_bytes(generate.return_value.content)
    job.metadata_json = {
        **job.metadata_json, "background_status": "ready", "background_storage_path": key.storage_path,
        "background_model": key.model, "background_prompt_hash": key.prompt_hash,
        "background_prompt_version": key.template_version,
    }
    if has_receipt:
        job.metadata_json = {**job.metadata_json, "single_image_request": key.model_dump()}
        test_db.add(MemoirShareVideoImageReceipt(
            job_id=job_id, slot="single", model=key.model, cache_key=key.model_dump(),
        ))
    await test_db.commit()

    resumed = await prepare(job_id)
    assert resumed.image_path == path
    assert generate.await_count == 0
    assert not (await test_db.scalars(select(AICreditTransaction.amount_rmb))).all()


async def test_account_deletion_racing_first_scene_registration(image_job, test_db, tmp_path, monkeypatch):
    job_id, _, _ = image_job
    job = await use_two_shots(test_db, job_id)
    user_id = job.user_id
    monkeypatch.setattr(account_deletion, "DATA_DIR", tmp_path)
    registering, proceed = asyncio.Event(), asyncio.Event()
    original_register = orchestration.register_share_video_background_asset

    async def pause_registration(db, **kwargs):
        registering.set()
        await proceed.wait()
        return await original_register(db, **kwargs)

    monkeypatch.setattr(orchestration, "register_share_video_background_asset", pause_registration)

    class FirstSceneCommitted(Exception):
        pass

    def stop_after_first_scene(metadata):
        if metadata.background_generated_count == 1:
            raise FirstSceneCommitted

    async def delete_account():
        async with async_session_maker() as db:
            await db.execute(text("SET LOCAL deadlock_timeout = '100ms'"))
            deletion_pid.set_result(await db.scalar(text("SELECT pg_backend_pid()")))
            await account_deletion.delete_account(db, await db.get(User, user_id))

    deletion_pid = asyncio.get_running_loop().create_future()
    worker = asyncio.create_task(prepare(job_id, after_progress=stop_after_first_scene))
    deleting = None
    try:
        await asyncio.wait_for(registering.wait(), 5)
        deleting = asyncio.create_task(delete_account())
        pid = await asyncio.wait_for(deletion_pid, 5)
        async with asyncio.timeout(5):
            async with async_session_maker() as observer:
                while not await observer.scalar(text("SELECT pg_blocking_pids(:pid)"), {"pid": pid}):
                    await asyncio.sleep(0.01)
        proceed.set()
        with pytest.raises(FirstSceneCommitted):
            await asyncio.wait_for(worker, 5)
        await asyncio.wait_for(deleting, 5)
    finally:
        for task in (worker, deleting):
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    assert await test_db.scalar(select(User.id).where(User.id == user_id)) is None
    assert not list(tmp_path.rglob("*.jpg"))


async def test_failed_settlement_cannot_feed_shared_cache(image_job, client, test_db, monkeypatch):
    first_id, first_project_id, generate = image_job
    other = await client.post("/api/mobile/memoir/projects", json={"title": "另一份回忆录"})
    second_id, _ = await create_image_job(client, test_db, project_id=other.json()["id"])
    first_image = generate.return_value
    record_usage = backgrounds.record_ai_usage

    async def abort_debit(**kwargs):
        await record_usage(**kwargs)
        raise RuntimeError("billing rolled back")

    with monkeypatch.context() as patch:
        patch.setattr(backgrounds, "record_ai_usage", abort_debit)
        with pytest.raises(RuntimeError, match="billing rolled back"):
            await prepare(first_id)
    first_receipt = await test_db.get(MemoirShareVideoImageReceipt, (first_id, "single"))
    first_path = backgrounds.ShareVideoBackgroundCacheKey.model_validate(first_receipt.cache_key).asset(
        cache_hit=False, model=first_image.model,
    ).path
    assert first_path.read_bytes() == first_image.content
    assert first_receipt.pending_content == first_image.content
    assert not (await test_db.scalars(select(AICreditTransaction.amount_rmb))).all()

    buffer = BytesIO()
    Image.new("RGB", (16, 24), "green").save(buffer, "PNG")
    generate.return_value = backgrounds.VolcArkGeneratedImage(buffer.getvalue(), "image/png", first_image.model, {})
    second = await prepare(second_id)
    assert second.image_path.read_bytes() == buffer.getvalue()
    assert generate.await_count == 2
    assert (await test_db.scalars(select(AICreditTransaction.amount_rmb))).all() == [Decimal("-0.18")]
    assert (await client.delete(f"/api/mobile/memoir/projects/{first_project_id}")).status_code == 204
    assert not first_path.exists()
    assert second.image_path.read_bytes() == buffer.getvalue()


@pytest.mark.parametrize("checkpoint_format", ["1710", "current", "legacy_receipt"])
async def test_scoped_request_does_not_keep_legacy_cache_file_alive(image_job, client, test_db, checkpoint_format):
    legacy_id, legacy_project_id, generate = image_job
    other = await client.post("/api/mobile/memoir/projects", json={"title": "另一份回忆录"})
    scoped_id, _ = await create_image_job(client, test_db, project_id=other.json()["id"])
    legacy = await test_db.get(MemoirMaterialShareVideo, legacy_id, populate_existing=True)
    scoped = await test_db.get(MemoirMaterialShareVideo, scoped_id, populate_existing=True)
    key = backgrounds.prepare_share_video_image_request(legacy.selected_text, "watercolor").key
    canonical = backgrounds.share_video_background_file_path(key.storage_path)
    scope = backgrounds.ShareVideoImageSlot(jobs.JobLease(scoped.id, scoped.lease_token), "single",
                                           AIUsageContext(str(uuid4()), "memoir.share_video_generation", scoped.user_id, scoped.project_id))
    own = key.for_slot(scope).asset(cache_hit=False, model=key.model)
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_bytes(generate.return_value.content)
    own.path.parent.mkdir(parents=True)
    own.path.write_bytes(b"independent image bytes")
    old_checkpoint = {
        "storage_path": key.storage_path, "style_id": key.style_id, "model": key.model,
        "prompt_hash": key.prompt_hash, "template_version": key.template_version,
    }
    legacy.metadata_json = {**legacy.metadata_json, "background_storage_path": key.storage_path}
    checkpoint = key.for_slot(scope).model_dump()
    if checkpoint_format == "1710":
        checkpoint = {**old_checkpoint, "image_storage_path": own.storage_path}
    scoped.metadata_json = {**scoped.metadata_json, "single_image_request": checkpoint}
    if checkpoint_format == "legacy_receipt":
        test_db.add(MemoirShareVideoImageReceipt(
            job_id=scoped_id, slot="single", model=key.model, cache_key=old_checkpoint,
        ))
    await test_db.commit()

    assert (await client.delete(f"/api/mobile/memoir/projects/{legacy_project_id}")).status_code == 204
    assert canonical.exists() == (checkpoint_format == "legacy_receipt")
    assert own.path.read_bytes() == b"independent image bytes"


@pytest.mark.parametrize("old_asset", [False, True])
async def test_cache_reuses_confirmed_library_image_after_video_deletion(image_job, client, test_db, old_asset):
    first_id, first_project_id, generate = image_job
    first = await prepare(first_id)
    first_job = await test_db.get(MemoirMaterialShareVideo, first_id, populate_existing=True)
    if old_asset:
        asset = await test_db.scalar(select(MemoirImageAsset).where(MemoirImageAsset.share_video_job_id == first_id))
        asset.metadata_json = {
            name: value for name, value in asset.metadata_json.items()
            if name != "share_video_image_cache_lookup_path"
        }
        receipt = await test_db.get(MemoirShareVideoImageReceipt, (first_id, "single"))
        old_key = {name: value for name, value in receipt.cache_key.items() if name != "cache_lookup_path"}
        old_key["storage_path"] = receipt.cache_key["cache_lookup_path"]
        old_key["image_storage_path"] = receipt.cache_key["storage_path"]
        receipt.cache_key = old_key
        first_job.metadata_json = {**first_job.metadata_json, "single_image_request": old_key}
    first_job.status = "failed"
    first_job.lease_token = None
    await test_db.commit()
    await jobs.delete_share_video_job(test_db, first_id, first_job.user_id)
    assert await test_db.scalar(select(MemoirShareVideoImageReceipt.job_id).where(
        MemoirShareVideoImageReceipt.job_id == first_id,
    )) is None
    assert first.image_path.is_file()

    other = await client.post("/api/mobile/memoir/projects", json={"title": "复用素材的回忆录"})
    second_id, _ = await create_image_job(client, test_db, project_id=other.json()["id"])
    second = await prepare(second_id)
    assert second.image_path != first.image_path
    assert generate.await_count == 1
    assert (await test_db.scalars(select(AICreditTransaction.amount_rmb))).all() == [Decimal("-0.18")]
    assert (await client.delete(f"/api/mobile/memoir/projects/{first_project_id}")).status_code == 204
    assert not first.image_path.exists()
    assert second.image_path.read_bytes() == generate.return_value.content


@pytest.mark.parametrize("interruption", ["after_link", "before_link", "older_worker"])
async def test_interrupted_free_copy_keeps_confirmed_evidence(
    image_job, client, test_db, monkeypatch, interruption,
):
    source_id, source_project, generate = image_job
    first = await prepare(source_id)
    project = await client.post("/api/mobile/memoir/projects", json={"title": "复用已付费图"})
    assert project.is_success
    copy_id, _ = await create_image_job(client, test_db, project_id=project.json()["id"])
    hardlink = Path.hardlink_to

    def interrupt_link(path, source):
        assert path.parent.name == f"{copy_id}-single"
        if interruption != "before_link":
            hardlink(path, source)
        raise asyncio.CancelledError

    with monkeypatch.context() as patch:
        patch.setattr(Path, "hardlink_to", interrupt_link)
        with pytest.raises(asyncio.CancelledError):
            await prepare(copy_id)

    source_receipt = await test_db.get(MemoirShareVideoImageReceipt, (source_id, "single"))
    receipt = await test_db.get(MemoirShareVideoImageReceipt, (copy_id, "single"))
    assert receipt.cache_key["pending_reuse"]["file_path"] == source_receipt.cache_key["storage_path"]

    if interruption == "older_worker":
        # Earlier versions left the link without first committing a receipt.
        await test_db.execute(delete(MemoirShareVideoImageReceipt).where(
            MemoirShareVideoImageReceipt.job_id == copy_id,
        ))
        await test_db.commit()
        assert (await prepare(copy_id)).image_path.samefile(first.image_path)
    assert (await client.delete(f"/api/mobile/memoir/projects/{source_project}")).status_code == 204
    assert not first.image_path.exists()

    copied = await prepare(copy_id)
    assert copied.image_path.read_bytes() == generate.return_value.content
    third_project = await client.post("/api/mobile/memoir/projects", json={"title": "继续复用副本"})
    assert third_project.is_success
    third_id, _ = await create_image_job(client, test_db, project_id=third_project.json()["id"])
    assert (await prepare(third_id)).image_path.samefile(copied.image_path)
    expected = 2 if interruption == "before_link" else 1
    assert generate.await_count == expected
    assert (await test_db.scalars(select(AICreditTransaction.amount_rmb))).all() == [Decimal("-0.18")] * expected


async def test_detaching_video_does_not_confirm_unproven_library_image(image_job, client, test_db):
    source_id, _, generate = image_job
    first = await prepare(source_id)
    job = await test_db.get(MemoirMaterialShareVideo, source_id, populate_existing=True)
    asset = await test_db.scalar(select(MemoirImageAsset).where(MemoirImageAsset.share_video_job_id == source_id))
    asset.metadata_json = {
        name: value for name, value in asset.metadata_json.items()
        if name != "share_video_image_cache_lookup_path"
    }
    # This is the shape an older unverified free copy could have.
    await test_db.execute(delete(MemoirShareVideoImageReceipt).where(
        MemoirShareVideoImageReceipt.job_id == source_id,
    ))
    job.status = "failed"
    job.lease_token = None
    await test_db.commit()
    await jobs.delete_share_video_job(test_db, source_id, job.user_id)
    assert first.image_path.is_file()

    project = await client.post("/api/mobile/memoir/projects", json={"title": "同文不同已确认状态"})
    assert project.is_success
    other_id, _ = await create_image_job(client, test_db, project_id=project.json()["id"])
    await prepare(other_id)
    assert generate.await_count == 2


async def test_free_reuse_tries_another_copy_if_reserved_source_disappears(image_job, client, test_db, monkeypatch):
    source_id, _, generate = image_job
    await prepare(source_id)
    duplicate_id, _ = await create_image_job(client, test_db)
    await prepare(duplicate_id)
    next_id, _ = await create_image_job(client, test_db)

    hardlink = Path.hardlink_to
    vanished = []

    def unlink_first_source(destination, source):
        if not vanished:
            source.unlink()
            vanished.append(source)
        return hardlink(destination, source)

    monkeypatch.setattr(Path, "hardlink_to", unlink_first_source)
    reused = await prepare(next_id)
    assert reused.image_path.read_bytes() == generate.return_value.content
    assert vanished and not vanished[0].exists()
    assert generate.await_count == 1
    assert (await test_db.scalars(select(AICreditTransaction.amount_rmb))).all() == [Decimal("-0.18")]
