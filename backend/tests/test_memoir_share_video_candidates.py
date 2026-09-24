from types import SimpleNamespace

from sqlalchemy import select

from app.clock import utcnow_naive
from app.domain.memoir.types import MemoirEditingJobStatus, MemoirEditingJobType
from app.models.memoir import AudioFile, MemoirEditingJob, MemoirShareVideoCandidate, MemoirVoiceUtterance
from app.services.memoir import share_video_candidates
from app.services.memoir.share_video_jobs import fallback_share_video_title
from app.services.memoir.editing_job_handlers import register_memoir_editing_job_handlers
from tests.memoir_helpers import create_test_voice_transcript, run_test_editing_job


STORY = (
    "我小时候在村口等父亲回家，他总会从包里拿出一颗糖。"
    "有一年冬天雪很大，我一路跑过去，鞋都湿透了，他把围巾围在我脖子上。"
)


async def _stopped_story(client, test_db):
    created = await create_test_voice_transcript(client, test_db, text=STORY)
    utterance = await test_db.get(MemoirVoiceUtterance, created["utterance_id"])
    utterance.end_ms = 32_000
    audio_file = await test_db.get(AudioFile, created["audio_file_id"])
    audio_file.duration_seconds = 32
    utterance.provider_payload_json = {
        "payload": {
            "result": {
                "utterances": [
                    {"text": STORY[: STORY.index("。") + 1], "start_time": 0, "end_time": 15_500},
                    {"text": STORY[STORY.index("。") + 1 :], "start_time": 15_500, "end_time": 32_000},
                ]
            }
        }
    }
    await test_db.commit()
    session = created["session"]
    stopped = await client.post(f"/api/mobile/memoir/voice-sessions/{session['id']}/stop", json={})
    assert stopped.status_code == 200, stopped.text
    job = await test_db.scalar(select(MemoirEditingJob).where(
        MemoirEditingJob.job_type == MemoirEditingJobType.EVALUATE_SHARE_VIDEO_CANDIDATE.value,
        MemoirEditingJob.dedupe_key == f"share_video_candidate:{session['id']}",
    ))
    assert job is not None
    return created, job.id


async def test_completed_narration_can_be_accepted_for_video(client, test_db, monkeypatch):
    created, job_id = await _stopped_story(client, test_db)

    async def choose_first(windows, *, ai_model):
        assert windows and 15_000 <= windows[0].duration_ms <= 45_000
        return 0

    monkeypatch.setattr(share_video_candidates, "_choose_window", choose_first)
    register_memoir_editing_job_handlers()
    assert await run_test_editing_job(test_db, job_id)

    project_id = created["session"]["project_id"]
    url = f"/api/mobile/memoir/projects/{project_id}/share-video-candidate"
    latest = await client.get(url)
    assert latest.status_code == 200
    candidate = latest.json()["candidate"]
    assert candidate["status"] == "ready"
    assert candidate["utterance_id"] == created["utterance_id"]
    assert candidate["duration_ms"] >= 15_000
    assert candidate["selected_text"] in STORY

    create_url = f"/api/mobile/memoir/projects/{project_id}/share-video-candidates/{candidate['id']}/create-video"
    created_job = await client.post(create_url, json={"background_source": "classic"})
    assert created_job.status_code == 201, created_job.text
    assert created_job.json()["selected_text"] == candidate["selected_text"]
    assert created_job.json()["title"] == fallback_share_video_title(candidate["selected_text"])
    assert created_job.json()["status"] == "pending"
    repeated = await client.post(create_url, json={"background_source": "classic"})
    assert repeated.status_code == 201
    assert repeated.json()["id"] == created_job.json()["id"]
    assert (await client.get(url)).json()["candidate"]["status"] == "accepted"


async def test_model_can_decline_without_failing_candidate_job(client, test_db, monkeypatch):
    created, job_id = await _stopped_story(client, test_db)

    async def choose_with_model(client, system_prompt, user_prompt, *, output_model, **kwargs):
        assert '{"candidate_id":1}' in system_prompt
        assert '{"candidate_id":null}' in system_prompt
        return SimpleNamespace(value=output_model.model_validate({"candidate_id": None}))

    monkeypatch.setattr(share_video_candidates, "get_ai_service", lambda model: object())
    monkeypatch.setattr(share_video_candidates, "generate_structured_with_client", choose_with_model)
    register_memoir_editing_job_handlers()
    assert await run_test_editing_job(test_db, job_id)

    candidate = await test_db.scalar(select(MemoirShareVideoCandidate).where(
        MemoirShareVideoCandidate.voice_session_id == created["session"]["id"],
    ))
    assert candidate is not None
    assert candidate.status == "none"
    assert candidate.reason_code == "model_declined"


async def test_recommendation_is_hidden_after_transcript_edit(client, test_db, monkeypatch):
    created, job_id = await _stopped_story(client, test_db)

    async def choose_first(windows, *, ai_model):
        return 0

    monkeypatch.setattr(share_video_candidates, "_choose_window", choose_first)
    register_memoir_editing_job_handlers()
    assert await run_test_editing_job(test_db, job_id)
    project_id = created["session"]["project_id"]
    url = f"/api/mobile/memoir/projects/{project_id}/share-video-candidate"
    candidate_id = (await client.get(url)).json()["candidate"]["id"]

    changed = await client.patch(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}",
        json={"transcript_edited": STORY + "后来我们还讲起过这件事。"},
    )
    assert changed.status_code == 200, changed.text
    assert (await client.get(url)).json()["candidate"] is None
    candidate = await test_db.get(MemoirShareVideoCandidate, candidate_id)
    await test_db.refresh(candidate)
    assert candidate.status == "stale"
    rejected = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/share-video-candidates/{candidate_id}/create-video",
        json={},
    )
    assert rejected.status_code == 409


async def test_short_or_private_narration_does_not_prompt(client, test_db):
    created = await create_test_voice_transcript(
        client, test_db, text="我的手机号是13800138000，请不要告诉别人。",
    )
    session = created["session"]
    stopped = await client.post(f"/api/mobile/memoir/voice-sessions/{session['id']}/stop", json={})
    assert stopped.status_code == 200
    job_id = await test_db.scalar(select(MemoirEditingJob.id).where(
        MemoirEditingJob.dedupe_key == f"share_video_candidate:{session['id']}",
    ))
    register_memoir_editing_job_handlers()
    assert await run_test_editing_job(test_db, job_id)
    project_id = session["project_id"]
    response = await client.get(f"/api/mobile/memoir/projects/{project_id}/share-video-candidate")
    assert response.status_code == 200
    assert response.json()["candidate"] is None
    stored = await test_db.scalar(select(MemoirShareVideoCandidate).where(
        MemoirShareVideoCandidate.voice_session_id == session["id"],
    ))
    assert stored.status == "none"
    assert stored.reason_code == "no_eligible_window"


async def test_pending_correction_delays_candidate_evaluation(client, test_db):
    created, job_id = await _stopped_story(client, test_db)
    test_db.add(MemoirEditingJob(
        project_id=created["session"]["project_id"],
        job_type=MemoirEditingJobType.CORRECT_TRANSCRIPT.value,
        status=MemoirEditingJobStatus.PENDING.value,
        run_after=utcnow_naive(),
        dedupe_key=f"correct_transcript:{created['utterance_id']}",
    ))
    await test_db.commit()
    register_memoir_editing_job_handlers()
    assert not await run_test_editing_job(test_db, job_id)
    job = await test_db.get(MemoirEditingJob, job_id)
    await test_db.refresh(job)
    assert job.status == MemoirEditingJobStatus.PENDING.value
    assert job.result_json["stage"] == "waiting_for_transcripts"
