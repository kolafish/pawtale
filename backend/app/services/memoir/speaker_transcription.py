from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
from typing import Any, Callable, Protocol, Sequence

from app.services.memoir.asr import StreamingASRClient, StreamingASREvent
from app.services.memoir.asr_text_normalization import normalize_memoir_asr_text
from app.services.memoir.speaker_diarization import (
    SpeakerSegment,
    fallback_segment_key,
    live_text_after_confirmed_prefix,
    speaker_segments_from_asr_event,
)
from app.services.memoir.voice_gateway import MAX_AUDIO_FRAME_BYTES


SpeakerTranscriberFactory = Callable[[], StreamingASRClient]
SPEAKER_TRANSCRIPTION_FINISH_DRAIN_TIMEOUT_SECONDS = 10.0
LIVE_PARTIAL_SEGMENT_KEY = "live:current"


class SpeakerTranscriptionClient(Protocol):
    async def receive_json(self) -> dict[str, Any]: ...

    async def send_json(self, payload: dict[str, Any]) -> None: ...

    async def close(self, code: int = 1000) -> None: ...


class SpeakerTranscriptionSession:
    """Ephemeral realtime speaker-attributed ASR session.

    Audio and transcripts are streamed to the client and are not persisted by
    this service. Speaker attribution comes only from provider labels.
    """

    def __init__(
        self,
        *,
        client: SpeakerTranscriptionClient,
        transcriber_factory: SpeakerTranscriberFactory,
        live_transcriber_factory: SpeakerTranscriberFactory | None = None,
    ) -> None:
        self.client = client
        self.transcriber_factory = transcriber_factory
        self.live_transcriber_factory = live_transcriber_factory
        self._transcriber: StreamingASRClient | None = None
        self._live_transcriber: StreamingASRClient | None = None
        self._asr_task: asyncio.Task[None] | None = None
        self._live_asr_task: asyncio.Task[None] | None = None
        self._state = "ready"
        self._terminal_error = ""
        self._terminal_stop = asyncio.Event()
        self._next_segment_index = 1
        self._segment_ids_by_key: dict[str, str] = {}
        self._segment_text_by_key: dict[str, str] = {}
        self._final_segment_keys: set[str] = set()
        self._confirmed_final_keys: list[str] = []
        self._confirmed_text_by_key: dict[str, str] = {}
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
        await self._send_error("unsupported_speaker_transcription_event", {"received_type": message_type})
        return False

    async def _start_transcriber(self) -> None:
        if self._state == "listening":
            await self.client.send_json(self._event("session.started"))
            return
        if self._state == "finishing":
            await self._send_error("speaker_transcription_session_finishing")
            return
        await self._close_transcriber()
        self._state = "listening"
        self._transcriber = self.transcriber_factory()
        await self._transcriber.start()
        self._asr_task = asyncio.create_task(self._forward_asr_events())
        if self.live_transcriber_factory is not None:
            self._live_transcriber = self.live_transcriber_factory()
            await self._live_transcriber.start()
            self._live_asr_task = asyncio.create_task(self._forward_live_asr_events())
        await self.client.send_json(self._event("session.started"))

    async def _send_audio_frame(self, message: dict[str, Any]) -> None:
        if self._state != "listening" or self._transcriber is None:
            await self._send_error("speaker_transcription_session_not_started")
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
        if self._live_transcriber is not None:
            await self._live_transcriber.send_audio(audio)

    async def _finish_audio(self) -> None:
        if self._state != "listening" or self._transcriber is None:
            return
        self._state = "finishing"
        await self._transcriber.finish()
        if self._live_transcriber is not None:
            await self._live_transcriber.finish()
        await self._drain_asr_task(self._asr_task)
        await self._drain_asr_task(self._live_asr_task)
        if not self._terminal_error:
            self._state = "ready"
            await self.client.send_json(self._event("audio.ended"))
        await self._close_transcriber()

    async def _drain_asr_task(self, task: asyncio.Task[None] | None) -> None:
        if task is None:
            return
        try:
            await asyncio.wait_for(
                asyncio.shield(task),
                timeout=SPEAKER_TRANSCRIPTION_FINISH_DRAIN_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            await self._mark_terminal_error("speaker_transcription_finish_timeout")

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

    async def _forward_live_asr_events(self) -> None:
        if self._live_transcriber is None:
            return
        try:
            async for event in self._live_transcriber.events():
                await self._send_live_asr_event(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._mark_terminal_error(str(exc))

    async def _send_asr_event(self, event: StreamingASREvent) -> None:
        if event.type == "error":
            await self._mark_terminal_error(event.error or "speaker_transcription_provider_error")
            return
        segments = self._segments_for_event(event)
        await self._send_speaker_label_status(event, segments)
        for segment in segments:
            event_type = "speaker.final" if event.type == "final" else "speaker.partial"
            await self.client.send_json(
                self._event(
                    event_type,
                    segment_id=segment.segment_id,
                    speaker_id=segment.speaker_id,
                    speaker_gender=segment.speaker_gender,
                    text=segment.text,
                    start_ms=segment.start_ms,
                    end_ms=segment.end_ms,
                    confidence=segment.confidence,
                )
            )
            if event_type == "speaker.final":
                self._remember_confirmed_segment(segment)

    async def _send_live_asr_event(self, event: StreamingASREvent) -> None:
        if event.type == "error":
            await self._mark_terminal_error(event.error or "speaker_transcription_live_provider_error")
            return
        text = live_text_after_confirmed_prefix(
            normalize_memoir_asr_text(event.text).strip(),
            self._confirmed_text(),
        )
        if not text:
            return
        segments = self._dedupe_segments([
            SpeakerSegment(
                segment_id="live_current",
                speaker_id="speaker_unknown",
                text=text,
                stable_key=LIVE_PARTIAL_SEGMENT_KEY,
                start_ms=event.start_ms,
                end_ms=event.end_ms,
            )
        ], event_type="partial")
        for segment in segments:
            await self.client.send_json(
                self._event(
                    "speaker.partial",
                    segment_id=segment.segment_id,
                    speaker_id=segment.speaker_id,
                    speaker_gender=segment.speaker_gender,
                    text=segment.text,
                    start_ms=segment.start_ms,
                    end_ms=segment.end_ms,
                    confidence=segment.confidence,
                )
            )

    async def _send_speaker_label_status(
        self,
        event: StreamingASREvent,
        segments: Sequence[SpeakerSegment],
    ) -> None:
        if any(segment.speaker_id != "speaker_unknown" for segment in segments):
            if self._sent_available_speaker_labels_status:
                return
            self._sent_available_speaker_labels_status = True
            await self.client.send_json(
                self._event(
                    "speaker.diarization",
                    status="available",
                    message="已收到说话人标签，会按不同人标注显示。",
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
                message=(
                    "当前 ASR 返回结果没有说话人标签，暂时只能标成未分辨说话人；"
                    "请确认服务商已开启说话人聚类分离。"
                ),
            )
        )

    def _segments_for_event(self, event: StreamingASREvent) -> list[SpeakerSegment]:
        raw_segments = speaker_segments_from_asr_event(event)
        if raw_segments:
            return self._dedupe_segments(raw_segments, event_type=event.type)
        return self._dedupe_segments([
            SpeakerSegment(
                segment_id="",
                speaker_id="speaker_unknown",
                text=event.text,
                stable_key=fallback_segment_key(
                    speaker_id="speaker_unknown",
                    text=event.text,
                    index=1,
                    start_ms=event.start_ms,
                ),
                speaker_gender=None,
                start_ms=event.start_ms,
                end_ms=event.end_ms,
            )
        ], event_type=event.type)

    def _dedupe_segments(
        self,
        segments: Sequence[SpeakerSegment],
        *,
        event_type: str,
    ) -> list[SpeakerSegment]:
        emit_segments: list[SpeakerSegment] = []
        for index, segment in enumerate(segments, start=1):
            text = normalize_memoir_asr_text(segment.text).strip()
            if not text:
                continue
            key = segment.stable_key or fallback_segment_key(
                speaker_id=segment.speaker_id,
                text=text,
                index=index,
                start_ms=segment.start_ms,
            )
            outgoing_is_final = event_type == "final"
            if not self._should_emit_segment(key, text=text, is_final=outgoing_is_final):
                continue
            emit_segments.append(
                SpeakerSegment(
                    segment_id=self._segment_id_for_key(key),
                    speaker_id=segment.speaker_id,
                    text=text,
                    stable_key=key,
                    speaker_gender=segment.speaker_gender,
                    start_ms=segment.start_ms,
                    end_ms=segment.end_ms,
                    confidence=segment.confidence,
                )
            )
        return emit_segments

    def _should_emit_segment(self, key: str, *, text: str, is_final: bool) -> bool:
        previous_text = self._segment_text_by_key.get(key, "")
        if is_final:
            if key in self._final_segment_keys and previous_text == text:
                return False
            self._segment_text_by_key[key] = text
            self._final_segment_keys.add(key)
            return True
        if key in self._final_segment_keys or previous_text == text:
            return False
        self._segment_text_by_key[key] = text
        return True

    def _segment_id_for_key(self, key: str) -> str:
        existing = self._segment_ids_by_key.get(key)
        if existing:
            return existing
        segment_id = self._next_segment_id()
        self._segment_ids_by_key[key] = segment_id
        return segment_id

    def _next_segment_id(self) -> str:
        segment_id = f"seg_{self._next_segment_index}"
        self._next_segment_index += 1
        return segment_id

    def _remember_confirmed_segment(self, segment: SpeakerSegment) -> None:
        key = segment.stable_key
        if not key:
            return
        if key not in self._confirmed_text_by_key:
            self._confirmed_final_keys.append(key)
        self._confirmed_text_by_key[key] = segment.text

    def _confirmed_text(self) -> str:
        return "".join(
            self._confirmed_text_by_key[key]
            for key in self._confirmed_final_keys
            if key in self._confirmed_text_by_key
        )

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
        if self._live_asr_task is not None and not self._live_asr_task.done():
            self._live_asr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._live_asr_task
        self._live_asr_task = None
        if self._transcriber is not None:
            await self._transcriber.close()
            self._transcriber = None
        if self._live_transcriber is not None:
            await self._live_transcriber.close()
            self._live_transcriber = None

    def _event(self, event_type: str, **payload: Any) -> dict[str, Any]:
        return {
            "type": event_type,
            "state": self._state,
            **payload,
        }
