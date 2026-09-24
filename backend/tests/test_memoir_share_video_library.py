from datetime import timedelta

from app.clock import utcnow_naive
from app.domain.memoir.types import MemoirShareVideoJobStatus
from app.models.memoir import (
    MemoirMaterialShareVideo,
    MemoirShareVideoOutput,
    MemoirVoiceUtterance,
)
from app.models.projects import Project
from app.models.users import User
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
        metadata_json={"title": f"{job_id} 的回忆"},
        created_at=created_at,
        completed_at=completed_at,
    )
    db.add(job)
    await db.flush()
    if with_output:
        db.add(MemoirShareVideoOutput(
            job_id=job.id,
            project_id=job.project_id,
            storage_path=f"memoir/share-videos/{job.user_id}/{job.id}.mp4",
            mime_type="video/mp4",
            size_bytes=1024,
        ))
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
