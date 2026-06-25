import base64
from contextlib import asynccontextmanager
import json
import struct

import httpx
import pytest
from fastapi import HTTPException

from app.config import Settings
from app.services.memoir.voice_tts import VolcTTSClient, get_tts_client


def test_get_tts_client_supports_volc_provider():
    settings = Settings(
        tts_provider="volc",
        volc_tts_app_id="app-id",
        volc_tts_access_token="access-token",
        volc_tts_resource_id="seed-tts-2.0",
        volc_tts_speaker="zh_female_vv_uranus_bigtts",
    )

    client = get_tts_client(settings)

    assert isinstance(client, VolcTTSClient)
    assert client.provider_name == "volc_tts"
    assert client.model_name == "seed-tts-2.0"
    assert client.default_voice == "zh_female_vv_uranus_bigtts"


def test_get_tts_client_rejects_disabled_provider():
    with pytest.raises(HTTPException):
        get_tts_client(Settings(tts_provider="disabled"))


@pytest.mark.asyncio
async def test_volc_tts_client_posts_sse_payload_and_maps_subtitle(monkeypatch):
    calls = []
    limiter = RecordingLimiter()
    audio = b"audio-bytes"
    response_body = "\n".join(
        [
            f'data: {json.dumps({"code": 0, "message": "", "data": base64.b64encode(audio).decode()})}',
            'event: 351',
            'data: '
            + json.dumps(
                {
                    "code": 0,
                    "message": "",
                    "data": None,
                    "sentence": {
                        "text": "你好，继续讲。",
                        "words": [
                            {"word": "你", "startTime": 0.1, "endTime": 0.22, "confidence": 0.91},
                            {"word": "好，", "startTime": 0.22, "endTime": 0.46, "confidence": 0.92},
                            {"word": "继", "startTime": 0.7, "endTime": 0.82, "confidence": 0.93},
                        ],
                    },
                },
                ensure_ascii=False,
            ),
        ]
    )

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
            return httpx.Response(200, text=response_body, request=request)

    monkeypatch.setattr("app.services.memoir.voice_tts.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr("app.services.memoir.voice_tts.provider_concurrency_limiter", limiter)
    client = VolcTTSClient(
        Settings(
            tts_provider="volc",
            tts_timeout_seconds=12,
            tts_max_concurrent_requests=6,
            volc_tts_app_id="app-id",
            volc_tts_access_token="access-token",
            volc_tts_endpoint="https://openspeech.bytedance.com/api/v3/tts/unidirectional/sse",
            volc_tts_resource_id="seed-tts-2.0",
            volc_tts_speaker="zh_female_vv_uranus_bigtts",
            volc_tts_response_format="mp3",
            volc_tts_sample_rate=24000,
        )
    )

    result = await client.synthesize("你好，继续讲。", voice="zh_male_m191_uranus_bigtts")

    assert result.audio == audio
    assert result.mime_type == "audio/mpeg"
    assert result.extension == ".mp3"
    assert result.provider == "volc_tts"
    assert result.model == "seed-tts-2.0"
    assert result.voice == "zh_male_m191_uranus_bigtts"
    assert result.speed_ratio == 1.0
    assert result.duration_seconds == 0.82
    assert [segment.text for segment in result.alignment] == ["你", "好，", "继"]
    assert result.alignment[1].start_ms == 220
    assert result.alignment[1].end_ms == 460
    assert result.alignment[1].text_start == 1
    assert result.alignment[1].text_end == 3
    assert result.alignment[1].confidence == 0.92
    assert calls[0]["url"] == "https://openspeech.bytedance.com/api/v3/tts/unidirectional/sse"
    assert calls[0]["json"] == {
        "user": {"uid": "pawtale-backend"},
        "req_params": {
            "text": "你好，继续讲。",
            "speaker": "zh_male_m191_uranus_bigtts",
            "audio_params": {
                "format": "mp3",
                "sample_rate": 24000,
                "enable_subtitle": True,
            },
            "additions": '{"disable_markdown_filter":true}',
        },
    }
    assert calls[0]["headers"]["X-Api-App-Key"] == "app-id"
    assert calls[0]["headers"]["X-Api-Access-Key"] == "access-token"
    assert calls[0]["headers"]["X-Api-Resource-Id"] == "seed-tts-2.0"
    assert calls[0]["headers"]["X-Api-Request-Id"]
    assert calls[0]["timeout"] == 12
    assert limiter.calls == [("tts", 6)]


@pytest.mark.asyncio
async def test_volc_tts_client_streams_sse_audio_chunks(monkeypatch):
    calls = []
    limiter = RecordingLimiter()
    first_audio = b"first"
    second_audio = b"second"
    response_lines = [
        f'data: {json.dumps({"code": 0, "message": "", "data": base64.b64encode(first_audio).decode()})}',
        "",
        f'data: {json.dumps({"code": 0, "message": "", "data": base64.b64encode(second_audio).decode()})}',
        "",
        'data: '
        + json.dumps(
            {
                "code": 0,
                "message": "",
                "data": None,
                "sentence": {
                    "text": "你好",
                    "words": [
                        {"word": "你", "startTime": 0.0, "endTime": 0.2},
                        {"word": "好", "startTime": 0.2, "endTime": 0.4},
                    ],
                },
            },
            ensure_ascii=False,
        ),
        "",
    ]

    class FakeStreamResponse:
        status_code = 200
        headers = {}

        def raise_for_status(self):
            return None

        async def aiter_lines(self):
            for line in response_lines:
                yield line

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    class FakeAsyncClient:
        def __init__(self, *, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def stream(self, method, url, *, json, headers):
            calls.append(
                {
                    "method": method,
                    "url": url,
                    "json": json,
                    "headers": headers,
                    "timeout": self.timeout,
                }
            )
            return FakeStreamResponse()

    monkeypatch.setattr("app.services.memoir.voice_tts.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr("app.services.memoir.voice_tts.provider_concurrency_limiter", limiter)
    client = VolcTTSClient(
        Settings(
            tts_provider="volc",
            tts_timeout_seconds=8,
            tts_max_concurrent_requests=3,
            volc_tts_app_id="app-id",
            volc_tts_access_token="access-token",
            volc_tts_endpoint="https://openspeech.bytedance.com/api/v3/tts/unidirectional/sse",
            volc_tts_resource_id="seed-tts-2.0",
            volc_tts_speaker="zh_female_vv_uranus_bigtts",
            volc_tts_response_format="pcm",
            volc_tts_sample_rate=16000,
        )
    )

    chunks = [chunk async for chunk in client.stream_synthesize("你好")]

    assert [chunk.audio for chunk in chunks] == [first_audio, second_audio, b""]
    assert [chunk.sequence for chunk in chunks] == [1, 2, 3]
    assert chunks[0].mime_type == "audio/L16"
    assert chunks[0].provider == "volc_tts"
    assert chunks[0].model == "seed-tts-2.0"
    assert chunks[0].voice == "zh_female_vv_uranus_bigtts"
    assert not chunks[0].is_final
    assert chunks[-1].is_final
    assert chunks[-1].duration_seconds == 0.4
    assert [segment.text for segment in chunks[-1].alignment] == ["你", "好"]
    assert calls[0]["method"] == "POST"
    assert calls[0]["json"]["req_params"]["audio_params"]["format"] == "pcm"
    assert calls[0]["json"]["req_params"]["audio_params"]["sample_rate"] == 16000
    assert limiter.slot_calls == [("tts", 3)]


@pytest.mark.asyncio
async def test_volc_tts_client_streams_bidirectional_text_input(monkeypatch):
    connect_calls = []
    limiter = RecordingLimiter()
    server_packets = [
        bidi_server_packet(50, connection_id="conn-1", payload={"event": 50}),
        bidi_server_packet(150, session_id="session-1", payload={"event": 150}),
        bidi_server_packet(352, session_id="session-1", audio=b"pcm-1", message_type=0xB4),
        bidi_server_packet(352, session_id="session-1", audio=b"pcm-2", message_type=0xB4),
        bidi_server_packet(152, session_id="session-1", payload={"event": 152}),
    ]
    connection = FakeBidirectionalConnection(server_packets)

    async def fake_connect(url, *, additional_headers, open_timeout, ping_interval, ping_timeout):
        connect_calls.append(
            {
                "url": url,
                "headers": additional_headers,
                "open_timeout": open_timeout,
                "ping_interval": ping_interval,
                "ping_timeout": ping_timeout,
            }
        )
        return connection

    async def text_chunks():
        yield "你好"
        yield "，继续"

    monkeypatch.setattr("app.services.memoir.voice_tts.websockets.connect", fake_connect)
    monkeypatch.setattr("app.services.memoir.voice_tts.provider_concurrency_limiter", limiter)
    client = VolcTTSClient(
        Settings(
            tts_provider="volc",
            tts_timeout_seconds=9,
            tts_max_concurrent_requests=2,
            volc_tts_app_id="app-id",
            volc_tts_access_token="access-token",
            volc_tts_bidirectional_endpoint="wss://openspeech.bytedance.com/api/v3/tts/bidirection",
            volc_tts_resource_id="seed-tts-2.0",
            volc_tts_speaker="zh_female_vv_uranus_bigtts",
        )
    )

    chunks = [
        chunk
        async for chunk in client.stream_input_synthesize(
            text_chunks(),
            voice="S_test_voice",
            speed_ratio=1.2,
            response_format="pcm",
            sample_rate=16000,
        )
    ]

    assert [chunk.audio for chunk in chunks] == [b"pcm-1", b"pcm-2", b""]
    assert [chunk.sequence for chunk in chunks] == [1, 2, 3]
    assert chunks[0].mime_type == "audio/L16"
    assert chunks[0].sample_rate == 16000
    assert chunks[-1].is_final
    assert chunks[-1].duration_seconds == len(b"pcm-1pcm-2") / (16000 * 2)
    assert connection.closed
    assert connect_calls[0]["url"] == "wss://openspeech.bytedance.com/api/v3/tts/bidirection"
    assert connect_calls[0]["headers"]["X-Api-App-Key"] == "app-id"
    assert connect_calls[0]["headers"]["X-Api-Access-Key"] == "access-token"
    assert connect_calls[0]["headers"]["X-Api-Resource-Id"] == "seed-tts-2.0"
    assert connect_calls[0]["open_timeout"] == 9
    assert limiter.slot_calls == [("tts", 2)]

    sent_packets = [decode_bidi_client_packet(frame) for frame in connection.sent]
    assert [packet["event"] for packet in sent_packets] == [1, 100, 200, 200, 102, 2]
    start_session = sent_packets[1]["payload"]
    assert start_session["namespace"] == "BidirectionalTTS"
    assert start_session["req_params"]["speaker"] == "S_test_voice"
    assert start_session["req_params"]["audio_params"]["format"] == "pcm"
    assert start_session["req_params"]["audio_params"]["sample_rate"] == 16000
    assert start_session["req_params"]["audio_params"]["speech_rate"] == 20
    assert sent_packets[2]["payload"]["req_params"]["text"] == "你好"
    assert sent_packets[3]["payload"]["req_params"]["text"] == "，继续"


@pytest.mark.asyncio
async def test_volc_tts_client_ignores_legacy_voice_values(monkeypatch):
    calls = []
    audio = b"audio-bytes"
    response_body = f'data: {json.dumps({"code": 0, "message": "", "data": base64.b64encode(audio).decode()})}'

    class FakeAsyncClient:
        def __init__(self, *, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, json, headers):
            calls.append(json)
            request = httpx.Request("POST", url)
            return httpx.Response(200, text=response_body, request=request)

    monkeypatch.setattr("app.services.memoir.voice_tts.httpx.AsyncClient", FakeAsyncClient)
    client = VolcTTSClient(
        Settings(
            tts_provider="volc",
            volc_tts_app_id="app-id",
            volc_tts_access_token="access-token",
            volc_tts_speaker="zh_female_vv_uranus_bigtts",
        )
    )

    result = await client.synthesize("你好", voice="cixingnansheng", speed_ratio=0.7)

    assert result.voice == "zh_female_vv_uranus_bigtts"
    assert result.speed_ratio == 0.7
    assert calls[0]["req_params"]["speaker"] == "zh_female_vv_uranus_bigtts"
    assert calls[0]["req_params"]["audio_params"]["speech_rate"] == -30


@pytest.mark.asyncio
async def test_volc_tts_client_fails_when_provider_returns_no_audio(monkeypatch):
    limiter = RecordingLimiter()
    response_body = 'event: 153\ndata: {"code":45000030,"message":"resource not granted"}\n'

    class FakeAsyncClient:
        def __init__(self, *, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, json, headers):
            request = httpx.Request("POST", url)
            return httpx.Response(200, text=response_body, request=request)

    monkeypatch.setattr("app.services.memoir.voice_tts.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr("app.services.memoir.voice_tts.provider_concurrency_limiter", limiter)
    client = VolcTTSClient(
        Settings(
            tts_provider="volc",
            volc_tts_app_id="app-id",
            volc_tts_access_token="access-token",
        )
    )

    with pytest.raises(HTTPException):
        await client.synthesize("你好")


class RecordingLimiter:
    def __init__(self):
        self.calls = []
        self.slot_calls = []

    async def run(self, key, limit, operation):
        self.calls.append((key, limit))
        return await operation()

    @asynccontextmanager
    async def slot(self, key, limit):
        self.slot_calls.append((key, limit))
        yield


class FakeBidirectionalConnection:
    def __init__(self, packets):
        self.packets = list(packets)
        self.sent = []
        self.closed = False

    async def send(self, frame):
        self.sent.append(frame)

    async def recv(self):
        if not self.packets:
            raise RuntimeError("no more fake bidirectional packets")
        return self.packets.pop(0)

    async def close(self):
        self.closed = True


def bidi_server_packet(
    event,
    *,
    session_id="",
    connection_id="",
    payload=None,
    audio=b"",
    message_type=0x94,
):
    parts = [struct.pack(">BBBBi", 0x11, message_type, 0x10, 0x00, event)]
    if event in {50, 51}:
        encoded_connection_id = connection_id.encode()
        parts.append(struct.pack(">I", len(encoded_connection_id)))
        parts.append(encoded_connection_id)
    else:
        encoded_session_id = session_id.encode()
        parts.append(struct.pack(">I", len(encoded_session_id)))
        parts.append(encoded_session_id)
    if message_type == 0xB4:
        parts.append(struct.pack(">I", len(audio)))
        parts.append(audio)
    else:
        payload_bytes = json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":")).encode()
        parts.append(struct.pack(">I", len(payload_bytes)))
        parts.append(payload_bytes)
    return b"".join(parts)


def decode_bidi_client_packet(frame):
    offset = 4
    event = struct.unpack(">i", frame[offset : offset + 4])[0]
    offset += 4
    session_id = ""
    if event in {100, 102, 200}:
        session_id_size = struct.unpack(">I", frame[offset : offset + 4])[0]
        offset += 4
        session_id = frame[offset : offset + session_id_size].decode()
        offset += session_id_size
    payload_size = struct.unpack(">I", frame[offset : offset + 4])[0]
    offset += 4
    payload = json.loads(frame[offset : offset + payload_size].decode())
    return {"event": event, "session_id": session_id, "payload": payload}
