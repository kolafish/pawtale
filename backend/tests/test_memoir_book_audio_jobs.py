import asyncio

import pytest
from sqlalchemy import select

from app.database import async_session_maker
from app.domain.memoir.types import MemoirEditingJobStatus
from app.models.memoir import AudioFile, MemoirBookOutput, MemoirBookOutputVersion, MemoirEditingJob
from app.models.projects import Project
from app.schemas.memoir_book import MemoirBookAudioPlanRequest
from app.services.memoir import book_audio, book_audio_jobs
from app.services.memoir.audio_files import audio_file_path
from app.services.memoir.editing_job_handlers import register_memoir_editing_job_handlers
from app.services.memoir.editing_jobs import claim_editing_job, run_editing_job
from tests.memoir_helpers import ensure_mobile_memoir_project, run_test_editing_job
from tests.test_memoir_book_outputs import (
    FakeBookTTS, _create_confirmed_book_plan, _seed_confirmed_book_output,
)


@pytest.fixture
def tts(monkeypatch, tmp_path):
    client = FakeBookTTS()
    register_memoir_editing_job_handlers()
    monkeypatch.setattr(book_audio, "get_tts_client", lambda: client)
    monkeypatch.setattr("app.services.memoir.audio_files.MEMOIR_AUDIO_DIR", tmp_path / "audio")
    return client


async def seed(client, db, content="书名\n\n第一段🌿正文。\n\n第二段正文。\n\n第三段正文。"):
    project_id = await ensure_mobile_memoir_project(client)
    await _create_confirmed_book_plan(db, project_id=project_id)
    version = await _seed_confirmed_book_output(db, project_id=project_id)
    version.content = content
    await db.commit()
    project = await db.get(Project, project_id)
    return project_id, project.user_id, version


async def prepare(db, project_id, user_id, version, **kwargs):
    return await book_audio_jobs.prepare_book_audio(
        db, project_id=project_id, user_id=user_id,
        req=MemoirBookAudioPlanRequest(version_id=version.id, **kwargs),
    )


async def test_plan_returns_before_synthesis_and_reuses_completed_first_chunk(client, test_db, tts):
    project_id, user_id, version = await seed(client, test_db)
    url = f"/api/mobile/memoir/projects/{project_id}/book-output/tts/plan"
    response = await client.post(url, json={"version_id": version.id})
    assert response.status_code == 200, response.text
    first = response.json()
    assert [s["status"] for s in first["segments"]] == ["pending", "pending", "idle", "idle"]
    assert tts.calls == []
    assert await run_test_editing_job(test_db, first["segments"][0]["job_id"])
    plan = await prepare(test_db, project_id, user_id, version)
    assert plan.ready_count == 1
    assert plan.segments[0].audio_file_id
    assert plan.segments[1].status == "pending"
    assert plan.segments[2].status == "idle"
    assert len(tts.calls) == 1


async def test_concurrent_requests_and_worker_claims_share_one_task(client, test_db, tts):
    project_id, user_id, version = await seed(client, test_db, "一段正文。")
    await test_db.commit()

    async def request():
        async with async_session_maker() as db:
            return await prepare(db, project_id, user_id, version)

    one, two = await asyncio.gather(request(), request())
    assert one.segments[0].job_id == two.segments[0].job_id

    async def work():
        async with async_session_maker() as db:
            lease = await claim_editing_job(db, one.segments[0].job_id)
            return await run_editing_job(db, lease) if lease else False

    assert sorted(await asyncio.gather(work(), work())) == [False, True]
    assert len(tts.calls) == 1
    assert (await request()).segments[0].audio_file_id


async def test_new_versions_reuse_unchanged_reordered_paragraphs_and_pin_old_playlist(client, test_db, tts):
    project_id, user_id, old = await seed(client, test_db, "第一段🌿正文。\n\n第二段正文。")
    original = await prepare(test_db, project_id, user_id, old)
    for segment in original.segments:
        assert await run_test_editing_job(test_db, segment.job_id)
    original = await prepare(test_db, project_id, user_id, old)
    new = MemoirBookOutputVersion(
        output_id=old.output_id, project_id=project_id, plan_id=old.plan_id, version=old.version + 1,
        content="新增的正文。\n\n第二段正文。\n\n第一段🌿正文。", created_by=old.created_by,
    )
    test_db.add(new)
    output = await test_db.get(MemoirBookOutput, old.output_id)
    output.metadata_json = {}  # Ordinary book edits clear the old whole-book cache.
    await test_db.commit()
    current = await prepare(test_db, project_id, user_id, new)
    assert current.ready_count == 2
    assert current.segments[0].status == "pending"
    assert current.segments[1].audio_file_id == original.segments[1].audio_file_id
    assert current.segments[2].audio_file_id == original.segments[0].audio_file_id
    assert current.segments[2].alignment[0].text_start == 0
    assert len(tts.calls) == 2
    pinned = await prepare(test_db, project_id, user_id, old, profile_key=original.profile_key)
    assert pinned == original
    new.content = old.content
    await test_db.commit()
    assert (await prepare(test_db, project_id, user_id, new)).ready_count == 2


@pytest.mark.parametrize("change", ["voice", "speed", "model", "provider", "sample_rate"])
async def test_audio_profile_changes_invalidate_cache(client, test_db, tts, change):
    project_id, user_id, version = await seed(client, test_db, "一段正文。")
    first = await prepare(test_db, project_id, user_id, version)
    assert await run_test_editing_job(test_db, first.segments[0].job_id)
    options = {}
    if change == "voice":
        tts.default_voice = "another-default"
    elif change == "speed":
        options["speed_ratio"] = 1.25
    elif change == "model":
        tts.model_name = "another-model"
    elif change == "provider":
        tts.provider_name = "stepfun"
    else:
        tts.sample_rate = 48000
    changed = await prepare(test_db, project_id, user_id, version, **options)
    assert changed.ready_count == 0
    assert changed.segments[0].job_id != first.segments[0].job_id
    assert changed.profile_key != first.profile_key


async def test_failures_retry_only_explicitly_and_keep_completed_audio(client, test_db, tts, monkeypatch):
    project_id, user_id, version = await seed(client, test_db, "第一段正文。\n\n第二段正文。")
    plan = await prepare(test_db, project_id, user_id, version)
    assert await run_test_editing_job(test_db, plan.segments[0].job_id)
    original = tts.synthesize

    async def fail(*args, **kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(tts, "synthesize", fail)
    assert not await run_test_editing_job(test_db, plan.segments[1].job_id)
    failed = await prepare(test_db, project_id, user_id, version)
    assert failed.segments[0].audio_file_id
    assert failed.segments[1].status == "failed"
    assert (await prepare(test_db, project_id, user_id, version)).segments[1].status == "failed"
    monkeypatch.setattr(tts, "synthesize", original)
    retry = await prepare(test_db, project_id, user_id, version, retry_failed=True)
    assert retry.segments[1].status == "pending"
    assert retry.segments[0].audio_file_id == failed.segments[0].audio_file_id
    assert await run_test_editing_job(test_db, retry.segments[1].job_id)
    assert len(tts.calls) == 2


async def test_missing_audio_file_is_regenerated(client, test_db, tts):
    project_id, user_id, version = await seed(client, test_db)
    plan = await prepare(test_db, project_id, user_id, version)
    for index in range(len(plan.segments)):
        plan = await prepare(test_db, project_id, user_id, version, start_index=index)
        assert await run_test_editing_job(test_db, plan.segments[index].job_id)
    ready = await prepare(test_db, project_id, user_id, version)
    audio = await test_db.get(AudioFile, ready.segments[-1].audio_file_id)
    audio_file_path(audio).unlink()
    next_plan = await prepare(test_db, project_id, user_id, version)
    assert next_plan.ready_count == len(plan.segments) - 1
    assert next_plan.segments[-1].status == "idle"
    queued = await prepare(test_db, project_id, user_id, version, start_index=len(plan.segments) - 1)
    assert queued.segments[-1].status == "pending"
    assert await run_test_editing_job(test_db, queued.segments[-1].job_id)
    assert (await prepare(test_db, project_id, user_id, version)).segments[-1].audio_file_id != audio.id


async def test_legacy_recording_stays_available_after_book_metadata_changes(client, test_db, tts):
    project_id, user_id, version = await seed(client, test_db)
    synthesis = await book_audio.synthesize_text_audio(test_db, user_id=user_id, text=version.content)
    output = await test_db.get(MemoirBookOutput, version.output_id)
    output.metadata_json = {"book_tts": book_audio.audio_cache_payload(synthesis, voice=None, speed_ratio=1)}
    await test_db.commit()
    count = len(tts.calls)
    legacy = await prepare(test_db, project_id, user_id, version)
    assert legacy.ready_count == len(legacy.segments)
    assert [s.audio_file_id for s in legacy.segments] == [a.id for a in synthesis.audio_files]
    output.metadata_json = {}
    await test_db.commit()
    assert (await prepare(test_db, project_id, user_id, version, profile_key=legacy.profile_key)) == legacy
    assert len(tts.calls) == count
    design_version = MemoirBookOutputVersion(
        output_id=version.output_id, project_id=project_id, plan_id=version.plan_id,
        version=version.version + 1, content=version.content, created_by=version.created_by,
        manuscript_json={"render_settings": {"cover_style": "warm_gray"}},
    )
    test_db.add(design_version)
    await test_db.commit()
    unchanged = await prepare(test_db, project_id, user_id, design_version)
    assert unchanged.version_id == design_version.id
    assert [s.audio_file_id for s in unchanged.segments] == [s.audio_file_id for s in legacy.segments]
    assert unchanged.ready_count == legacy.ready_count
    assert len(tts.calls) == count
    output.metadata_json = {"book_tts": book_audio.audio_cache_payload(synthesis, voice=None, speed_ratio=1)}
    tts.default_voice = "changed-default"
    await test_db.commit()
    changed = await prepare(test_db, project_id, user_id, version)
    assert changed.ready_count == 0
    assert changed.profile_key != legacy.profile_key


async def test_plan_rejects_missing_version_and_changed_profile(client, test_db, tts):
    project_id, user_id, version = await seed(client, test_db)
    url = f"/api/mobile/memoir/projects/{project_id}/book-output/tts/plan"
    missing = await client.post(url, json={"version_id": "missing-version"})
    assert missing.status_code == 404
    initial = await prepare(test_db, project_id, user_id, version)
    changed = await client.post(url, json={"version_id": version.id, "profile_key": initial.profile_key, "speed_ratio": 1.25})
    assert changed.status_code == 409
    invalid = await client.post(url, json={"version_id": version.id, "start_index": 500})
    assert invalid.status_code == 400


async def test_forced_regeneration_replaces_new_requests_but_keeps_playing_legacy_audio(client, test_db, tts):
    project_id, user_id, version = await seed(client, test_db)
    synthesis = await book_audio.synthesize_text_audio(test_db, user_id=user_id, text=version.content)
    output = await test_db.get(MemoirBookOutput, version.output_id)
    output.metadata_json = {"book_tts": book_audio.audio_cache_payload(synthesis, voice=None, speed_ratio=1)}
    await test_db.commit()
    legacy = await prepare(test_db, project_id, user_id, version)
    forced = await book_audio_jobs.complete_book_audio(
        test_db, project_id=project_id, user_id=user_id,
        req=MemoirBookAudioPlanRequest(version_id=version.id, force_regenerate=True),
    )
    current = await prepare(test_db, project_id, user_id, version)
    assert [s.audio_file_id for s in current.segments] == forced.audio_file_ids
    assert current.profile_key != legacy.profile_key
    pinned = await prepare(test_db, project_id, user_id, version, profile_key=legacy.profile_key)
    assert pinned == legacy


async def test_committed_audio_checkpoint_survives_worker_restart(client, test_db, tts):
    project_id, user_id, version = await seed(client, test_db, "一段正文。")
    plan = await prepare(test_db, project_id, user_id, version)
    assert await run_test_editing_job(test_db, plan.segments[0].job_id)
    job = await test_db.scalar(select(MemoirEditingJob).where(MemoirEditingJob.id == plan.segments[0].job_id))
    job.status = MemoirEditingJobStatus.PENDING.value
    await test_db.commit()
    assert await run_test_editing_job(test_db, job.id)
    assert len(tts.calls) == 1


async def test_interrupted_provider_call_requires_explicit_retry(client, test_db, tts):
    project_id, user_id, version = await seed(client, test_db, "一段正文。")
    plan = await prepare(test_db, project_id, user_id, version)
    job = await test_db.get(MemoirEditingJob, plan.segments[0].job_id)
    job.result_json = book_audio_jobs.AudioChunkResult(provider_started=True).model_dump(mode="json")
    await test_db.commit()
    assert not await run_test_editing_job(test_db, job.id)
    assert tts.calls == []
    await test_db.refresh(version)
    assert (await prepare(test_db, project_id, user_id, version)).segments[0].status == "failed"
    retried = await prepare(test_db, project_id, user_id, version, retry_failed=True)
    assert await run_test_editing_job(test_db, retried.segments[0].job_id)
    assert len(tts.calls) == 1


@pytest.mark.parametrize("target", ["chapter", "section", "outline"])
async def test_narration_entries_prepare_immediately_and_share_legacy_playback(client, test_db, tts, target):
    from app.models.memoir import MemoirSection

    project_id = await ensure_mobile_memoir_project(client)
    _, chapters = await _create_confirmed_book_plan(test_db, project_id=project_id)
    base = f"/api/mobile/memoir/projects/{project_id}/book-plan"
    endpoint = f"{base}/outline/tts"
    if target != "outline":
        endpoint = f"{base}/chapters/{chapters[0].id}/draft"
        if target == "section":
            section = await test_db.scalar(select(MemoirSection).where(MemoirSection.chapter_id == chapters[0].id))
            endpoint += f"/sections/{section.id}"
        endpoint += "/tts"
    first = await client.post(endpoint + "/plan", json={})
    assert first.status_code == 200, first.text
    plan = first.json()
    assert tts.calls == []
    assert await run_test_editing_job(test_db, plan["segments"][0]["job_id"])
    ready = await client.post(endpoint + "/plan", json={
        "version_id": plan["version_id"], "source_text_hash": plan["source_text_hash"], "profile_key": plan["profile_key"],
    })
    assert ready.status_code == 200, ready.text
    assert ready.json()["segments"][0]["audio_file_id"]
    if len(plan["segments"]) > 1:
        assert ready.json()["segments"][1]["status"] == "pending"
    completed = await client.post(endpoint, json={})
    assert completed.status_code == 200, completed.text
    assert completed.json()["audio_file_ids"][0] == ready.json()["segments"][0]["audio_file_id"]
    assert len(tts.calls) == len(plan["segments"])


async def test_chapter_and_section_share_cache_and_pin_reading_version(client, test_db, tts):
    from app.models.memoir import MemoirBookChapterDraftVersion, MemoirSection

    project_id = await ensure_mobile_memoir_project(client)
    _, chapters = await _create_confirmed_book_plan(test_db, project_id=project_id)
    chapter_id = chapters[0].id
    version = await test_db.scalar(select(MemoirBookChapterDraftVersion).where(MemoirBookChapterDraftVersion.chapter_id == chapter_id))
    section = await test_db.scalar(select(MemoirSection).where(MemoirSection.chapter_id == chapter_id))
    base = f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter_id}/draft"
    first = (await client.post(base + "/tts/plan", json={})).json()
    for segment in first["segments"]:
        assert await run_test_editing_job(test_db, segment["job_id"])
    section_plan = (await client.post(base + f"/sections/{section.id}/tts/plan", json={})).json()
    assert section_plan["ready_count"] == len(first["segments"])
    assert [s["job_id"] for s in section_plan["segments"]] == [s["job_id"] for s in first["segments"]]
    new = MemoirBookChapterDraftVersion(
        draft_id=version.draft_id, chapter_id=chapter_id, project_id=project_id,
        version=version.version + 1, content="新增的故事。", created_by=version.created_by,
        section_snapshot_json=[{**v, "content": "新增的故事。"} for v in version.section_snapshot_json],
    )
    test_db.add(new)
    await test_db.commit()
    pinned = await client.post(base + "/tts/plan", json={
        "version_id": first["version_id"], "source_text_hash": first["source_text_hash"], "profile_key": first["profile_key"],
    })
    assert pinned.status_code == 200, pinned.text
    assert pinned.json()["ready_count"] == len(first["segments"])
    current = (await client.post(base + "/tts/plan", json={})).json()
    assert current["version_id"] == new.id
    assert current["source_text_hash"] != first["source_text_hash"]
    assert current["segments"][0]["audio_file_id"] == pinned.json()["segments"][0]["audio_file_id"]
    assert len(tts.calls) == len(first["segments"])
    wrong = await client.post(f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapters[1].id}/draft/tts/plan", json={"version_id": version.id})
    assert wrong.status_code == 404


async def test_outline_change_is_rejected_before_new_jobs_are_enqueued(client, test_db, tts):
    project_id = await ensure_mobile_memoir_project(client)
    plan, chapters = await _create_confirmed_book_plan(test_db, project_id=project_id)
    url = f"/api/mobile/memoir/projects/{project_id}/book-plan/outline/tts/plan"
    first = (await client.post(url, json={})).json()
    count = len((await test_db.scalars(select(MemoirEditingJob))).all())
    chapters[0].title = "修改后的章节名"
    await test_db.commit()
    changed = await client.post(url, json={
        "version_id": plan.id, "source_text_hash": first["source_text_hash"], "profile_key": first["profile_key"],
    })
    assert changed.status_code == 409
    assert len((await test_db.scalars(select(MemoirEditingJob))).all()) == count
    assert tts.calls == []


def test_long_paragraphs_have_short_chunks_and_exact_unicode_offsets():
    text = "这是回忆🌿里的故事。" * 90
    chunks = book_audio_jobs.split_book_audio_text(text)
    assert len(chunks) > 2
    assert all(len(c.text) <= 180 for c in chunks)
    assert all(text[c.text_start:c.text_end] == c.text for c in chunks)
    assert "".join(c.text for c in chunks) == text
