from datetime import timedelta
from io import BytesIO
import subprocess
from types import SimpleNamespace

from PIL import Image
import pytest
from sqlalchemy import select

from app.clock import utcnow_naive
from app.domain.memoir.types import MemoirShareVideoJobStatus
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
    assert response.json() == {"items": [], "total": 0, "limit": 20, "offset": 0}


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


async def test_remake_preserves_completed_file_and_lists_each_style(client, test_db, tmp_path, monkeypatch):
    monkeypatch.setattr(share_video_jobs, "MEMOIR_SHARE_VIDEO_DIR", tmp_path)
    monkeypatch.setattr(share_video_jobs, "get_settings", lambda: SimpleNamespace(
        memoir_share_video_ai_background_enabled=False, volc_ark_api_key="",
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
        json={"start_char": 0, "end_char": len(text), "video_style": "story_animation"},
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
        old.id: "illustrated", new.id: "story_animation",
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
    assert (await client.get(url)).status_code == 404
    assert (await client.get(f"{url}/download")).status_code == 404
    assert (await client.get(f"/api/mobile/memoir/share-video/jobs/{retained.id}/download")).content == b"retained"
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
