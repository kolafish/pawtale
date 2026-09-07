import pytest

from app.models.memoir import AudioFile, MemoirVoiceUtterance
from app.services.memoir.share_video_alignment import build_share_video_alignment


@pytest.mark.parametrize("strategy", ["words", "utterances"])
@pytest.mark.parametrize("start", [
    {"start_time": 0},
    {"start_time": 0, "start_ms": 99},
    {"start_time": None, "start_ms": 0},
    {"start_ms": 0},
])
def test_alignment_preserves_provider_zero_timestamp(strategy, start):
    first = {"text": "你", **start, "end_time": 200}
    second = {"text": "好", "start_time": 200, "end_time": 700}
    utterances = [{"words": [first, second]}] if strategy == "words" else [first, second]
    utterance = MemoirVoiceUtterance(
        transcript_raw="你好", transcript_edited="", end_ms=700, audio_file_id="audio",
        audio_file=AudioFile(size_bytes=22400, duration_seconds=0.7, mime_type="audio/pcm"),
        provider_payload_json={"result": {"utterances": utterances}},
    )

    alignment = build_share_video_alignment(utterance)

    assert alignment.strategy == f"provider_{strategy}"
    assert alignment.char_timings == [
        {"start_ms": 0, "end_ms": 200}, {"start_ms": 200, "end_ms": 700},
    ]
    assert alignment.confidence == 0.95


@pytest.mark.parametrize("strategy", ["words", "utterances"])
def test_zero_end_time_does_not_fall_back_to_another_timestamp(strategy):
    item = {"text": "你", "start_time": 0, "end_time": 0, "start_ms": 0, "end_ms": 700}
    utterance = MemoirVoiceUtterance(
        transcript_raw="你", end_ms=700, audio_file_id="audio",
        audio_file=AudioFile(size_bytes=22400, duration_seconds=0.7, mime_type="audio/pcm"),
        provider_payload_json={"utterances": [{"words": [item]}] if strategy == "words" else [item]},
    )

    assert build_share_video_alignment(utterance).strategy == "linear_estimate"
