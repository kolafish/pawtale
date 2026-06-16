import httpx
import pytest

from app.config import Settings
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
