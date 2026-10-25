from copy import deepcopy

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.main import app
from app.models.memoir import MemoirBookChapterDraftVersion, MemoirEditingJob
from app.services.memoir import book_shares
from tests.memoir_helpers import run_test_editing_job
from tests.test_memoir_book_audio_jobs import tts as tts
from tests.test_memoir_story_shares import saved_stories
from tests.test_memoir_book_outputs import _create_confirmed_book_plan, _seed_confirmed_book_output
from tests.memoir_helpers import ensure_mobile_memoir_project


@pytest.mark.parametrize("scope", ["story", "chapter"])
async def test_shared_audio_is_read_only_scoped_and_revocable(client, test_db, tts, scope):
    pid, chapter, draft, version, section, other, _ = await saved_stories(client, test_db)
    private = "另一则私密正文"
    path = f"/api/mobile/memoir/projects/{pid}/book-output/share"
    params = {"section_id": section.id} if scope == "story" else {"chapter_id": chapter.id}
    token = (await client.put(path, params=params)).json()["token"]
    jobs = list((await test_db.scalars(select(MemoirEditingJob).where(MemoirEditingJob.job_type == "generate_book_audio_chunk"))).all())
    assert jobs
    assert (private in "".join(job.payload_json["text"] for job in jobs)) is (scope == "chapter")
    assert not tts.calls
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as guest:
        query = {"token": token, "version_id": version.id}
        response = await guest.post("/api/mobile/memoir/book-shares/audio/plan", json=query)
        assert response.status_code == 200, response.text
        assert all(segment["job_id"] is None for segment in response.json()["segments"])
        for field, value in [("retry_failed", True), ("force_regenerate", True), ("voice", "another")]:
            assert (await guest.post("/api/mobile/memoir/book-shares/audio/plan", json={**query, field: value})).status_code == 422
        assert (await guest.post("/api/mobile/memoir/book-shares/audio/plan", json={**query, "chapter_id": other.id})).status_code == 404
        for job in jobs:
            assert await run_test_editing_job(test_db, job.id)
        response = await guest.post("/api/mobile/memoir/book-shares/audio/plan", json=query)
        body = response.json()
        assert body["ready_count"] == len(body["segments"])
        audio_id = body["segments"][0]["audio_file_id"]
        url = f"/api/mobile/memoir/book-shares/{token}/audio/{audio_id}?version_id={version.id}"
        assert (await guest.get(url)).status_code == 200
        assert (await guest.get(url.replace(audio_id, other.id))).status_code == 404
        count = await test_db.scalar(select(func.count()).select_from(MemoirEditingJob))
        for _ in range(3):
            assert (await guest.post("/api/mobile/memoir/book-shares/audio/plan", json=query)).status_code == 200
        assert await test_db.scalar(select(func.count()).select_from(MemoirEditingJob)) == count
        assert len(tts.calls) == len(jobs)
        snapshots = deepcopy(version.section_snapshot_json)
        snapshots[0]["content"] = "院子里更新后的正文"
        latest = MemoirBookChapterDraftVersion(draft_id=draft.id, project_id=pid, chapter_id=chapter.id,
            version=2, content="院子里更新后的正文\n\n" + private, section_snapshot_json=snapshots)
        test_db.add(latest)
        await test_db.flush()
        await book_shares.queue_active_share_audio(test_db, project_id=pid, chapter_id=chapter.id)
        await test_db.commit()
        assert (await guest.get(url)).status_code == 409
        assert (await guest.post("/api/mobile/memoir/book-shares/audio/plan", json=query)).status_code == 409
        current = await guest.post("/api/mobile/memoir/book-shares/audio/plan", json={**query, "version_id": latest.id})
        assert current.status_code == 200, current.text
        assert "院子里更新后的正文" in current.json()["content"]
        assert current.json()["source_text_hash"] != body["source_text_hash"]
        await client.delete(path, params=params)
        assert (await guest.get(url)).status_code == 404
        assert (await guest.post("/api/mobile/memoir/book-shares/audio/plan", json={**query, "version_id": latest.id})).status_code == 404


async def test_book_audio_uses_requested_shared_chapter(client, test_db, tts):
    pid = await ensure_mobile_memoir_project(client)
    _, chapters = await _create_confirmed_book_plan(test_db, project_id=pid)
    version = await _seed_confirmed_book_output(test_db, project_id=pid)
    token = (await client.put(f"/api/mobile/memoir/projects/{pid}/book-output/share")).json()["token"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as guest:
        query = {"token": token, "version_id": version.id, "chapter_id": chapters[0].id}
        response = await guest.post("/api/mobile/memoir/book-shares/audio/plan", json=query)
        assert response.status_code == 200, response.text
        assert response.json()["content"] == version.manuscript_json["chapters"][0]["content"]
        assert all(segment["status"] == "pending" for segment in response.json()["segments"])
        assert (await guest.post("/api/mobile/memoir/book-shares/audio/plan", json={**query, "chapter_id": pid})).status_code == 404


async def test_missing_audio_never_enqueues_from_public_read(client, test_db, tts):
    pid, _, _, version, section, _, _ = await saved_stories(client, test_db)
    path = f"/api/mobile/memoir/projects/{pid}/book-output/share"
    token = (await client.put(path, params={"section_id": section.id})).json()["token"]
    from sqlalchemy import delete
    await test_db.execute(delete(MemoirEditingJob).where(MemoirEditingJob.job_type == "generate_book_audio_chunk"))
    await test_db.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as guest:
        response = await guest.post("/api/mobile/memoir/book-shares/audio/plan", json={"token": token, "version_id": version.id})
        assert response.status_code == 200, response.text
        assert all(segment["status"] == "failed" for segment in response.json()["segments"])
        assert await test_db.scalar(select(func.count()).select_from(MemoirEditingJob).where(MemoirEditingJob.job_type == "generate_book_audio_chunk")) == 0
        assert (await guest.post("/api/mobile/memoir/book-shares/read", json={"token": token})).status_code == 200


async def test_author_repreparing_share_retries_failed_audio_without_public_retry(client, test_db, tts):
    pid, _, _, _, section, _, _ = await saved_stories(client, test_db)
    path = f"/api/mobile/memoir/projects/{pid}/book-output/share"
    params = {"section_id": section.id}
    first = await client.put(path, params=params)
    assert first.status_code == 200, first.text
    job = await test_db.scalar(select(MemoirEditingJob).where(
        MemoirEditingJob.job_type == "generate_book_audio_chunk"))
    assert job is not None
    job.status = "failed"
    await test_db.commit()

    await book_shares.queue_active_share_audio(test_db, project_id=pid, chapter_id=section.chapter_id)
    await test_db.commit()
    await test_db.refresh(job)
    assert job.status == "failed"

    repeated = await client.put(path, params=params)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["token"] == first.json()["token"]
    await test_db.refresh(job)
    assert job.status == "pending"


async def test_saved_book_version_prepares_audio_without_committing_the_author_transaction(client, test_db, tts):
    from app.domain.memoir.types import MemoirCreatedBy
    from app.models.memoir import MemoirBookOutput
    from app.services.memoir.book_output_versions import append_book_output_version

    pid = await ensure_mobile_memoir_project(client)
    await _create_confirmed_book_plan(test_db, project_id=pid)
    initial = await _seed_confirmed_book_output(test_db, project_id=pid)
    share = await client.put(f"/api/mobile/memoir/projects/{pid}/book-output/share")
    assert share.status_code == 200, share.text
    output = await test_db.get(MemoirBookOutput, initial.output_id)
    manuscript = deepcopy(initial.manuscript_json)
    manuscript["chapters"][0]["content"] = "作者保存后的新版朗读正文。"
    snapshots = deepcopy(initial.chapter_snapshot_json)
    snapshots[0]["content"] = manuscript["chapters"][0]["content"]
    saved = await append_book_output_version(test_db, output=output, content="新版正文",
        chapter_snapshot_json=snapshots, manuscript_json=manuscript, created_by=MemoirCreatedBy.USER)
    version_id = saved.id
    jobs = list((await test_db.scalars(select(MemoirEditingJob).where(
        MemoirEditingJob.job_type == "generate_book_audio_chunk"))).all())
    assert any(job.payload_json["text"] == manuscript["chapters"][0]["content"] for job in jobs)
    assert not tts.calls
    await test_db.rollback()
    from app.models.memoir import MemoirBookOutputVersion
    assert await test_db.get(MemoirBookOutputVersion, version_id) is None
    assert not any(job.payload_json["version_id"] == version_id for job in (await test_db.scalars(
        select(MemoirEditingJob).where(MemoirEditingJob.job_type == "generate_book_audio_chunk"))).all())


async def test_background_book_audio_does_not_starve_editing_and_interactive_playback_promotes_its_window(client, test_db, tts):
    from app.domain.memoir.types import MemoirEditingJobType
    from app.models.projects import Project
    from app.schemas.memoir_book import MemoirAudioPlanRequest
    from app.services.memoir.book_audio_jobs import AudioSource, prepare_audio_plan
    from app.services.memoir.editing_jobs import claim_due_editing_jobs, enqueue_editing_job

    pid, _, _, version, _, _, _ = await saved_stories(client, test_db)
    owner = (await test_db.get(Project, pid)).user_id
    source = AudioSource(version.id, "\n\n".join(f"第{i}段。" * 15 for i in range(10)))
    queued = await prepare_audio_plan(test_db, user_id=owner, project_id=pid,
        source=source, req=MemoirAudioPlanRequest(), prepare_all=True)
    assert len(queued.segments) > 5
    ordinary = await enqueue_editing_job(test_db, project_id=pid, job_type=MemoirEditingJobType.ORGANIZE_MATERIAL)
    await test_db.commit()
    batch = await claim_due_editing_jobs(test_db, limit=5)
    assert batch[0].job_id == ordinary.id
    assert len(batch) < len(queued.segments)
    last_index = len(queued.segments) - 1
    promoted = await prepare_audio_plan(test_db, user_id=owner, project_id=pid, source=source,
        req=MemoirAudioPlanRequest(start_index=last_index))
    # Repeating background warm-up must not demote the explicit playback request.
    await prepare_audio_plan(test_db, user_id=owner, project_id=pid,
        source=source, req=MemoirAudioPlanRequest(), prepare_all=True)
    next_batch = await claim_due_editing_jobs(test_db, limit=1)
    assert next_batch[0].job_id == promoted.segments[last_index].job_id


@pytest.mark.parametrize("scope", ["story", "chapter"])
async def test_first_share_waits_for_a_concurrent_save_before_preparing_audio(client, test_db, tts, monkeypatch, scope):
    import asyncio
    from app.database import async_session_maker
    from app.models.projects import Project
    from app.services.memoir.chapter_generation import lock_chapter_draft_write

    pid, chapter, draft, previous, section, _, _ = await saved_stories(client, test_db)
    owner = (await test_db.get(Project, pid)).user_id
    snapshots = deepcopy(previous.section_snapshot_json)
    snapshots[0]["content"] = "首次分享前刚刚保存的新故事。"
    acquired = asyncio.Event()
    original = book_shares._owned_resource

    async def observed(*args, **kwargs):
        result = await original(*args, **kwargs)
        acquired.set()
        return result

    monkeypatch.setattr(book_shares, "_owned_resource", observed)
    async with async_session_maker() as writer, async_session_maker() as sharer:
        await lock_chapter_draft_write(writer, project_id=pid, chapter_id=chapter.id)
        newer = MemoirBookChapterDraftVersion(draft_id=draft.id, project_id=pid, chapter_id=chapter.id,
            version=2, content="新故事正文", section_snapshot_json=snapshots)
        writer.add(newer)
        await writer.flush()
        task = asyncio.create_task(book_shares.create_share(sharer, user_id=owner, project_id=pid,
            **({"section_id": section.id} if scope == "story" else {"chapter_id": chapter.id})))
        try:
            await asyncio.wait_for(acquired.wait(), 3)
            # Save hook sees no share yet, so the waiting first share must queue v2.
            await book_shares.queue_active_share_audio(writer, project_id=pid, chapter_id=chapter.id)
            await writer.commit()
            shared = await asyncio.wait_for(task, 5)
            result = await book_shares.shared_audio_plan(sharer, shared.token, newer.id)
            assert result.segments and all(segment.status == "pending" for segment in result.segments)
            assert result.content == (snapshots[0]["content"] if scope == "story" else "新故事正文")
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
