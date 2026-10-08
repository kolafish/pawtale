import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.clock import utcnow_naive
from app.domain.memoir.types import MemoirEditingJobStatus, MemoirEditingJobType
from app.models.memoir import (
    AudioFile, MemoirEditingJob, MemoirMaterialShareVideo, MemoirShareVideoCandidate,
    MemoirShareVideoOutput, MemoirVoiceUtterance,
)
from app.services.memoir import share_video_candidates
from app.services.memoir.share_video_jobs import fallback_share_video_title
from app.services.memoir.editing_job_handlers import register_memoir_editing_job_handlers
from tests.memoir_helpers import create_test_voice_transcript, run_test_editing_job


STORY = (
    "我小时候在村口等父亲回家，他总会从包里拿出一颗糖。"
    "有一年冬天雪很大，我一路跑过去，鞋都湿透了，他把围巾围在我脖子上。"
)


def _model_picks(monkeypatch, **overrides):
    """Answer the selection prompt with the whole first narration unless told otherwise."""
    prompts = []

    async def generate(client, system_prompt, user_prompt, *, output_model, **kwargs):
        prompts.append(json.loads(user_prompt))
        sentences = prompts[-1]["narrations"][0]["sentences"]
        pick = {
            "start_sentence_id": sentences[0]["id"], "end_sentence_id": sentences[-1]["id"],
            "hook_sentence_id": None, "emotion": 4, "visual": 4, "standalone": 4,
            "reason": "讲了雪天父亲把围巾给我", **overrides,
        }
        return SimpleNamespace(value=output_model.model_validate(pick))

    monkeypatch.setattr(share_video_candidates, "get_ai_service", lambda model: object())
    monkeypatch.setattr(share_video_candidates, "generate_structured_with_client", generate)
    return prompts


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
    prompts = _model_picks(monkeypatch)
    register_memoir_editing_job_handlers()
    assert await run_test_editing_job(test_db, job_id)
    # The model reads the narration sentence by sentence, not pre-cut windows.
    assert "".join(item["text"] for item in prompts[0]["narrations"][0]["sentences"]) == STORY

    project_id = created["session"]["project_id"]
    url = f"/api/mobile/memoir/projects/{project_id}/share-video-candidate"
    latest = await client.get(url)
    assert latest.status_code == 200
    candidate = latest.json()["candidate"]
    assert candidate["status"] == "ready"
    assert candidate["utterance_id"] == created["utterance_id"]
    assert candidate["duration_ms"] >= 15_000
    assert candidate["selected_text"] == STORY
    assert candidate["reason"] == "讲了雪天父亲把围巾给我"

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

    # The author can open with another short line from the clip, but not with words it never says.
    rejected = await client.post(create_url, json={"background_source": "classic", "hook_text": "我从没说过这句"})
    assert rejected.status_code == 422
    chosen = await client.post(create_url, json={"background_source": "classic", "hook_text": "鞋都湿透了"})
    assert chosen.status_code == 201, chosen.text
    assert chosen.json()["id"] != created_job.json()["id"]
    chosen_job = await test_db.get(MemoirMaterialShareVideo, chosen.json()["id"])
    assert chosen_job.metadata_json["hook_text"] == "鞋都湿透了"


@pytest.mark.parametrize("accept_original", [True, False])
async def test_recommendation_follows_latest_manual_video_for_its_source_range(
    client, test_db, monkeypatch, accept_original,
):
    created, evaluation_id = await _stopped_story(client, test_db)
    _model_picks(monkeypatch)
    register_memoir_editing_job_handlers()
    assert await run_test_editing_job(test_db, evaluation_id)
    project_id = created["session"]["project_id"]
    url = f"/api/mobile/memoir/projects/{project_id}/share-video-candidate"
    candidate = (await client.get(url)).json()["candidate"]
    if accept_original:
        original = await client.post(
            f"/api/mobile/memoir/projects/{project_id}/share-video-candidates/{candidate['id']}/create-video",
            json={"background_source": "classic"},
        )
        old_job = await test_db.get(MemoirMaterialShareVideo, original.json()["id"])
        old_job.status = "failed"
        old_job.error_message = "source evidence rejected"
        await test_db.commit()

    create_url = f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/share-video/jobs"
    remade = await client.post(create_url, json={
        "start_char": candidate["start_char"], "end_char": candidate["end_char"],
        "background_source": "classic",
    })
    assert remade.status_code == 201, remade.text
    new_id = remade.json()["id"]
    current = (await client.get(url)).json()["candidate"]
    assert current["status"] == "accepted"
    assert current["video_job_id"] == new_id
    job = await test_db.get(MemoirMaterialShareVideo, new_id)
    for status in ("running", "succeeded"):
        job.status = status
        if status == "succeeded":
            job.output = MemoirShareVideoOutput(
                job_id=new_id, project_id=project_id,
                storage_path=f"memoir/share-videos/{job.user_id}/{new_id}.mp4",
                mime_type="video/mp4", size_bytes=1024,
            )
        await test_db.commit()
        assert (await client.get(url)).json()["candidate"]["video_job_id"] == new_id
        assert (await client.get(f"/api/mobile/memoir/share-video/jobs/{new_id}")).json()["status"] == status

    # A newer task for another range must not replace the recommendation's video.
    first_sentence_end = STORY.index("。") + 1
    assert first_sentence_end < candidate["end_char"]
    different_range = await client.post(create_url, json={
        "start_char": 0, "end_char": first_sentence_end, "background_source": "classic",
    })
    assert different_range.status_code == 201, different_range.text
    assert different_range.json()["id"] != new_id
    assert (await client.get(url)).json()["candidate"]["video_job_id"] == new_id
    if accept_original:
        await test_db.refresh(old_job)
        assert old_job.status == "failed"


@pytest.mark.parametrize(("pick", "reason_code"), [
    ({"start_sentence_id": None}, "model_declined"),
    ({"emotion": 2, "visual": 5, "standalone": 5}, "low_score"),
    ({"emotion": 3, "visual": 4, "standalone": 3}, "low_score"),
])
async def test_weak_or_declined_picks_are_not_recommended(client, test_db, monkeypatch, pick, reason_code):
    created, job_id = await _stopped_story(client, test_db)
    _model_picks(monkeypatch, **pick)
    register_memoir_editing_job_handlers()
    assert await run_test_editing_job(test_db, job_id)

    candidate = await test_db.scalar(select(MemoirShareVideoCandidate).where(
        MemoirShareVideoCandidate.voice_session_id == created["session"]["id"],
    ))
    assert candidate is not None
    assert candidate.status == "none"
    assert candidate.reason_code == reason_code
    if reason_code == "low_score":
        # Scores stay with the judged range so the threshold can be revisited.
        assert (candidate.emotion_score, candidate.visual_score, candidate.standalone_score) == (
            pick["emotion"], pick["visual"], pick["standalone"],
        )
        assert candidate.selected_text == STORY
    project_id = created["session"]["project_id"]
    latest = await client.get(f"/api/mobile/memoir/projects/{project_id}/share-video-candidate")
    assert latest.json()["candidate"] is None


async def test_recommendation_is_hidden_after_transcript_edit(client, test_db, monkeypatch):
    created, job_id = await _stopped_story(client, test_db)
    _model_picks(monkeypatch)
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


def _sentences(*parts):
    """(utterance, text, seconds) parts laid out back to back within each utterance."""
    sentences, offsets = [], {}
    texts = {}
    for utterance, text, _seconds in parts:
        texts[utterance] = texts.get(utterance, "") + text
    for index, (utterance, text, seconds) in enumerate(parts, start=1):
        char, ms = offsets.get(utterance, (0, 0))
        sentences.append(share_video_candidates.CandidateSentence(
            id=index, narration=1 if utterance == "a" else 2, utterance_id=utterance, audio_file_id="audio",
            transcript_version_id="version", text_hash="hash", transcript_text=texts[utterance],
            start_char=char, end_char=char + len(text), start_ms=ms, end_ms=ms + seconds * 1000,
        ))
        offsets[utterance] = (char + len(text), ms + seconds * 1000)
    return sentences


def test_model_range_must_stay_in_one_narration_and_quote_its_own_hook():
    sentences = _sentences(
        ("a", "那年中秋父亲没有回来。", 4),
        ("a", "母亲在门口一直等到半夜，桌上的月饼一块都没有动过，我们谁也不敢先睡。", 12),
        ("a", "后来才知道他在路上救了一个落水的孩子。", 6),
        ("b", "这件事我记了一辈子，每年中秋都会想起那个晚上。", 8),
    )
    pick = share_video_candidates._CandidatePick

    window, code = share_video_candidates.window_from_pick(sentences, pick(
        start_sentence_id=1, end_sentence_id=3, hook_sentence_id=1, emotion=5, visual=4, standalone=4,
        reason="  讲了父亲没回来的\n那个中秋 ",
    ))
    assert code == "model_selected"
    assert window.selected_text.startswith("那年中秋") and window.selected_text.endswith("孩子。")
    assert window.hook_text == "那年中秋父亲没有回来。"
    assert window.reason == "讲了父亲没回来的 那个中秋"

    outside_hook, _ = share_video_candidates.window_from_pick(sentences, pick(
        start_sentence_id=2, end_sentence_id=3, hook_sentence_id=1, emotion=4, visual=4, standalone=4,
    ))
    assert outside_hook.hook_text == ""
    assert share_video_candidates.window_from_pick(sentences, pick(
        start_sentence_id=3, end_sentence_id=4, emotion=5, visual=5, standalone=5,
    )) == (None, "invalid_range")
    assert share_video_candidates.window_from_pick(sentences, pick(
        start_sentence_id=1, end_sentence_id=1, emotion=5, visual=5, standalone=5,
    )) == (None, "out_of_bounds")
