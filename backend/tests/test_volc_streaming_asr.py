import gzip
import json

import pytest

from app.services.memoir.asr import StreamingASREvent
from app.services.memoir.volc_asr_protocol import (
    GZIP_COMPRESSION,
    JSON_SERIALIZATION,
    SERVER_ACK,
    SERVER_ERROR_RESPONSE,
    SERVER_FULL_RESPONSE,
    VolcASRProtocolCodec,
)
from app.services.memoir.volc_streaming_asr import (
    VolcStreamingASRClient,
    VolcStreamingASRConfig,
)


CODEC = VolcASRProtocolCodec()


def test_build_volc_full_request_uses_binary_gzip_json_frame():
    frame = CODEC.build_full_request({"request": {"model_name": "bigmodel"}})

    assert frame[:4] == bytes([0x11, 0x10, 0x11, 0x00])

    payload_size = int.from_bytes(frame[4:8], "big")
    payload = gzip.decompress(frame[8 : 8 + payload_size])
    assert json.loads(payload) == {"request": {"model_name": "bigmodel"}}


def test_build_volc_full_request_can_include_sequence():
    frame = CODEC.build_full_request({"request": {"model_name": "bigmodel"}}, sequence=1)

    assert frame[:4] == bytes([0x11, 0x11, 0x11, 0x00])
    assert int.from_bytes(frame[4:8], "big", signed=True) == 1

    payload_size = int.from_bytes(frame[8:12], "big")
    payload = gzip.decompress(frame[12 : 12 + payload_size])
    assert json.loads(payload) == {"request": {"model_name": "bigmodel"}}


def test_build_volc_audio_request_marks_regular_and_final_frames():
    regular = CODEC.build_audio_request(b"abc")
    final = CODEC.build_audio_request(b"", is_last=True)

    assert regular[:4] == bytes([0x11, 0x20, 0x11, 0x00])
    assert final[:4] == bytes([0x11, 0x22, 0x11, 0x00])
    assert gzip.decompress(regular[8:]) == b"abc"


def test_build_volc_audio_request_can_include_sequence():
    regular = CODEC.build_audio_request(b"abc", sequence=2)
    final = CODEC.build_audio_request(b"", is_last=True, sequence=3)

    assert regular[:4] == bytes([0x11, 0x21, 0x11, 0x00])
    assert int.from_bytes(regular[4:8], "big", signed=True) == 2
    assert gzip.decompress(regular[12:]) == b"abc"
    assert final[:4] == bytes([0x11, 0x23, 0x11, 0x00])
    assert int.from_bytes(final[4:8], "big", signed=True) == -3


def test_parse_volc_full_response_and_extract_partial_event():
    payload = {"result": [{"text": "我小时候", "is_final": False}]}
    frame = _server_frame(SERVER_FULL_RESPONSE, payload)

    message = CODEC.parse_server_message(frame)
    event = CODEC.message_to_event(message)

    assert message.message_type == SERVER_FULL_RESPONSE
    assert event == StreamingASREvent(
        type="partial",
        text="我小时候",
        raw={
            "message_type": SERVER_FULL_RESPONSE,
            "flags": 0,
            "payload": payload,
        },
    )


def test_parse_volc_full_response_with_sequence():
    payload = {"result": [{"text": "我小时候", "is_final": False}]}
    frame = _server_frame(SERVER_FULL_RESPONSE, payload, sequence=2)

    message = CODEC.parse_server_message(frame)
    event = CODEC.message_to_event(message)

    assert message.sequence == 2
    assert event is not None
    assert event.type == "partial"
    assert event.text == "我小时候"


def test_parse_volc_empty_full_response_is_ignored():
    frame = bytes([0x11, SERVER_FULL_RESPONSE << 4, (JSON_SERIALIZATION << 4) | GZIP_COMPRESSION, 0x00])

    message = CODEC.parse_server_message(frame)
    event = CODEC.message_to_event(message)

    assert message.payload is None
    assert event is None


def test_parse_volc_empty_json_payload_is_ignored():
    body = gzip.compress(b"")
    frame = (
        bytes([0x11, SERVER_FULL_RESPONSE << 4, (JSON_SERIALIZATION << 4) | GZIP_COMPRESSION, 0x00])
        + len(body).to_bytes(4, "big")
        + body
    )

    message = CODEC.parse_server_message(frame)
    event = CODEC.message_to_event(message)

    assert message.payload == ""
    assert event is None


def test_parse_volc_ack_extracts_final_utterance_event():
    payload = {
        "result": [
            {
                "text": "我小时候住在外婆家。",
                "utterances": [
                    {
                        "text": "我小时候住在外婆家。",
                        "definite": True,
                        "start_time": 0,
                        "end_time": 2400,
                    }
                ],
            }
        ]
    }
    frame = _server_frame(SERVER_ACK, payload, sequence=7)

    message = CODEC.parse_server_message(frame)
    event = CODEC.message_to_event(message)

    assert message.sequence == 7
    assert event is not None
    assert event.type == "final"
    assert event.text == "我小时候住在外婆家。"
    assert event.start_ms == 0
    assert event.end_ms == 2400


def test_parse_volc_error_response_extracts_error_event():
    payload = {"message": "invalid api key"}
    frame = _server_frame(SERVER_ERROR_RESPONSE, payload, code=401)

    message = CODEC.parse_server_message(frame)
    event = CODEC.message_to_event(message)

    assert message.code == 401
    assert event is not None
    assert event.type == "error"
    assert event.error == "invalid api key"


def test_volc_config_accepts_new_or_legacy_credentials():
    VolcStreamingASRConfig(ws_url="wss://example.test", resource_id="resource", api_key="api").validate_credentials()
    VolcStreamingASRConfig(
        ws_url="wss://example.test",
        resource_id="resource",
        app_key="app",
        access_key="access",
    ).validate_credentials()

    with pytest.raises(RuntimeError):
        VolcStreamingASRConfig(ws_url="wss://example.test", resource_id="resource").validate_credentials()


def test_volc_headers_prefer_app_access_credentials_over_api_key():
    client = VolcStreamingASRClient(
        config=VolcStreamingASRConfig(
            ws_url="wss://example.test",
            resource_id="volc.seedasr.sauc.duration",
            api_key="legacy-api-key",
            app_key="app-id",
            access_key="access-token",
        ),
        user_id="user-1",
        voice_session_id="voice-session-1",
    )

    headers = client._headers()

    assert headers["X-Api-App-Key"] == "app-id"
    assert headers["X-Api-Access-Key"] == "access-token"
    assert "X-Api-Key" not in headers


def test_volc_initial_request_asks_for_full_two_pass_results():
    client = VolcStreamingASRClient(
        config=VolcStreamingASRConfig(
            ws_url="wss://example.test",
            resource_id="volc.seedasr.sauc.duration",
            api_key="api",
        ),
        user_id="user-1",
        voice_session_id="voice-session-1",
    )

    request = client._initial_request()

    assert request["audio"] == {
        "format": "pcm",
        "codec": "raw",
        "rate": 16000,
        "bits": 16,
        "channel": 1,
    }
    assert request["request"]["result_type"] == "full"
    assert request["request"]["enable_nonstream"] is True


def test_volc_client_start_sends_initial_request_with_sequence(monkeypatch):
    sent_frames = []
    connect_calls = []

    class FakeConnection:
        async def send(self, frame):
            sent_frames.append(frame)

    async def fake_connect(*args, **kwargs):
        connect_calls.append({"args": args, "kwargs": kwargs})
        return FakeConnection()

    monkeypatch.setattr("app.services.memoir.volc_streaming_asr.websockets.connect", fake_connect)

    client = VolcStreamingASRClient(
        config=VolcStreamingASRConfig(
            ws_url="wss://example.test",
            resource_id="volc.seedasr.sauc.duration",
            api_key="api",
        ),
        user_id="user-1",
        voice_session_id="voice-session-1",
    )

    import asyncio

    asyncio.run(client.start())
    asyncio.run(client.send_audio(b"abc"))

    assert connect_calls[0]["args"] == ("wss://example.test",)
    assert connect_calls[0]["kwargs"]["additional_headers"]["X-Api-Key"] == "api"
    assert sent_frames[0][:4] == bytes([0x11, 0x11, 0x11, 0x00])
    assert int.from_bytes(sent_frames[0][4:8], "big", signed=True) == 1
    assert sent_frames[1][:4] == bytes([0x11, 0x21, 0x11, 0x00])
    assert int.from_bytes(sent_frames[1][4:8], "big", signed=True) == 2


def test_volc_client_uses_sequence_frames_for_requests():
    sent_frames = []

    class FakeConnection:
        async def send(self, frame):
            sent_frames.append(frame)

    client = VolcStreamingASRClient(
        config=VolcStreamingASRConfig(
            ws_url="wss://example.test",
            resource_id="volc.seedasr.sauc.duration",
            api_key="api",
        ),
        user_id="user-1",
        voice_session_id="voice-session-1",
    )
    client._connection = FakeConnection()

    import asyncio

    asyncio.run(client.send_audio(b"abc"))
    asyncio.run(client.finish())

    assert sent_frames[0][:4] == bytes([0x11, 0x21, 0x11, 0x00])
    assert int.from_bytes(sent_frames[0][4:8], "big", signed=True) == 1
    assert sent_frames[1][:4] == bytes([0x11, 0x23, 0x11, 0x00])
    assert int.from_bytes(sent_frames[1][4:8], "big", signed=True) == -2


def _server_frame(
    message_type: int,
    payload: dict,
    *,
    sequence: int | None = None,
    code: int | None = None,
) -> bytes:
    body = gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    flags = 0x01 if sequence is not None and message_type == SERVER_FULL_RESPONSE else 0x00
    header = bytes([0x11, (message_type << 4) | flags, (JSON_SERIALIZATION << 4) | GZIP_COMPRESSION, 0x00])
    size_and_body = len(body).to_bytes(4, "big") + body
    if sequence is not None and message_type == SERVER_FULL_RESPONSE:
        return header + int(sequence).to_bytes(4, "big", signed=True) + size_and_body
    if message_type == SERVER_ACK:
        return header + int(sequence or 0).to_bytes(4, "big", signed=True) + size_and_body
    if message_type == SERVER_ERROR_RESPONSE:
        return header + int(code or 0).to_bytes(4, "big", signed=False) + size_and_body
    return header + size_and_body
