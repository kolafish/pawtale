import base64
import json

import httpx
import pytest

from app.services.asr import ASRAudioFormat, StepFunASRError, StepFunASRService


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.stepfun.test")


async def test_stepfun_asr_builds_request_and_parses_done_event():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        captured["payload"] = json.loads(request.content.decode("utf-8"))
        body = "\n\n".join([
            'data: {"type":"transcript.text.delta","delta":"外婆"}',
            'data: {"type":"transcript.text.delta","delta":"家的院子"}',
            (
                'data: {"type":"transcript.text.done","text":"外婆家的院子",'
                '"usage":{"type":"realtime_asr","total_tokens":3}}'
            ),
        ])
        return httpx.Response(200, text=f"{body}\n\n")

    service = StepFunASRService(
        api_key="test-key",
        base_url="https://api.stepfun.test",
        model="stepaudio-3-asr-max",
    )

    async with _client(handler) as client:
        result = await service.transcribe(
            b"audio",
            ASRAudioFormat(type="mp3"),
            hotwords=["外婆"],
            client=client,
        )

    assert result.text == "外婆家的院子"
    assert result.deltas == ("外婆", "家的院子")
    assert result.usage == {"type": "realtime_asr", "total_tokens": 3}
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["headers"]["Accept"] == "text/event-stream"
    payload = captured["payload"]
    assert payload["audio"]["data"] == base64.b64encode(b"audio").decode("ascii")
    transcription = payload["audio"]["input"]["transcription"]
    assert transcription["model"] == "stepaudio-3-asr-max"
    assert transcription["language"] == "zh"
    assert transcription["enable_itn"] is True
    assert transcription["enable_timestamp"] is False
    assert transcription["hotwords"] == ["外婆"]
    assert payload["audio"]["input"]["format"] == {"type": "mp3"}


async def test_stepfun_asr_uses_deltas_when_done_text_is_absent():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                "event: transcript.text.delta\n"
                'data: {"delta":"童年"}\n\n'
                "event: transcript.text.delta\n"
                'data: {"delta":"记忆"}\n\n'
            ),
        )

    service = StepFunASRService(api_key="test-key", base_url="https://api.stepfun.test")

    async with _client(handler) as client:
        result = await service.transcribe(b"audio", ASRAudioFormat(type="wav"), client=client)

    assert result.text == "童年记忆"
    assert result.deltas == ("童年", "记忆")


async def test_stepfun_asr_raises_for_error_event():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text='data: {"type":"error","message":"bad audio"}\n\n')

    service = StepFunASRService(api_key="test-key", base_url="https://api.stepfun.test")

    async with _client(handler) as client:
        with pytest.raises(StepFunASRError, match="bad audio"):
            await service.transcribe(b"audio", ASRAudioFormat(type="mp3"), client=client)


async def test_stepfun_asr_raises_for_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    service = StepFunASRService(api_key="test-key", base_url="https://api.stepfun.test")

    async with _client(handler) as client:
        with pytest.raises(StepFunASRError, match="HTTP 401"):
            await service.transcribe(b"audio", ASRAudioFormat(type="mp3"), client=client)


async def test_stepfun_asr_requires_api_key():
    service = StepFunASRService(api_key="")

    with pytest.raises(StepFunASRError, match="API key"):
        await service.transcribe(b"audio", ASRAudioFormat(type="mp3"))


async def test_stepfun_asr_rejects_empty_audio():
    service = StepFunASRService(api_key="test-key")

    with pytest.raises(ValueError, match="音频内容不能为空"):
        await service.transcribe(b"", ASRAudioFormat(type="mp3"))


def test_pcm_format_requires_metadata():
    with pytest.raises(ValueError, match="PCM"):
        ASRAudioFormat(type="pcm").to_payload()

    assert ASRAudioFormat(
        type="pcm",
        codec="pcm_s16le",
        rate=16000,
        bits=16,
        channel=1,
    ).to_payload() == {
        "type": "pcm",
        "codec": "pcm_s16le",
        "rate": 16000,
        "bits": 16,
        "channel": 1,
    }


def test_audio_format_rejects_unknown_type():
    with pytest.raises(ValueError, match="仅支持"):
        ASRAudioFormat(type="aac").to_payload()
