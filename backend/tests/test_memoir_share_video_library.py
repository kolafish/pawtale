from datetime import timedelta
from io import BytesIO
import subprocess
from types import SimpleNamespace

from httpx import ASGITransport, AsyncClient
from PIL import Image
import pytest
from sqlalchemy import select

from app.clock import utcnow_naive
from app.domain.memoir.types import MemoirShareVideoJobStatus
from app.main import app
from app.models.memoir import (
    MemoirMaterialShareVideo,
    MemoirImageAsset,
    MemoirShareVideoCandidate,
    MemoirShareVideoOutput,
    MemoirVoiceUtterance,
)
from app.models.projects import Project
from app.models.users import User
from app.services.memoir import share_video_jobs
from tests.memoir_helpers import create_test_voice_transcript, ensure_mobile_memoir_project


async def _add_video_job(
    db,
    created,
    *,
    job_id: str,
    status: MemoirShareVideoJobStatus,
    created_at,
    completed_at=None,
    with_output: bool = True,
    metadata=None,
):
    job = MemoirMaterialShareVideo(
        id=job_id,
        project_id=created["session"]["project_id"],
        utterance_id=created["utterance_id"],
        audio_file_id=created["audio_file_id"],
        user_id=created["user_id"],
        status=status.value,
        text_source="edited",
        text_hash=f"hash-{job_id}",
        selected_text="小时候我住在外婆家。",
        start_char=0,
        end_char=11,
        start_ms=0,
        end_ms=8000,
        metadata_json={"title": f"{job_id} 的回忆", **(metadata or {})},
        created_at=created_at,
        completed_at=completed_at,
    )
    db.add(job)
    await db.flush()
    if with_output:
        job.output = MemoirShareVideoOutput(
            job_id=job.id,
            project_id=job.project_id,
            storage_path=f"memoir/share-videos/{job.user_id}/{job.id}.mp4",
            mime_type="video/mp4",
            size_bytes=1024,
        )
    return job


async def test_share_video_library_lists_completed_jobs_with_stable_pagination(client, test_db):
    created = await create_test_voice_transcript(client, test_db, text="小时候我住在外婆家。")
    project_id = created["session"]["project_id"]
    now = utcnow_naive()
    await _add_video_job(
        test_db, created, job_id="video-old", status=MemoirShareVideoJobStatus.SUCCEEDED,
        created_at=now - timedelta(hours=4), completed_at=now - timedelta(hours=3),
    )
    await _add_video_job(
        test_db, created, job_id="video-legacy", status=MemoirShareVideoJobStatus.SUCCEEDED,
        created_at=now - timedelta(hours=2), completed_at=None,
    )
    for job_id in ("video-a", "video-b"):
        await _add_video_job(
            test_db, created, job_id=job_id, status=MemoirShareVideoJobStatus.SUCCEEDED,
            created_at=now - timedelta(hours=5), completed_at=now - timedelta(hours=1),
        )
    await _add_video_job(
        test_db, created, job_id="video-failed", status=MemoirShareVideoJobStatus.FAILED,
        created_at=now, with_output=False,
    )
    await _add_video_job(
        test_db, created, job_id="video-no-output", status=MemoirShareVideoJobStatus.SUCCEEDED,
        created_at=now, completed_at=now, with_output=False,
    )
    utterance = await test_db.get(MemoirVoiceUtterance, created["utterance_id"])
    assert utterance is not None
    utterance.transcript_edited = "后来重新整理过的讲述。"
    await test_db.commit()

    url = f"/api/mobile/memoir/projects/{project_id}/share-video/jobs"
    first = await client.get(url, params={"limit": 2, "offset": 0})
    second = await client.get(url, params={"limit": 2, "offset": 2})
    assert first.status_code == second.status_code == 200
    assert first.json()["total"] == second.json()["total"] == 4
    assert first.json()["limit"] == second.json()["limit"] == 2
    assert [item["id"] for item in first.json()["items"]] == ["video-b", "video-a"]
    assert [item["id"] for item in second.json()["items"]] == ["video-legacy", "video-old"]
    assert first.json()["items"][0]["title"] == "video-b 的回忆"
    assert first.json()["items"][0]["selected_text"] == "小时候我住在外婆家。"
    assert first.json()["items"][0]["duration_ms"] == 8000
    assert first.json()["items"][0]["utterance_id"] == created["utterance_id"]
    assert first.json()["items"][0]["completed_at"] is not None
    assert second.json()["items"][0]["completed_at"] is None


async def test_share_video_library_checks_project_ownership_and_allows_admin(client, test_db):
    await ensure_mobile_memoir_project(client)
    other_user = User(email="share-video-other@example.com", password_hash="unused", display_name="其他用户")
    test_db.add(other_user)
    await test_db.flush()
    other_project = Project(user_id=other_user.id, type="memoir", title="别人的回忆录")
    test_db.add(other_project)
    await test_db.commit()

    url = f"/api/mobile/memoir/projects/{other_project.id}/share-video/jobs"
    assert (await client.get(url)).status_code == 404

    current_user_id = (await client.get("/api/auth/me")).json()["id"]
    current_user = await test_db.get(User, current_user_id)
    assert current_user is not None
    current_user.role = "admin"
    await test_db.commit()
    response = await client.get(url)
    assert response.status_code == 200
    assert response.json() == {"items": [], "active_jobs": [], "failed_jobs": [], "total": 0, "limit": 20, "offset": 0}


async def test_share_video_library_recovers_active_jobs_outside_completed_pagination(client, test_db):
    created = await create_test_voice_transcript(client, test_db)
    now = utcnow_naive()
    pending = await _add_video_job(
        test_db, created, job_id="waiting", status=MemoirShareVideoJobStatus.PENDING,
        created_at=now, with_output=False,
    )
    running = await _add_video_job(
        test_db, created, job_id="rendering", status=MemoirShareVideoJobStatus.RUNNING,
        created_at=now - timedelta(minutes=1), with_output=False,
        metadata={"background_status": "generating", "background_scene_count": 3},
    )
    await _add_video_job(
        test_db, created, job_id="completed", status=MemoirShareVideoJobStatus.SUCCEEDED,
        created_at=now - timedelta(hours=1), completed_at=now - timedelta(minutes=5),
    )
    other_project = Project(user_id=created["user_id"], type="memoir", title="另一份回忆")
    test_db.add(other_project)
    await test_db.flush()
    other_created = await create_test_voice_transcript(client, test_db, project_id=other_project.id)
    await _add_video_job(
        test_db, other_created, job_id="other-project", status=MemoirShareVideoJobStatus.RUNNING,
        created_at=now, with_output=False,
    )
    await _add_video_job(
        test_db, other_created, job_id="other-failure", status=MemoirShareVideoJobStatus.FAILED,
        created_at=now, with_output=False,
    )
    await test_db.commit()

    url = f"/api/mobile/memoir/projects/{pending.project_id}/share-video/jobs"
    response = await client.get(url, params={"limit": 1, "offset": 10})
    assert response.status_code == 200
    data = response.json()
    assert data["items"] == [] and data["total"] == 1
    assert [(job["id"], job["status"]) for job in data["active_jobs"]] == [
        (pending.id, "pending"), (running.id, "running"),
    ]
    assert data["active_jobs"][1]["background_status"] == "generating"
    assert data["active_jobs"][1]["background_scene_count"] == 3
    assert all(job["output"] is None for job in data["active_jobs"])

    pending.status = MemoirShareVideoJobStatus.SUCCEEDED.value
    pending.completed_at = now
    pending.output = MemoirShareVideoOutput(
        job_id=pending.id, project_id=pending.project_id,
        storage_path=f"memoir/share-videos/{pending.user_id}/{pending.id}.mp4",
        mime_type="video/mp4", size_bytes=1024,
    )
    running.status = MemoirShareVideoJobStatus.FAILED.value
    running.error_message = "配图生成失败"
    await test_db.commit()
    refreshed = (await client.get(url, params={"limit": 1})).json()
    assert refreshed["active_jobs"] == []
    assert refreshed["total"] == 2
    assert [item["id"] for item in refreshed["items"]] == [pending.id]
    assert [(job["id"], job["error_message"]) for job in refreshed["failed_jobs"]] == [
        (running.id, "配图生成失败"),
    ]

    next_job = await _add_video_job(
        test_db, created, job_id="next-video", status=MemoirShareVideoJobStatus.PENDING,
        created_at=now + timedelta(minutes=1), with_output=False,
    )
    await test_db.commit()
    reopened = (await client.get(url, params={"limit": 1, "offset": 10})).json()
    assert [job["id"] for job in reopened["active_jobs"]] == [next_job.id]
    assert [job["id"] for job in reopened["failed_jobs"]] == [running.id]
    assert reopened["failed_jobs"][0]["output"] is None
    assert reopened["items"] == [] and reopened["total"] == 2


async def test_video_thumbnail_uses_real_frames_and_keeps_cached_images_private(client, test_db, tmp_path, monkeypatch):
    monkeypatch.setattr(share_video_jobs, "MEMOIR_SHARE_VIDEO_DIR", tmp_path)
    created = await create_test_voice_transcript(client, test_db)
    job = await _add_video_job(
        test_db, created, job_id="thumbnail", status=MemoirShareVideoJobStatus.SUCCEEDED,
        created_at=utcnow_naive(),
    )
    await test_db.commit()
    path = share_video_jobs.share_video_file_path(job)
    path.parent.mkdir(parents=True)
    subprocess.run([
        share_video_jobs.rendering._ffmpeg_executable(), "-y", "-f", "lavfi",
        "-i", "color=c=green:s=640x360:d=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
    ], check=True, capture_output=True, timeout=10)
    original = path.read_bytes()
    url = f"/api/mobile/memoir/share-video/jobs/{job.id}/download?variant=thumbnail"
    first = await client.get(url)
    assert first.status_code == 200 and first.headers["content-type"] == "image/jpeg"
    image = Image.open(BytesIO(first.content))
    assert image.size == (320, 180)
    assert image.getpixel((160, 90))[1] > 100
    assert path.read_bytes() == original

    async def never_encode(*args, **kwargs):
        pytest.fail("cached thumbnails must not be encoded again")

    monkeypatch.setattr(share_video_jobs.rendering, "_run_ffmpeg", never_encode)
    assert (await client.get(url)).content == first.content
    other = User(email="other-thumbnail-owner@example.com", password_hash="unused", display_name="其他人")
    test_db.add(other)
    await test_db.flush()
    project = await test_db.get(Project, job.project_id)
    project.user_id = other.id
    job.user_id = other.id
    await test_db.commit()
    assert (await client.get(url)).status_code == 404


async def test_forwarded_card_cover_is_a_sharp_crop_without_captions(client, test_db, tmp_path, monkeypatch):
    monkeypatch.setattr(share_video_jobs, "MEMOIR_SHARE_VIDEO_DIR", tmp_path)
    created = await create_test_voice_transcript(client, test_db)
    job = await _add_video_job(
        test_db, created, job_id="card", status=MemoirShareVideoJobStatus.SUCCEEDED,
        created_at=utcnow_naive(), metadata={"hook_text": "到了夏天，那条路上还有桑葚"},
    )
    await test_db.commit()
    path = share_video_jobs.share_video_file_path(job)
    path.parent.mkdir(parents=True)
    # Green story image with a blue caption band where the subtitles sit, then a red closing card.
    subprocess.run([
        share_video_jobs.rendering._ffmpeg_executable(), "-y", "-f", "lavfi",
        "-i", "color=c=green:s=720x1280:d=5,drawbox=y=880:w=720:h=400:color=blue:t=fill,"
        "drawbox=w=720:h=1280:color=red:t=fill:enable='gte(t,4.4)'",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
    ], check=True, capture_output=True, timeout=10)

    response = await client.get(f"/api/mobile/memoir/share-video/shares/{job.share_code}/video?variant=card")

    assert response.status_code == 200 and response.headers["content-type"] == "image/jpeg"
    image = Image.open(BytesIO(response.content)).convert("RGB")
    assert image.size == (720, 576)
    # No caption band, and the chosen opening line is set in light text near the bottom.
    assert all(image.getpixel((x, 300))[2] < 80 for x in (10, 360, 710))
    text_rows = image.crop((0, 380, 720, 576)).convert("L")
    assert text_rows.getextrema()[1] > 200

    # After playback the page holds the closing card, so its mini program code stays scannable.
    end = await client.get(f"/api/mobile/memoir/share-video/shares/{job.share_code}/video?variant=end")
    assert end.status_code == 200 and end.headers["content-type"] == "image/jpeg"
    end_image = Image.open(BytesIO(end.content)).convert("RGB")
    assert end_image.size == (720, 1280)
    red, green, _blue = end_image.getpixel((360, 640))
    assert red > 180 and green < 80

    # The player's poster is the portrait opening frame.
    poster = await client.get(f"/api/mobile/memoir/share-video/shares/{job.share_code}/video?variant=poster")
    assert poster.status_code == 200 and poster.headers["content-type"] == "image/jpeg"
    assert Image.open(BytesIO(poster.content)).size == (720, 1280)


async def test_remake_preserves_completed_file_and_lists_each_version(client, test_db, tmp_path, monkeypatch):
    monkeypatch.setattr(share_video_jobs, "MEMOIR_SHARE_VIDEO_DIR", tmp_path)
    monkeypatch.setattr(share_video_jobs, "get_settings", lambda: SimpleNamespace(
        memoir_share_video_ai_background_enabled=True, volc_ark_api_key="test-key",
    ))
    text = "小时候我住在外婆家。"
    created = await create_test_voice_transcript(client, test_db, text=text)
    old = await _add_video_job(
        test_db, created, job_id="original", status=MemoirShareVideoJobStatus.SUCCEEDED,
        created_at=utcnow_naive(), metadata={"video_style": "illustrated"},
    )
    await test_db.commit()
    path = share_video_jobs.share_video_file_path(old)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"original video")

    remake = await client.post(
        f"/api/mobile/memoir/voice-transcripts/{old.utterance_id}/share-video/jobs",
        json={"start_char": 0, "end_char": len(text), "video_style": "illustrated"},
    )
    assert remake.status_code == 201, remake.text
    assert remake.json()["id"] != old.id
    new = await test_db.get(MemoirMaterialShareVideo, remake.json()["id"])
    new.status = MemoirShareVideoJobStatus.SUCCEEDED.value
    new.completed_at = utcnow_naive()
    new.output = MemoirShareVideoOutput(
        job_id=new.id, project_id=new.project_id,
        storage_path=f"memoir/share-videos/{new.user_id}/{new.id}.mp4",
        mime_type="video/mp4", size_bytes=10,
    )
    await test_db.commit()
    share_video_jobs.share_video_file_path(new).write_bytes(b"new video!")

    library = await client.get(f"/api/mobile/memoir/projects/{old.project_id}/share-video/jobs")
    assert {item["id"]: item["video_style"] for item in library.json()["items"]} == {
        old.id: "illustrated", new.id: "illustrated",
    }
    for job, contents in ((old, b"original video"), (new, b"new video!")):
        response = await client.get(f"/api/mobile/memoir/share-video/jobs/{job.id}/download")
        assert response.status_code == 200
        assert response.content == contents


async def test_video_delete_requires_confirmation_and_preserves_other_versions_and_sources(
    client, test_db, tmp_path, monkeypatch,
):
    monkeypatch.setattr(share_video_jobs, "MEMOIR_SHARE_VIDEO_DIR", tmp_path)
    created = await create_test_voice_transcript(client, test_db, text="小时候我住在外婆家。")
    jobs = [await _add_video_job(
        test_db, created, job_id=job_id, status=MemoirShareVideoJobStatus.SUCCEEDED,
        created_at=utcnow_naive(),
    ) for job_id in ("chosen", "retained")]
    chosen, retained = jobs
    image_path = tmp_path / "illustration.png"
    image_path.write_bytes(b"expensive illustration")
    asset = MemoirImageAsset(
        project_id=chosen.project_id, user_id=chosen.user_id, status="ready",
        source_type="share_video_background", source_signature="chosen-image",
        share_video_job_id=chosen.id, source_utterance_id=chosen.utterance_id,
        source_quote=chosen.selected_text, storage_path=str(image_path),
    )
    candidate = MemoirShareVideoCandidate(
        project_id=chosen.project_id, voice_session_id=created["session"]["id"],
        utterance_id=chosen.utterance_id, status="accepted", video_job_id=chosen.id,
        audio_file_id=chosen.audio_file_id, transcript_version_id=created["transcript_version_id"],
        text_hash=chosen.text_hash, selected_text=chosen.selected_text,
        start_char=chosen.start_char, end_char=chosen.end_char,
        start_ms=chosen.start_ms, end_ms=chosen.end_ms,
    )
    test_db.add_all([asset, candidate])
    await test_db.commit()
    for job in jobs:
        path = share_video_jobs.share_video_file_path(job)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(job.id.encode())
        path.with_suffix(".thumbnail.jpg").write_bytes(b"thumbnail")
        path.with_suffix(".card.jpg").write_bytes(b"card")
        path.with_suffix(".end.jpg").write_bytes(b"end")
        path.with_suffix(".poster.jpg").write_bytes(b"poster")
    shared_read = "/api/mobile/memoir/share-video/shares/read"
    share_code = chosen.share_code
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as guest:
        shared = await guest.post(shared_read, json={"code": share_code})
        assert shared.status_code == 200, shared.text
        assert shared.json()["video_url"] == f"/api/mobile/memoir/share-video/shares/{share_code}/video"
        assert shared.json()["poster_url"] == f"{shared.json()['video_url']}?variant=poster"
        assert shared.json()["end_frame_url"] == f"{shared.json()['video_url']}?variant=end"
        assert shared.json()["book_share_token"] is None
        assert (await guest.get(shared.json()["video_url"])).content == chosen.id.encode()
        # The player streams by byte ranges instead of downloading the whole clip first.
        ranged = await guest.get(shared.json()["video_url"], headers={"Range": "bytes=2-5"})
        assert ranged.status_code == 206
        assert ranged.content == chosen.id.encode()[2:6]
        assert ranged.headers["content-range"] == f"bytes 2-5/{len(chosen.id)}"
        unsatisfiable = await guest.get(shared.json()["video_url"], headers={"Range": "bytes=999-"})
        assert unsatisfiable.status_code == 416
        assert (await guest.post(shared_read, json={"code": "0" * 20})).status_code == 404
    url = f"/api/mobile/memoir/share-video/jobs/{chosen.id}"
    for body in (None, {}, {"confirm": False}):
        response = await client.request("DELETE", url, json=body)
        assert response.status_code == 422
        assert share_video_jobs.share_video_file_path(chosen).is_file()
        assert (await client.get(url)).status_code == 200

    response = await client.request("DELETE", url, json={"confirm": True})
    assert response.status_code == 204, response.text
    assert not share_video_jobs.share_video_file_path(chosen).exists()
    assert not share_video_jobs.share_video_file_path(chosen).with_suffix(".thumbnail.jpg").exists()
    assert not share_video_jobs.share_video_file_path(chosen).with_suffix(".card.jpg").exists()
    assert not share_video_jobs.share_video_file_path(chosen).with_suffix(".end.jpg").exists()
    assert not share_video_jobs.share_video_file_path(chosen).with_suffix(".poster.jpg").exists()
    assert (await client.get(url)).status_code == 404
    assert (await client.get(f"{url}/download")).status_code == 404
    assert (await client.get(f"/api/mobile/memoir/share-video/jobs/{retained.id}/download")).content == b"retained"
    # Deleting the video takes back every card and printed code that opened it.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as guest:
        assert (await guest.post(shared_read, json={"code": share_code})).status_code == 404
        assert (await guest.get(f"/api/mobile/memoir/share-video/shares/{share_code}/video")).status_code == 404
    library = await client.get(f"/api/mobile/memoir/projects/{chosen.project_id}/share-video/jobs")
    assert [item["id"] for item in library.json()["items"]] == [retained.id]
    await test_db.refresh(asset)
    await test_db.refresh(candidate)
    assert asset.status == "ready" and asset.share_video_job_id is None
    assert asset.source_utterance_id == chosen.utterance_id and asset.source_quote == chosen.selected_text
    assert image_path.read_bytes() == b"expensive illustration"
    assert candidate.video_job_id is None
    transcript = await client.get(f"/api/mobile/memoir/voice-transcripts/{chosen.utterance_id}")
    assert transcript.status_code == 200
    assert transcript.json()["audio_file_id"] == created["audio_file_id"]
    assert await test_db.scalar(select(MemoirShareVideoOutput).where(MemoirShareVideoOutput.job_id == chosen.id)) is None


async def test_video_delete_rejects_foreign_owner_and_unfinished_job(client, test_db):
    created = await create_test_voice_transcript(client, test_db)
    job = await _add_video_job(
        test_db, created, job_id="unfinished", status=MemoirShareVideoJobStatus.RUNNING,
        created_at=utcnow_naive(), with_output=False,
    )
    await test_db.commit()
    url = f"/api/mobile/memoir/share-video/jobs/{job.id}"
    assert (await client.request("DELETE", url, json={"confirm": True})).status_code == 409
    assert (await client.get(f"{url}/download?variant=thumbnail")).status_code == 409
    # A failed attempt can be cleared from the library.
    failed = await _add_video_job(
        test_db, created, job_id="failed", status=MemoirShareVideoJobStatus.FAILED,
        created_at=utcnow_naive(), with_output=False,
    )
    await test_db.commit()
    failed_url = f"/api/mobile/memoir/share-video/jobs/{failed.id}"
    assert (await client.request("DELETE", failed_url, json={"confirm": True})).status_code == 204
    assert (await client.get(failed_url)).status_code == 404
    other = User(email="other-video-owner@example.com", password_hash="unused", display_name="其他人")
    test_db.add(other)
    await test_db.flush()
    project = await test_db.get(Project, job.project_id)
    project.user_id = other.id
    job.user_id = other.id
    await test_db.commit()
    assert (await client.request("DELETE", url, json={"confirm": True})).status_code == 404
    assert (await client.get(f"{url}/download?variant=thumbnail")).status_code == 404
    assert await test_db.get(MemoirMaterialShareVideo, job.id) is not None


async def test_video_file_survives_a_failed_database_delete(client, test_db, tmp_path, monkeypatch):
    monkeypatch.setattr(share_video_jobs, "MEMOIR_SHARE_VIDEO_DIR", tmp_path)
    created = await create_test_voice_transcript(client, test_db)
    job = await _add_video_job(
        test_db, created, job_id="retained-on-error", status=MemoirShareVideoJobStatus.SUCCEEDED,
        created_at=utcnow_naive(),
    )
    await test_db.commit()
    job_id, user_id = job.id, job.user_id
    path = share_video_jobs.share_video_file_path(job)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"valuable video")

    async def fail_commit():
        raise RuntimeError("database unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(test_db, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="database unavailable"):
            await share_video_jobs.delete_share_video_job(test_db, job_id, user_id)
    await test_db.rollback()
    assert path.read_bytes() == b"valuable video"
    assert await test_db.get(MemoirMaterialShareVideo, job_id) is not None
