import httpx
import pytest

from app.config import Settings
from app.services.memoir.tts_alignment import ElevenLabsForcedAlignmentClient
from app.services.memoir.voice_tts import (
    OpenAICompatibleTTSClient,
    StepFunTTSClient,
    get_tts_client,
)


def test_get_tts_client_supports_stepfun_provider():
    settings = Settings(
        tts_provider="stepfun",
        tts_api_key="generic-key",
        tts_response_format="mp3",
        stepfun_api_key="stepfun-key",
        stepfun_base_url="https://api.stepfun.ai/v1",
        stepfun_tts_model="stepaudio-2.5-tts",
        stepfun_tts_voice="cixingnansheng",
    )

    client = get_tts_client(settings)

    assert isinstance(client, StepFunTTSClient)
    assert client.provider_name == "stepfun"
    assert client.model_name == "stepaudio-2.5-tts"
    assert client.config.api_key == "stepfun-key"
    assert client.config.voice == "cixingnansheng"
    assert client._endpoint() == "https://api.stepfun.ai/v1/audio/speech"


def test_stepfun_tts_client_can_fallback_to_generic_tts_key():
    settings = Settings(
        tts_provider="stepfun",
        tts_api_key="generic-key",
        stepfun_api_key="",
    )

    client = get_tts_client(settings)

    assert isinstance(client, StepFunTTSClient)
    assert client.config.api_key == "generic-key"


def test_stepfun_tts_client_accepts_host_level_base_url():
    client = get_tts_client(
        Settings(
            tts_provider="stepfun",
            tts_api_key="generic-key",
            stepfun_base_url="https://api.stepfun.com",
        )
    )

    assert isinstance(client, StepFunTTSClient)
    assert client._endpoint() == "https://api.stepfun.com/v1/audio/speech"


@pytest.mark.asyncio
async def test_audio_speech_tts_client_posts_openai_compatible_payload(monkeypatch):
    calls = []

    class FakeAsyncClient:
        def __init__(self, *, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, json, headers):
            calls.append(
                {
                    "url": url,
                    "json": json,
                    "headers": headers,
                    "timeout": self.timeout,
                }
            )
            request = httpx.Request("POST", url)
            return httpx.Response(200, content=b"audio-bytes", request=request)

    monkeypatch.setattr("app.services.memoir.voice_tts.httpx.AsyncClient", FakeAsyncClient)
    client = OpenAICompatibleTTSClient(
        Settings(
            tts_api_key="test-key",
            tts_base_url="https://tts.example.test/v1",
            tts_model="tts-model",
            tts_voice="default-voice",
            tts_response_format="mp3",
            tts_timeout_seconds=12,
        )
    )

    result = await client.synthesize("你想先聊哪一位亲人？", voice="warm")

    assert result.audio == b"audio-bytes"
    assert result.mime_type == "audio/mpeg"
    assert result.extension == ".mp3"
    assert calls == [
        {
            "url": "https://tts.example.test/v1/audio/speech",
            "json": {
                "model": "tts-model",
                "input": "你想先聊哪一位亲人？",
                "voice": "warm",
                "response_format": "mp3",
            },
            "headers": {"Authorization": "Bearer test-key"},
            "timeout": 12,
        }
    ]


@pytest.mark.asyncio
async def test_elevenlabs_forced_alignment_client_posts_audio_and_maps_characters(monkeypatch):
    calls = []

    class FakeAsyncClient:
        def __init__(self, *, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, data, files, headers):
            calls.append(
                {
                    "url": url,
                    "data": data,
                    "files": files,
                    "headers": headers,
                    "timeout": self.timeout,
                }
            )
            request = httpx.Request("POST", url)
            return httpx.Response(
                200,
                json={
                    "characters": [
                        {"text": "你", "start": 0.0, "end": 0.12},
                        {"text": "好", "start": 0.12, "end": 0.28},
                    ],
                    "words": [],
                    "loss": 0.04,
                },
                request=request,
            )

    monkeypatch.setattr("app.services.memoir.tts_alignment.httpx.AsyncClient", FakeAsyncClient)
    client = ElevenLabsForcedAlignmentClient(
        Settings(
            tts_alignment_api_key="align-key",
            tts_alignment_base_url="https://api.elevenlabs.io/v1",
            tts_alignment_timeout_seconds=9,
        )
    )

    result = await client.align("你好", b"audio-bytes", mime_type="audio/mpeg", extension=".mp3")

    assert result.provider == "elevenlabs_forced_alignment"
    assert result.duration_seconds == 0.28
    assert result.loss == 0.04
    assert [segment.text for segment in result.segments] == ["你", "好"]
    assert result.segments[1].start_ms == 120
    assert result.segments[1].end_ms == 280
    assert result.segments[1].text_start == 1
    assert calls == [
        {
            "url": "https://api.elevenlabs.io/v1/forced-alignment",
            "data": {"text": "你好"},
            "files": {"file": ("tts-audio.mp3", b"audio-bytes", "audio/mpeg")},
            "headers": {"xi-api-key": "align-key"},
            "timeout": 9,
        }
    ]
