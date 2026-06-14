import gzip
import json

import pytest

from app.services.memoir.streaming_asr import (
    GZIP_COMPRESSION,
    JSON_SERIALIZATION,
    SERVER_ACK,
    SERVER_ERROR_RESPONSE,
    SERVER_FULL_RESPONSE,
    StreamingASREvent,
    VolcStreamingASRClient,
    VolcStreamingASRConfig,
    build_volc_audio_request,
    build_volc_full_request,
    parse_volc_server_message,
    volc_message_to_event,
)


def test_build_volc_full_request_uses_binary_gzip_json_frame():
    frame = build_volc_full_request({"request": {"model_name": "bigmodel"}})

    assert frame[:4] == bytes([0x11, 0x10, 0x11, 0x00])

    payload_size = int.from_bytes(frame[4:8], "big")
    payload = gzip.decompress(frame[8 : 8 + payload_size])
    assert json.loads(payload) == {"request": {"model_name": "bigmodel"}}


def test_build_volc_audio_request_marks_regular_and_final_frames():
    regular = build_volc_audio_request(b"abc")
    final = build_volc_audio_request(b"", is_last=True)

    assert regular[:4] == bytes([0x11, 0x20, 0x11, 0x00])
    assert final[:4] == bytes([0x11, 0x22, 0x11, 0x00])
    assert gzip.decompress(regular[8:]) == b"abc"


def test_parse_volc_full_response_and_extract_partial_event():
    payload = {"result": [{"text": "我小时候", "is_final": False}]}
    frame = _server_frame(SERVER_FULL_RESPONSE, payload)

    message = parse_volc_server_message(frame)
    event = volc_message_to_event(message)

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

    message = parse_volc_server_message(frame)
    event = volc_message_to_event(message)

    assert message.sequence == 7
    assert event is not None
    assert event.type == "final"
    assert event.text == "我小时候住在外婆家。"
    assert event.start_ms == 0
    assert event.end_ms == 2400


def test_parse_volc_error_response_extracts_error_event():
    payload = {"message": "invalid api key"}
    frame = _server_frame(SERVER_ERROR_RESPONSE, payload, code=401)

    message = parse_volc_server_message(frame)
    event = volc_message_to_event(message)

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


def _server_frame(
    message_type: int,
    payload: dict,
    *,
    sequence: int | None = None,
    code: int | None = None,
) -> bytes:
    body = gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    header = bytes([0x11, message_type << 4, (JSON_SERIALIZATION << 4) | GZIP_COMPRESSION, 0x00])
    size_and_body = len(body).to_bytes(4, "big") + body
    if message_type == SERVER_ACK:
        return header + int(sequence or 0).to_bytes(4, "big", signed=True) + size_and_body
    if message_type == SERVER_ERROR_RESPONSE:
        return header + int(code or 0).to_bytes(4, "big", signed=False) + size_and_body
    return header + size_and_body
