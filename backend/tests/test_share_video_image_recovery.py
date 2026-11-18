import asyncio
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from threading import Event, get_ident
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import select

from app.database import async_session_maker
from app.models.ai_credits import AICreditTransaction
from app.models.ai_usage import AIUsageRecord
from app.models.memoir import MemoirMaterialShareVideo, MemoirShareVideoImageReceipt
from app.models.projects import Project
from app.services.ai_usage_records import AIUsageContext
from app.services.memoir import project_deletion, share_video_background_orchestration as orchestration
from app.services.memoir import share_video_backgrounds as backgrounds, share_video_jobs as jobs
from tests.memoir_helpers import create_test_voice_transcript


@pytest.fixture
async def image_job(client, test_db, tmp_path, monkeypatch):
    text = "我们去县城读书。后来又回到老房子。"
    created = await create_test_voice_transcript(client, test_db, text=text)
    settings = backgrounds.get_settings().model_copy(update={"volc_ark_api_key": "test", "memoir_share_video_ai_background_enabled": True})
    monkeypatch.setattr(backgrounds, "get_settings", lambda: settings)
    monkeypatch.setattr(jobs, "get_settings", lambda: settings)
    response = await client.post(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/share-video/jobs",
        json={"start_char": 0, "end_char": len(text), "background_source": "ai", "background_mode": "single"},
    )
    assert response.status_code == 201
    job_id = response.json()["id"]
    lease = await jobs.claim_share_video_job(test_db, job_id, lease_seconds=60, max_attempts=3)
    assert lease
    monkeypatch.setattr(project_deletion, "DATA_DIR", tmp_path)
    monkeypatch.setattr(backgrounds, "MEMOIR_SHARE_VIDEO_BACKGROUND_DIR", tmp_path / "memoir/share-video-backgrounds")
    buffer = BytesIO()
    Image.new("RGB", (16, 24)).save(buffer, "PNG")
    generate = AsyncMock(return_value=backgrounds.VolcArkGeneratedImage(buffer.getvalue(), "image/png", settings.volc_ark_image_model, {}))
    monkeypatch.setattr(backgrounds.VolcArkImageGenerationService, "generate", generate)
    return job_id, created["session"]["project_id"], generate


async def prepare(job_id):
    async with async_session_maker() as db:
        job = await db.get(MemoirMaterialShareVideo, job_id)
        metadata = orchestration.ShareVideoJobMetadata.from_json(job.metadata_json)

        async def persist(current):
            job.metadata_json = current.to_json()
            await db.commit()

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
            patch.setattr(Path, "replace", full_disk)
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


@pytest.mark.parametrize("retained_job", [False, True])
async def test_cancelled_reference_cannot_reappear_after_project_deletion(image_job, client, test_db, tmp_path, monkeypatch, retained_job):
    job_id, project_id, _ = image_job
    assert (await client.post("/api/mobile/memoir/projects", json={"title": "保留的回忆录"})).is_success
    job = await test_db.get(MemoirMaterialShareVideo, job_id, populate_existing=True)
    split = job.selected_text.index("。") + 1
    job.metadata_json = {**job.metadata_json, "background_mode": "smart_multi", "background_scene_plan": {
        "version": orchestration.SHARE_VIDEO_SCENE_PLAN_VERSION,
        "scenes": [{"index": i, "start_char": start, "end_char": end, "start_ms": i * 3000, "end_ms": (i + 1) * 3000}
                   for i, (start, end) in enumerate([(0, split), (split, len(job.selected_text))])],
    }}
    await test_db.commit()
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
    job = await test_db.get(MemoirMaterialShareVideo, job_id, populate_existing=True)
    split = job.selected_text.index("。") + 1
    people = ["我", "父亲"]
    job.metadata_json = {**job.metadata_json, "background_mode": "smart_multi", "background_scene_plan": {
        "version": orchestration.SHARE_VIDEO_SCENE_PLAN_VERSION,
        "scenes": [{"index": i, "start_char": start, "end_char": end, "start_ms": i * 3000, "end_ms": (i + 1) * 3000,
                    "characters": people, "clear_faces": [] if i == 0 else people}
                   for i, (start, end) in enumerate([(0, split), (split, len(job.selected_text))])],
    }}
    await test_db.commit()
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
    assert (await backgrounds.recover_share_video_background(key, slot)).cache_hit
    assert generate.await_count == 3
    assert (await test_db.scalars(select(AICreditTransaction.amount_rmb))).all() == [Decimal("-0.18")] * 3
