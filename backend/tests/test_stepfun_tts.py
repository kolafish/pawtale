import base64
import json
from unittest.mock import AsyncMock

from fastapi import HTTPException
import httpx
import pytest

from app.application_errors import InsufficientAICreditError
from app.config import Settings
from app.services import tts_stepfun
from app.services.memoir import book_audio
from app.services.memoir.voice_tts import VolcTTSClient, get_tts_client
from app.services.tts_stepfun import StepFunTTSClient


TEXT = "1998年9月，\n你好。"
# Reduced from the actual Step Mini comparison response, including the
# zero-duration 年 and the original-number spelling in subtitles.
SUBTITLES = [
    {"text": "1998", "start_time": 0, "end_time": 800},
    {"text": "年", "start_time": 800, "end_time": 800},
    {"text": "9", "start_time": 800, "end_time": 1040},
    {"text": "月，", "start_time": 1040, "end_time": 1520},
    {"text": "你", "start_time": 1680, "end_time": 1840},
    {"text": "好。", "start_time": 1840, "end_time": 2160},
]


def events():
    return [
        {"type": "speech.audio.delta", "audio": base64.b64encode(b"mp3-one").decode()},
        {"type": "response.subtitle", "data": {"text": TEXT, "items": SUBTITLES}},
        {"type": "speech.audio.delta", "audio": base64.b64encode(b"mp3-two").decode()},
        {"type": "speech.audio.done", "audio": ""},
    ]


def mock_provider(monkeypatch, stream_events=None, status=200):
    calls = []
    body = "".join("data: " + json.dumps(event, ensure_ascii=False) + "\n\n" for event in (
        stream_events if stream_events is not None else events()
    )) + "data: [DONE]\n\n"

    async def handle(request):
        calls.append(request)
        return httpx.Response(status, text=body, headers={"content-type": "text/event-stream"})

    client_type = httpx.AsyncClient
    monkeypatch.setattr(tts_stepfun.httpx, "AsyncClient", lambda **kwargs: client_type(
        transport=httpx.MockTransport(handle), **kwargs,
    ))
    return calls


async def test_step_mini_maps_real_subtitles_and_audio_and_preserves_speed(monkeypatch):
    calls = mock_provider(monkeypatch)
    client = StepFunTTSClient(Settings(stepfun_api_key="test-key"))
    result = await client.synthesize(TEXT, voice="zh_female_vv_uranus_bigtts", speed_ratio=0.7)
    assert len(calls) == 1
    payload = json.loads(calls[0].content)
    assert str(calls[0].url) == "https://api.stepfun.com/v1/audio/speech"
    assert payload == {
        "model": "step-tts-mini", "input": TEXT, "voice": "wenrounvsheng",
        "response_format": "mp3", "sample_rate": 24000, "speed": 0.7,
        "volume": 1.0, "text_normalization": "standard", "stream_format": "sse", "timestamp": True,
    }
    assert result.audio == b"mp3-onemp3-two"
    assert (result.provider, result.model, result.voice) == ("stepfun", "step-tts-mini", "wenrounvsheng")
    assert result.speed_ratio == 0.7
    assert result.duration_seconds == 2.16
    assert result.extension == ".mp3"
    assert [(s.text, s.start_ms, s.end_ms) for s in result.alignment][:2] == [
        ("1998", 0, 800), ("年", 800, 801),
    ]
    assert all(TEXT[s.text_start:s.text_end] == s.text for s in result.alignment)
    assert result.alignment[-1].text_end == len(TEXT)


@pytest.mark.parametrize("legacy, expected", [
    (None, "wenrounvsheng"),
    ("zh_female_vv_uranus_bigtts", "wenrounvsheng"),
    ("zh_female_xiaohe_uranus_bigtts", "linjiajiejie"),
    ("zh_male_m191_uranus_bigtts", "cixingnansheng"),
    ("cixingnansheng", "cixingnansheng"),
])
def test_narration_resolves_released_client_voices(legacy, expected):
    assert StepFunTTSClient(Settings()).effective_voice(legacy) == expected


def test_only_longform_narration_uses_step_mini(monkeypatch):
    settings = Settings(tts_provider="volc", stepfun_api_key="test-key")
    monkeypatch.setattr(book_audio, "get_settings", lambda: settings)
    assert isinstance(book_audio.get_tts_client(), StepFunTTSClient)
    assert isinstance(get_tts_client(settings), VolcTTSClient)


@pytest.mark.parametrize("failure", ["truncated", "late_error", "bad_base64", "no_subtitles"])
async def test_partial_or_invalid_provider_responses_never_become_completed_audio(monkeypatch, failure):
    stream_events = events()
    if failure == "truncated":
        stream_events.pop()
    elif failure == "late_error":
        stream_events.append({"type": "speech.audio.error", "message": "upstream failed"})
    elif failure == "bad_base64":
        stream_events[0]["audio"] = "!invalid!"
    else:
        stream_events.pop(1)
    calls = mock_provider(monkeypatch, stream_events)
    with pytest.raises(HTTPException) as caught:
        await StepFunTTSClient(Settings(stepfun_api_key="test-key")).synthesize(TEXT)
    assert caught.value.status_code == 503
    assert len(calls) == 1


async def test_normalized_subtitles_and_new_event_types_keep_completed_audio(monkeypatch):
    text = "涨了50%，你好。"
    stream_events = events()
    stream_events.insert(0, {"type": "speech.audio.start", "data": {"session": "s-1"}})
    stream_events[2]["data"] = {"text": text, "items": [
        {"text": "涨了", "start_time": 0, "end_time": 300},
        {"text": "百分之五十，", "start_time": 300, "end_time": 1200},
        {"text": "你好。", "start_time": 1300, "end_time": 1700},
    ]}
    mock_provider(monkeypatch, stream_events)
    result = await StepFunTTSClient(Settings(stepfun_api_key="test-key")).synthesize(text)
    assert result.audio == b"mp3-onemp3-two"
    assert result.duration_seconds == 1.7
    assert [(s.text, s.text_start) for s in result.alignment] == [("涨了", 0), ("你好。", 6)]
    assert all(text[s.text_start:s.text_end] == s.text for s in result.alignment)


async def test_http_failure_is_not_retried(monkeypatch):
    calls = mock_provider(monkeypatch, status=429)
    with pytest.raises(HTTPException) as caught:
        await StepFunTTSClient(Settings(stepfun_api_key="test-key")).synthesize(TEXT)
    assert caught.value.status_code == 503
    assert len(calls) == 1


async def test_credit_is_checked_inside_provider_slot_before_contacting_step(monkeypatch):
    calls = mock_provider(monkeypatch)
    inside_slot = False

    async def run(_name, _limit, operation):
        nonlocal inside_slot
        inside_slot = True
        return await operation()

    async def exhausted():
        assert inside_slot
        raise InsufficientAICreditError("AI 额度已用尽")

    monkeypatch.setattr(tts_stepfun.provider_concurrency_limiter, "run", run)
    monkeypatch.setattr(tts_stepfun, "assert_current_ai_credit_available", exhausted)
    with pytest.raises(InsufficientAICreditError):
        await StepFunTTSClient(Settings(stepfun_api_key="test-key")).synthesize(TEXT)
    assert not calls


@pytest.mark.parametrize("text, voice", [("字" * 1001, None), (" ", None), ("你好", "unknown")])
async def test_invalid_input_is_rejected_without_paid_request(monkeypatch, text, voice):
    calls = mock_provider(monkeypatch)
    with pytest.raises(HTTPException) as caught:
        await StepFunTTSClient(Settings(stepfun_api_key="test-key")).synthesize(text, voice=voice)
    assert caught.value.status_code == 400
    assert not calls


async def test_narration_usage_prices_the_actual_step_result(monkeypatch):
    mock_provider(monkeypatch)
    record = AsyncMock()
    monkeypatch.setattr(book_audio, "record_ai_usage", record)
    monkeypatch.setattr(book_audio, "stage_audio_bytes", AsyncMock(return_value=type("Staged", (), {"audio_file": object()})()))
    result = await book_audio.synthesize_text_audio(
        None, user_id="user", text=TEXT, tts_client=StepFunTTSClient(Settings(stepfun_api_key="test-key")),
    )
    assert result.provider == "stepfun"
    usage = record.call_args.kwargs
    assert usage["model"] == "step-tts-mini"
    assert usage["status"] == "succeeded"
    assert usage["input_characters"] == len(TEXT)
    assert usage["cost"].status == "estimated"
    assert usage["cost"].amount_rmb == 0.00099
