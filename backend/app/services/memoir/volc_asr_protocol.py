from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from json import JSONDecodeError
from typing import Any

from app.services.memoir.asr import StreamingASREvent


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
class VolcServerMessage:
    message_type: int
    flags: int
    sequence: int | None = None
    code: int | None = None
    payload: dict[str, Any] | str | bytes | None = None


class VolcASRProtocolCodec:
    def build_full_request(self, payload: dict[str, Any], *, sequence: int | None = None) -> bytes:
        return _build_volc_frame(
            message_type=CLIENT_FULL_REQUEST,
            flags=POS_SEQUENCE if sequence is not None else NO_SEQUENCE,
            sequence=sequence,
            payload=_serialize_payload(payload),
        )

    def build_audio_request(self, audio: bytes, *, is_last: bool = False, sequence: int | None = None) -> bytes:
        flags = NEG_SEQUENCE if is_last else NO_SEQUENCE
        if sequence is not None:
            flags = NEG_SEQUENCE_1 if is_last else POS_SEQUENCE
        return _build_volc_frame(
            message_type=CLIENT_AUDIO_ONLY_REQUEST,
            flags=flags,
            sequence=(-abs(sequence) if sequence is not None and is_last else sequence),
            payload=gzip.compress(audio),
        )

    def parse_server_message(self, message: bytes) -> VolcServerMessage:
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

    def text_message_to_server_message(self, message: str) -> VolcServerMessage | None:
        if not message.strip():
            return None
        return VolcServerMessage(
            message_type=SERVER_FULL_RESPONSE,
            flags=NO_SEQUENCE,
            payload=json.loads(message),
        )

    def message_to_event(self, message: VolcServerMessage) -> StreamingASREvent | None:
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
        valid_utterances = [utterance for utterance in utterances if isinstance(utterance, dict)]
        first_utterance = valid_utterances[0] if valid_utterances else {}
        last_utterance = valid_utterances[-1] if valid_utterances else {}
        utterance_text = "".join(str(utterance.get("text") or "") for utterance in valid_utterances)
        return {
            "text": base.get("text") or payload.get("text") or utterance_text,
            "is_final": bool(
                last_utterance.get("definite")
                or last_utterance.get("is_final")
                or base.get("definite")
                or base.get("is_final")
                or payload.get("is_final")
            ),
            "start_ms": _as_optional_int(
                _first_present(
                    first_utterance.get("start_ms"),
                    first_utterance.get("start_time"),
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
