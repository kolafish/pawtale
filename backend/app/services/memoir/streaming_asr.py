from __future__ import annotations

import gzip
import json
import uuid
from dataclasses import dataclass, field
from json import JSONDecodeError
from typing import Any, AsyncIterator, Literal, Protocol

import websockets

from app.config import get_settings


ASREventType = Literal["partial", "final", "error"]

PROTOCOL_VERSION = 0b0001
DEFAULT_HEADER_SIZE = 0b0001

CLIENT_FULL_REQUEST = 0b0001
CLIENT_AUDIO_ONLY_REQUEST = 0b0010
SERVER_FULL_RESPONSE = 0b1001
SERVER_ACK = 0b1011
SERVER_ERROR_RESPONSE = 0b1111

NO_SEQUENCE = 0b0000
POS_SEQUENCE = 0b0001
NEG_SEQUENCE = 0b0010
NEG_SEQUENCE_1 = 0b0011

JSON_SERIALIZATION = 0b0001
GZIP_COMPRESSION = 0b0001


@dataclass(frozen=True)
class StreamingASREvent:
    type: ASREventType
    text: str = ""
    start_ms: int | None = None
    end_ms: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    error: str = ""


@dataclass(frozen=True)
class VolcStreamingASRConfig:
    ws_url: str
    resource_id: str
    api_key: str = ""
    app_key: str = ""
    access_key: str = ""
    connect_timeout_seconds: float = 10.0

    def validate_credentials(self) -> None:
        if self.app_key and self.access_key:
            return
        if self.api_key:
            return
        raise RuntimeError("Volc streaming ASR credentials are not configured")


@dataclass(frozen=True)
class VolcServerMessage:
    message_type: int
    flags: int
    sequence: int | None = None
    code: int | None = None
    payload: dict[str, Any] | str | bytes | None = None


class StreamingASRClient(Protocol):
    async def start(self) -> None: ...

    async def send_audio(self, audio: bytes) -> None: ...

    async def finish(self) -> None: ...

    async def events(self) -> AsyncIterator[StreamingASREvent]: ...

    async def close(self) -> None: ...


def volc_config_from_settings() -> VolcStreamingASRConfig:
    settings = get_settings()
    return VolcStreamingASRConfig(
        ws_url=settings.volc_asr_ws_url,
        resource_id=settings.volc_asr_resource_id,
        api_key=settings.volc_asr_api_key,
        app_key=settings.volc_asr_app_key,
        access_key=settings.volc_asr_access_key,
        connect_timeout_seconds=settings.volc_asr_connect_timeout_seconds,
    )


def create_volc_streaming_asr_client(user_id: str, voice_session_id: str) -> "VolcStreamingASRClient":
    return VolcStreamingASRClient(
        config=volc_config_from_settings(),
        user_id=user_id,
        voice_session_id=voice_session_id,
    )


def build_volc_full_request(payload: dict[str, Any], *, sequence: int | None = None) -> bytes:
    return _build_volc_frame(
        message_type=CLIENT_FULL_REQUEST,
        flags=POS_SEQUENCE if sequence is not None else NO_SEQUENCE,
        sequence=sequence,
        payload=_serialize_payload(payload),
    )


def build_volc_audio_request(audio: bytes, *, is_last: bool = False, sequence: int | None = None) -> bytes:
    flags = NEG_SEQUENCE if is_last else NO_SEQUENCE
    if sequence is not None:
        flags = NEG_SEQUENCE_1 if is_last else POS_SEQUENCE
    return _build_volc_frame(
        message_type=CLIENT_AUDIO_ONLY_REQUEST,
        flags=flags,
        sequence=(-abs(sequence) if sequence is not None and is_last else sequence),
        payload=gzip.compress(audio),
    )


def parse_volc_server_message(message: bytes) -> VolcServerMessage:
    if len(message) < 4:
        raise ValueError("Volc ASR frame is shorter than the protocol header")

    header_size = (message[0] & 0x0F) * 4
    if header_size < 4 or len(message) < header_size:
        raise ValueError("Volc ASR frame has an invalid header size")

    message_type = message[1] >> 4
    flags = message[1] & 0x0F
    serialization = message[2] >> 4
    compression = message[2] & 0x0F
    payload = message[header_size:]
    sequence, body_payload = _split_sequence(flags, payload)

    if message_type == SERVER_FULL_RESPONSE:
        if not body_payload:
            return VolcServerMessage(message_type=message_type, flags=flags, sequence=sequence)
        body = _read_payload_body(body_payload)
        return VolcServerMessage(
            message_type=message_type,
            flags=flags,
            sequence=sequence,
            payload=_deserialize_payload(body, serialization, compression) if body else None,
        )

    if message_type == SERVER_ACK:
        if sequence is None and len(payload) < 4:
            raise ValueError("Volc ASR ACK frame is missing sequence")
        if sequence is None:
            sequence = int.from_bytes(payload[:4], "big", signed=True)
            body_payload = payload[4:]
        body = _read_payload_body(body_payload) if len(body_payload) >= 4 else b""
        decoded_payload: dict[str, Any] | str | bytes | None = None
        if body:
            decoded_payload = _deserialize_payload(body, serialization, compression)
        return VolcServerMessage(
            message_type=message_type,
            flags=flags,
            sequence=sequence,
            payload=decoded_payload,
        )

    if message_type == SERVER_ERROR_RESPONSE:
        if len(body_payload) < 8:
            raise ValueError("Volc ASR error frame is missing code or payload size")
        code = int.from_bytes(body_payload[:4], "big", signed=False)
        body = _read_payload_body(body_payload[4:])
        return VolcServerMessage(
            message_type=message_type,
            flags=flags,
            sequence=sequence,
            code=code,
            payload=_deserialize_payload(body, serialization, compression),
        )

    return VolcServerMessage(message_type=message_type, flags=flags, sequence=sequence, payload=body_payload)


def volc_message_to_event(message: VolcServerMessage) -> StreamingASREvent | None:
    if message.message_type == SERVER_ERROR_RESPONSE:
        return StreamingASREvent(
            type="error",
            error=_payload_error_message(message.payload) or f"Volc ASR error {message.code}",
            raw=_raw_payload(message),
        )

    if not isinstance(message.payload, dict):
        return None

    provider_code = message.payload.get("code")
    if isinstance(provider_code, int) and provider_code != 0:
        return StreamingASREvent(
            type="error",
            error=_payload_error_message(message.payload) or f"Volc ASR error {provider_code}",
            raw=_raw_payload(message),
        )

    candidate = _extract_text_candidate(message.payload)
    if not candidate["text"]:
        return None

    is_final = bool(candidate["is_final"] or (message.sequence is not None and message.sequence < 0))
    return StreamingASREvent(
        type="final" if is_final else "partial",
        text=str(candidate["text"]),
        start_ms=candidate["start_ms"],
        end_ms=candidate["end_ms"],
        raw=_raw_payload(message),
    )


class VolcStreamingASRClient:
    def __init__(
        self,
        *,
        config: VolcStreamingASRConfig,
        user_id: str,
        voice_session_id: str,
    ) -> None:
        self.config = config
        self.user_id = user_id
        self.voice_session_id = voice_session_id
        self.request_id = str(uuid.uuid4())
        self._connection: Any | None = None
        self._sequence = 1

    async def start(self) -> None:
        self.config.validate_credentials()
        self._connection = await websockets.connect(
            self.config.ws_url,
            additional_headers=self._headers(),
            open_timeout=self.config.connect_timeout_seconds,
            ping_interval=20,
            ping_timeout=20,
        )
        await self._connection.send(build_volc_full_request(self._initial_request(), sequence=self._next_sequence()))

    async def send_audio(self, audio: bytes) -> None:
        await self._send(build_volc_audio_request(audio, sequence=self._next_sequence()))

    async def finish(self) -> None:
        await self._send(build_volc_audio_request(b"", is_last=True, sequence=self._next_sequence()))

    async def events(self) -> AsyncIterator[StreamingASREvent]:
        if self._connection is None:
            raise RuntimeError("Volc streaming ASR client has not been started")
        async for message in self._connection:
            try:
                if isinstance(message, bytes):
                    server_message = parse_volc_server_message(message)
                else:
                    if not message.strip():
                        continue
                    server_message = VolcServerMessage(
                        message_type=SERVER_FULL_RESPONSE,
                        flags=NO_SEQUENCE,
                        payload=json.loads(message),
                    )
                event = volc_message_to_event(server_message)
            except Exception as exc:
                event = StreamingASREvent(type="error", error=str(exc))
            if event:
                yield event

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    async def _send(self, frame: bytes) -> None:
        if self._connection is None:
            raise RuntimeError("Volc streaming ASR client has not been started")
        await self._connection.send(frame)

    def _headers(self) -> dict[str, str]:
        headers = {
            "X-Api-Request-Id": self.request_id,
            "X-Api-Resource-Id": self.config.resource_id,
        }
        if self.config.app_key and self.config.access_key:
            headers["X-Api-App-Key"] = self.config.app_key
            headers["X-Api-Access-Key"] = self.config.access_key
        elif self.config.api_key:
            headers["X-Api-Key"] = self.config.api_key
        return headers

    def _initial_request(self) -> dict[str, Any]:
        return {
            "user": {"uid": self.user_id},
            "audio": {
                "format": "pcm",
                "codec": "raw",
                "rate": 16000,
                "bits": 16,
                "channel": 1,
            },
            "request": {
                "model_name": "bigmodel",
                "enable_itn": True,
                "enable_punc": True,
                "enable_ddc": True,
                "show_utterances": True,
                "enable_nonstream": True,
                "result_type": "full",
                "voice_session_id": self.voice_session_id,
            },
        }

    def _next_sequence(self) -> int:
        sequence = self._sequence
        self._sequence += 1
        return sequence


def _build_volc_frame(*, message_type: int, flags: int, payload: bytes, sequence: int | None = None) -> bytes:
    header = bytes(
        [
            (PROTOCOL_VERSION << 4) | DEFAULT_HEADER_SIZE,
            (message_type << 4) | flags,
            (JSON_SERIALIZATION << 4) | GZIP_COMPRESSION,
            0,
        ]
    )
    sequence_bytes = b"" if sequence is None else sequence.to_bytes(4, "big", signed=True)
    return header + sequence_bytes + len(payload).to_bytes(4, "big", signed=False) + payload


def _serialize_payload(payload: dict[str, Any]) -> bytes:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return gzip.compress(raw)


def _read_payload_body(payload: bytes) -> bytes:
    if len(payload) < 4:
        raise ValueError("Volc ASR frame is missing payload size")
    size = int.from_bytes(payload[:4], "big", signed=False)
    body = payload[4 : 4 + size]
    if len(body) != size:
        raise ValueError("Volc ASR frame payload is truncated")
    return body


def _split_sequence(flags: int, payload: bytes) -> tuple[int | None, bytes]:
    if flags not in {POS_SEQUENCE, NEG_SEQUENCE, NEG_SEQUENCE_1}:
        return None, payload
    if len(payload) < 4:
        raise ValueError("Volc ASR frame is missing sequence")
    return int.from_bytes(payload[:4], "big", signed=True), payload[4:]


def _deserialize_payload(body: bytes, serialization: int, compression: int) -> dict[str, Any] | str | bytes:
    if compression == GZIP_COMPRESSION:
        body = gzip.decompress(body)
    if serialization == JSON_SERIALIZATION:
        text = body.decode("utf-8", errors="replace")
        if not text.strip():
            return ""
        try:
            decoded = json.loads(text)
        except JSONDecodeError:
            return text
        return decoded if isinstance(decoded, dict) else {"value": decoded}
    return body


def _extract_text_candidate(payload: dict[str, Any]) -> dict[str, Any]:
    result = payload.get("result")
    if isinstance(result, list) and result:
        base = result[-1] if isinstance(result[-1], dict) else {}
    elif isinstance(result, dict):
        base = result
    else:
        base = payload

    utterances = base.get("utterances")
    if not utterances:
        utterances = payload.get("utterances")

    if isinstance(utterances, list) and utterances:
        last_utterance = utterances[-1] if isinstance(utterances[-1], dict) else {}
        return {
            "text": last_utterance.get("text") or base.get("text") or payload.get("text") or "",
            "is_final": bool(
                last_utterance.get("definite")
                or last_utterance.get("is_final")
                or base.get("definite")
                or base.get("is_final")
                or payload.get("is_final")
            ),
            "start_ms": _as_optional_int(
                _first_present(
                    last_utterance.get("start_ms"),
                    last_utterance.get("start_time"),
                    base.get("start_ms"),
                )
            ),
            "end_ms": _as_optional_int(
                _first_present(
                    last_utterance.get("end_ms"),
                    last_utterance.get("end_time"),
                    base.get("end_ms"),
                )
            ),
        }

    return {
        "text": base.get("text") or payload.get("text") or "",
        "is_final": bool(base.get("definite") or base.get("is_final") or payload.get("is_final")),
        "start_ms": _as_optional_int(_first_present(base.get("start_ms"), payload.get("start_ms"))),
        "end_ms": _as_optional_int(_first_present(base.get("end_ms"), payload.get("end_ms"))),
    }


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _as_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _payload_error_message(payload: dict[str, Any] | str | bytes | None) -> str:
    if isinstance(payload, dict):
        message = payload.get("message") or payload.get("error") or payload.get("detail")
        return str(message) if message else ""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, bytes):
        return payload.decode("utf-8", errors="replace")
    return ""


def _raw_payload(message: VolcServerMessage) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "message_type": message.message_type,
        "flags": message.flags,
    }
    if message.sequence is not None:
        raw["sequence"] = message.sequence
    if message.code is not None:
        raw["code"] = message.code
    if isinstance(message.payload, dict):
        raw["payload"] = message.payload
    elif isinstance(message.payload, str):
        raw["payload"] = {"message": message.payload}
    return raw
