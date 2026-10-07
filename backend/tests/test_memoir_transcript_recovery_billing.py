from types import SimpleNamespace
import wave

import pytest
from fastapi import HTTPException

from app.application_errors import InsufficientAICreditError
from app.services.asr.streaming import StreamingASREvent
from app.services.memoir import voice_transcript_recovery as recovery
from app.services.memoir.audio_files import audio_file_duration_seconds


def _recoverable_audio(monkeypatch, tmp_path, *, mime_type="audio/pcm", suffix=".pcm"):
    path = tmp_path / f"unfinalized{suffix}"
    path.write_bytes(b"\x00" * 32000)
    utterance = SimpleNamespace(
        id="utterance-1",
        audio_file=SimpleNamespace(duration_seconds=None, mime_type=mime_type),
    )

    async def load_utterance(*_args, **_kwargs):
        return utterance

    monkeypatch.setattr(recovery, "_load_recoverable_utterance", load_utterance)
    monkeypatch.setattr(recovery, "current_utterance_text", lambda _utterance: "")
    monkeypatch.setattr(recovery, "audio_file_path", lambda _audio_file: path)
    return utterance, path


def test_stored_duration_supports_compressed_historical_audio(tmp_path):
    path = tmp_path / "historical.mp3"
    path.write_bytes(b"historical-audio")
    audio_file = SimpleNamespace(duration_seconds=12, mime_type="audio/mpeg")

    assert audio_file_duration_seconds(audio_file, path) == 12.0


async def test_recovery_checks_credit_before_requesting_asr(monkeypatch, tmp_path):
    _recoverable_audio(monkeypatch, tmp_path)

    async def no_credit(_user_id):
        raise InsufficientAICreditError("AI 额度已用尽")

    async def should_not_run(*_args, **_kwargs):
        raise AssertionError("ASR started without AI credit")

    monkeypatch.setattr(recovery, "assert_ai_credit_available", no_credit)
    monkeypatch.setattr(recovery, "transcribe_audio_path_for_recovery", should_not_run)
    monkeypatch.setattr(recovery, "record_volc_streaming_asr_usage", should_not_run)

    with pytest.raises(InsufficientAICreditError):
        await recovery.recover_voice_transcript_from_audio(
            object(), "utterance-1", "speaker", asr_client_factory=lambda: object(),
        )


async def test_recovery_records_asr_duration_when_transcription_succeeds(monkeypatch, tmp_path):
    _recoverable_audio(monkeypatch, tmp_path)
    events = []

    async def allow_credit(user_id):
        events.append(("credit", user_id))

    async def transcribe(_path, *, asr_client_factory):
        events.append(("asr",))
        return StreamingASREvent(type="final", text="外婆讲的故事")

    async def record_usage(**kwargs):
        events.append(("usage", kwargs))

    async def apply_transcript(*_args, **_kwargs):
        events.append(("persist",))

    async def get_transcript(*_args, **_kwargs):
        return "recovered"

    monkeypatch.setattr(recovery, "assert_ai_credit_available", allow_credit)
    monkeypatch.setattr(recovery, "transcribe_audio_path_for_recovery", transcribe)
    monkeypatch.setattr(recovery, "record_volc_streaming_asr_usage", record_usage)
    monkeypatch.setattr(recovery, "_apply_recovered_transcript", apply_transcript)
    monkeypatch.setattr(recovery, "get_voice_transcript", get_transcript)

    result = await recovery.recover_voice_transcript_from_audio(
        object(), "utterance-1", "speaker", asr_client_factory=lambda: object(),
    )

    assert result == "recovered"
    assert [event[0] for event in events] == ["credit", "asr", "usage", "persist"]
    assert events[2][1] == {"audio_duration_seconds": 1.0, "status": "succeeded"}


async def test_recovery_records_failed_asr_attempt(monkeypatch, tmp_path):
    _recoverable_audio(monkeypatch, tmp_path)
    usage = []

    async def allow_credit(_user_id):
        return None

    async def fail_asr(_path, *, asr_client_factory):
        raise HTTPException(status_code=502, detail="语音识别服务暂时不可用")

    async def record_usage(**kwargs):
        usage.append(kwargs)

    monkeypatch.setattr(recovery, "assert_ai_credit_available", allow_credit)
    monkeypatch.setattr(recovery, "transcribe_audio_path_for_recovery", fail_asr)
    monkeypatch.setattr(recovery, "record_volc_streaming_asr_usage", record_usage)

    with pytest.raises(HTTPException) as exc_info:
        await recovery.recover_voice_transcript_from_audio(
            object(), "utterance-1", "speaker", asr_client_factory=lambda: object(),
        )

    assert exc_info.value.status_code == 502
    assert usage == [{
        "audio_duration_seconds": 1.0,
        "status": "failed",
        "error_type": "HTTPException",
    }]


async def test_recovery_measures_wav_duration_when_metadata_is_missing(monkeypatch, tmp_path):
    _utterance, path = _recoverable_audio(
        monkeypatch, tmp_path, mime_type="audio/wav", suffix=".wav",
    )
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00" * 32000)
    usage = []

    async def allow_credit(_user_id):
        return None

    async def transcribe(_path, *, asr_client_factory):
        return StreamingASREvent(type="final", text="外婆家的院子")

    async def record_usage(**kwargs):
        usage.append(kwargs)

    async def no_op(*_args, **_kwargs):
        return None

    monkeypatch.setattr(recovery, "assert_ai_credit_available", allow_credit)
    monkeypatch.setattr(recovery, "transcribe_audio_path_for_recovery", transcribe)
    monkeypatch.setattr(recovery, "record_volc_streaming_asr_usage", record_usage)
    monkeypatch.setattr(recovery, "_apply_recovered_transcript", no_op)
    monkeypatch.setattr(recovery, "get_voice_transcript", no_op)

    await recovery.recover_voice_transcript_from_audio(
        object(), "utterance-1", "speaker", asr_client_factory=lambda: object(),
    )

    assert usage == [{"audio_duration_seconds": 1.0, "status": "succeeded"}]


async def test_recovery_rejects_unmeasurable_audio_before_asr(monkeypatch, tmp_path):
    _recoverable_audio(monkeypatch, tmp_path, mime_type="audio/mpeg", suffix=".mp3")

    async def should_not_run(*_args, **_kwargs):
        raise AssertionError("Unmetered ASR started")

    monkeypatch.setattr(recovery, "assert_ai_credit_available", should_not_run)
    monkeypatch.setattr(recovery, "transcribe_audio_path_for_recovery", should_not_run)
    monkeypatch.setattr(recovery, "record_volc_streaming_asr_usage", should_not_run)

    with pytest.raises(HTTPException) as exc_info:
        await recovery.recover_voice_transcript_from_audio(
            object(), "utterance-1", "speaker", asr_client_factory=lambda: object(),
        )

    assert exc_info.value.status_code == 422
