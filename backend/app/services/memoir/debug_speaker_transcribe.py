from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import json
from dataclasses import dataclass
from typing import Any, Callable, Protocol, Sequence

from app.services.memoir.asr import StreamingASRClient, StreamingASREvent
from app.services.memoir.asr_text_normalization import normalize_memoir_asr_text
from app.services.memoir.voice_gateway import MAX_AUDIO_FRAME_BYTES
from app.services.memoir.voice_transcripts import (
    CleanedTranscriptText,
    MemoirTranscriptCleanupContext,
    generate_cleaned_transcript_text,
)


DebugSpeakerTranscriberFactory = Callable[[], StreamingASRClient]
DEBUG_SPEAKER_FINISH_DRAIN_TIMEOUT_SECONDS = 10.0
DEBUG_SPEAKER_PROOFREAD_CONTEXT_LIMIT = 8
DEBUG_SPEAKER_PROOFREAD_CONTEXT_TEXT_LIMIT = 160


@dataclass(frozen=True)
class DebugSpeakerSegment:
    segment_id: str
    speaker_id: str
    text: str
    start_ms: int | None = None
    end_ms: int | None = None
    confidence: float | None = None
    provider_payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class DebugSpeakerProofreadContextLine:
    speaker_id: str | None
    text: str
    start_ms: int | None = None
    end_ms: int | None = None


class DebugSpeakerTranscribeClient(Protocol):
    async def receive_json(self) -> dict[str, Any]: ...

    async def send_json(self, payload: dict[str, Any]) -> None: ...

    async def close(self, code: int = 1000) -> None: ...


class DebugSpeakerTranscribeSession:
    """Realtime speaker-attributed ASR session for developer diagnostics only.

    This session intentionally does not persist audio, transcripts, speaker labels,
    or embeddings. It forwards audio to an ASR provider and groups transcript
    events by provider speaker fields when available.
    """

    def __init__(
        self,
        *,
        client: DebugSpeakerTranscribeClient,
        transcriber_factory: DebugSpeakerTranscriberFactory,
    ) -> None:
        self.client = client
        self.transcriber_factory = transcriber_factory
        self._transcriber: StreamingASRClient | None = None
        self._asr_task: asyncio.Task[None] | None = None
        self._state = "ready"
        self._terminal_error = ""
        self._terminal_stop = asyncio.Event()
        self._next_segment_index = 1
        self._sent_missing_speaker_labels_status = False
        self._sent_available_speaker_labels_status = False

    async def run(self) -> None:
        await self.client.send_json(self._event("session.ready"))
        try:
            while not self._terminal_stop.is_set():
                message = await self._receive_next_message()
                if message is None:
                    break
                should_stop = await self._handle_client_message(message)
                if should_stop:
                    break
        except Exception as exc:
            await self._mark_terminal_error(str(exc))
        finally:
            await self._shutdown()
            self._state = "failed" if self._terminal_error else "stopped"
            with contextlib.suppress(Exception):
                await self.client.send_json(
                    self._event(
                        "session.stopped",
                        status="failed" if self._terminal_error else "stopped",
                        error=self._terminal_error,
                    )
                )

    async def _receive_next_message(self) -> dict[str, Any] | None:
        receive_task = asyncio.create_task(self.client.receive_json())
        stop_task = asyncio.create_task(self._terminal_stop.wait())
        done, pending = await asyncio.wait(
            {receive_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        for task in pending:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if receive_task in done:
            return receive_task.result()
        return None

    async def _handle_client_message(self, message: dict[str, Any]) -> bool:
        message_type = message.get("type")
        if message_type == "session.start":
            await self._start_transcriber()
            return False
        if message_type == "audio.frame":
            await self._send_audio_frame(message)
            return False
        if message_type == "audio.end":
            await self._finish_audio()
            return False
        if message_type == "session.stop":
            if self._state == "listening":
                await self._finish_audio()
            return True
        await self._send_error("unsupported_debug_speaker_event", {"received_type": message_type})
        return False

    async def _start_transcriber(self) -> None:
        if self._state == "listening":
            await self.client.send_json(self._event("session.started"))
            return
        if self._state == "finishing":
            await self._send_error("debug_speaker_session_finishing")
            return
        await self._close_transcriber()
        self._state = "listening"
        self._transcriber = self.transcriber_factory()
        await self._transcriber.start()
        self._asr_task = asyncio.create_task(self._forward_asr_events())
        await self.client.send_json(self._event("session.started"))

    async def _send_audio_frame(self, message: dict[str, Any]) -> None:
        if self._state != "listening" or self._transcriber is None:
            await self._send_error("debug_speaker_session_not_started")
            return
        encoded = message.get("audio")
        if not isinstance(encoded, str):
            await self._send_error("audio_frame_missing_base64")
            return
        try:
            audio = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            await self._send_error("audio_frame_invalid_base64")
            return
        if not audio:
            return
        if len(audio) > MAX_AUDIO_FRAME_BYTES:
            await self._mark_terminal_error("audio_frame_too_large")
            return
        await self._transcriber.send_audio(audio)

    async def _finish_audio(self) -> None:
        if self._state != "listening" or self._transcriber is None:
            return
        self._state = "finishing"
        await self._transcriber.finish()
        if self._asr_task is not None:
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._asr_task),
                    timeout=DEBUG_SPEAKER_FINISH_DRAIN_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                await self._mark_terminal_error("debug_speaker_finish_timeout")
        if not self._terminal_error:
            self._state = "ready"
            await self.client.send_json(self._event("audio.ended"))
        await self._close_transcriber()

    async def _forward_asr_events(self) -> None:
        if self._transcriber is None:
            return
        try:
            async for event in self._transcriber.events():
                await self._send_asr_event(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._mark_terminal_error(str(exc))

    async def _send_asr_event(self, event: StreamingASREvent) -> None:
        if event.type == "error":
            await self._mark_terminal_error(event.error or "debug_speaker_provider_error")
            return
        segments = self._segments_for_event(event)
        await self._send_speaker_label_status(event, segments)
        for segment in segments:
            await self.client.send_json(
                self._event(
                    "speaker.final" if event.type == "final" else "speaker.partial",
                    segment_id=segment.segment_id,
                    speaker_id=segment.speaker_id,
                    text=normalize_memoir_asr_text(segment.text),
                    start_ms=segment.start_ms,
                    end_ms=segment.end_ms,
                    confidence=segment.confidence,
                    provider_event_type=event.type,
                    provider_payload=segment.provider_payload or event.raw,
                )
            )

    async def _send_speaker_label_status(
        self,
        event: StreamingASREvent,
        segments: Sequence[DebugSpeakerSegment],
    ) -> None:
        if any(segment.speaker_id != "speaker_unknown" for segment in segments):
            if self._sent_available_speaker_labels_status:
                return
            self._sent_available_speaker_labels_status = True
            await self.client.send_json(
                self._event(
                    "speaker.diarization",
                    status="available",
                    message="已收到说话人标签，会按不同人分窗口显示。",
                    provider_event_type=event.type,
                )
            )
            return
        if not event.text.strip() or self._sent_missing_speaker_labels_status:
            return
        self._sent_missing_speaker_labels_status = True
        await self.client.send_json(
            self._event(
                "speaker.diarization",
                status="missing_provider_labels",
                message="当前 ASR 返回结果没有说话人标签，暂时只能显示在一个窗口；请确认服务商已开启说话人聚类分离。",
                provider_event_type=event.type,
            )
        )

    def _segments_for_event(self, event: StreamingASREvent) -> list[DebugSpeakerSegment]:
        raw_segments = speaker_segments_from_asr_event(event)
        if raw_segments:
            return [
                DebugSpeakerSegment(
                    segment_id=self._next_segment_id(),
                    speaker_id=segment.speaker_id,
                    text=segment.text,
                    start_ms=segment.start_ms,
                    end_ms=segment.end_ms,
                    confidence=segment.confidence,
                    provider_payload=segment.provider_payload,
                )
                for segment in raw_segments
            ]
        return [
            DebugSpeakerSegment(
                segment_id=self._next_segment_id(),
                speaker_id="speaker_unknown",
                text=event.text,
                start_ms=event.start_ms,
                end_ms=event.end_ms,
                provider_payload=event.raw,
            )
        ]

    def _next_segment_id(self) -> str:
        segment_id = f"seg_{self._next_segment_index}"
        self._next_segment_index += 1
        return segment_id

    async def _send_error(self, error: str, detail: dict[str, Any] | None = None) -> None:
        payload = self._event("session.error", error=error)
        if detail:
            payload["detail"] = detail
        await self.client.send_json(payload)

    async def _mark_terminal_error(self, error: str) -> None:
        if not self._terminal_error:
            self._terminal_error = error
        self._state = "failed"
        self._terminal_stop.set()
        await self._send_error(error)

    async def _shutdown(self) -> None:
        await self._close_transcriber()

    async def _close_transcriber(self) -> None:
        if self._asr_task is not None and not self._asr_task.done():
            self._asr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._asr_task
        self._asr_task = None
        if self._transcriber is not None:
            await self._transcriber.close()
            self._transcriber = None

    def _event(self, event_type: str, **payload: Any) -> dict[str, Any]:
        return {
            "type": event_type,
            "state": self._state,
            **payload,
        }


def debug_speaker_cleanup_context(
    context_lines: Sequence[DebugSpeakerProofreadContextLine],
) -> MemoirTranscriptCleanupContext:
    utterances: list[str] = []
    for line in context_lines[-DEBUG_SPEAKER_PROOFREAD_CONTEXT_LIMIT:]:
        text = " ".join(line.text.split()).strip()
        if not text:
            continue
        label = _speaker_context_label(line.speaker_id)
        prefix = f"{label}：" if label else ""
        utterances.append(f"{prefix}{text[:DEBUG_SPEAKER_PROOFREAD_CONTEXT_TEXT_LIMIT]}")
    return MemoirTranscriptCleanupContext(
        scene="多人实时转写调试",
        recent_user_utterances=tuple(utterances),
    )


async def proofread_debug_speaker_transcript_text(
    text: str,
    *,
    context_lines: Sequence[DebugSpeakerProofreadContextLine] = (),
    ai_model: str | None = None,
) -> CleanedTranscriptText:
    return await generate_cleaned_transcript_text(
        text,
        ai_model=ai_model,
        context=debug_speaker_cleanup_context(context_lines),
        strict=False,
    )


def _speaker_context_label(speaker_id: str | None) -> str:
    value = (speaker_id or "").strip()
    if not value:
        return ""
    if value == "speaker_unknown":
        return "未分辨说话人"
    if value.startswith("speaker_"):
        suffix = value.removeprefix("speaker_").strip()
        return f"说话人 {suffix}" if suffix else "说话人"
    return value


def speaker_segments_from_asr_event(event: StreamingASREvent) -> list[DebugSpeakerSegment]:
    payload = _provider_payload(event.raw)
    if payload is None:
        return []
    utterances = _provider_utterances(payload)
    segments: list[DebugSpeakerSegment] = []
    for index, utterance in enumerate(utterances, start=1):
        text = str(utterance.get("text") or "").strip()
        speaker = _speaker_id(utterance)
        if not text or not speaker:
            continue
        segments.append(
            DebugSpeakerSegment(
                segment_id=f"provider_{index}",
                speaker_id=speaker,
                text=text,
                start_ms=_optional_int(utterance.get("start_ms") or utterance.get("start_time")),
                end_ms=_optional_int(utterance.get("end_ms") or utterance.get("end_time")),
                confidence=_optional_float(utterance.get("confidence")),
                provider_payload=utterance,
            )
        )
    if segments:
        return segments
    for index, base in enumerate(_provider_text_bases(payload), start=1):
        text = str(base.get("text") or "").strip()
        speaker = _speaker_id(base)
        if not text or not speaker:
            continue
        segments.append(
            DebugSpeakerSegment(
                segment_id=f"provider_{index}",
                speaker_id=speaker,
                text=text,
                start_ms=_optional_int(base.get("start_ms") or base.get("start_time")),
                end_ms=_optional_int(base.get("end_ms") or base.get("end_time")),
                confidence=_optional_float(base.get("confidence")),
                provider_payload=base,
            )
        )
    return segments


def _provider_payload(raw: dict[str, Any]) -> dict[str, Any] | None:
    payload = raw.get("payload") if isinstance(raw, dict) else None
    return payload if isinstance(payload, dict) else None


def _provider_utterances(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for base in _provider_text_bases(payload):
        utterances = base.get("utterances")
        if isinstance(utterances, list):
            return [item for item in utterances if isinstance(item, dict)]
    return []


def _provider_text_bases(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result = payload.get("result")
    bases: list[dict[str, Any]] = []
    if isinstance(result, list):
        bases.extend(item for item in result if isinstance(item, dict))
    elif isinstance(result, dict):
        bases.append(result)
    bases.append(payload)
    return bases


SPEAKER_ID_KEYS = (
    "speaker_id",
    "speaker",
    "speaker_label",
    "spk_id",
    "spk",
    "speakerId",
    "speakerID",
    "speaker_no",
    "speakerNo",
)

SPEAKER_CONTAINER_KEYS = ("speaker_info", "speakerInfo", "additions", "speaker")


def _speaker_id(utterance: dict[str, Any]) -> str:
    for key in SPEAKER_ID_KEYS:
        speaker = _speaker_id_from_value(utterance.get(key))
        if speaker:
            return speaker
    for key in SPEAKER_CONTAINER_KEYS:
        container = _as_dict(utterance.get(key))
        if container is None:
            continue
        speaker = _speaker_id_from_mapping(container)
        if speaker:
            return speaker
    return ""


def _speaker_id_from_mapping(value: dict[str, Any]) -> str:
    for key in SPEAKER_ID_KEYS + ("id", "label"):
        speaker = _speaker_id_from_value(value.get(key))
        if speaker:
            return speaker
    return ""


def _speaker_id_from_value(value: Any) -> str:
    if value is None:
        return ""
    nested = _as_dict(value)
    if nested is not None:
        return _speaker_id_from_mapping(nested)
    if isinstance(value, (list, tuple)):
        for item in value:
            speaker = _speaker_id_from_value(item)
            if speaker:
                return speaker
        return ""
    if isinstance(value, bool):
        return ""
    text = str(value).strip()
    if not text:
        return ""
    return _normalize_speaker_id(text)


def _as_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw.startswith("{"):
        return None
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _normalize_speaker_id(value: Any) -> str:
    text = str(value).strip().lower()
    if not text.startswith("speaker_"):
        return f"speaker_{text}"
    return text


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
