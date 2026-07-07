from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Protocol

from app.domain.memoir.types import MemoirSpeakerRole
from app.services.memoir.asr import StreamingASRClient, StreamingASREvent
from app.services.memoir.asr_text_normalization import normalize_memoir_asr_text

logger = logging.getLogger(__name__)


class VoiceGatewayClient(Protocol):
    async def receive_json(self) -> dict[str, Any]: ...

    async def send_json(self, payload: dict[str, Any]) -> None: ...

    async def close(self, code: int = 1000) -> None: ...


StreamingASRClientFactory = Callable[[], StreamingASRClient]
SessionStartHandler = Callable[[], Awaitable[None]]
TurnStartHandler = Callable[["VoiceGatewayTurnContext"], Awaitable[None]]
SessionStopHandler = Callable[[str | None], Awaitable[None]]
AudioFrameHandler = Callable[[bytes], Awaitable[None]]
ASRFinalHandler = Callable[[StreamingASREvent], Awaitable[dict[str, Any] | None]]
ASR_FINISH_DRAIN_TIMEOUT_SECONDS = 10.0
MAX_AUDIO_FRAME_BYTES = 256 * 1024


class VoiceGatewayClientError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class VoiceGatewayState(str, Enum):
    READY = "ready"
    LISTENING = "listening"
    FINISHING = "finishing"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(frozen=True)
class VoiceGatewayTurnContext:
    speaker_role: str = MemoirSpeakerRole.NARRATOR.value
    parent_message_id: str | None = None


class VoiceGatewaySession:
    def __init__(
        self,
        *,
        client: VoiceGatewayClient,
        asr_client_factory: StreamingASRClientFactory,
        on_start: SessionStartHandler | None = None,
        on_turn_start: TurnStartHandler | None = None,
        on_stop: SessionStopHandler | None = None,
        on_audio_frame: AudioFrameHandler | None = None,
        on_asr_final: ASRFinalHandler | None = None,
    ) -> None:
        self.client = client
        self.asr_client_factory = asr_client_factory
        self.on_start = on_start
        self.on_turn_start = on_turn_start
        self.on_stop = on_stop
        self.on_audio_frame = on_audio_frame
        self.on_asr_final = on_asr_final
        self._asr_client: StreamingASRClient | None = None
        self._asr_task: asyncio.Task[None] | None = None
        self._state = VoiceGatewayState.READY
        self._terminal_error: str | None = None
        self._terminal_stop = asyncio.Event()
        self._pending_final_event: StreamingASREvent | None = None
        self._latest_text_event: StreamingASREvent | None = None
        self._turn_context = VoiceGatewayTurnContext()

    async def run(self) -> None:
        await self.client.send_json(self._event("session.ready"))
        stop_error: str | None = None
        try:
            while not self._terminal_stop.is_set():
                message = await self._receive_next_message()
                if message is None:
                    stop_error = self._terminal_error
                    break
                should_stop = await self._handle_client_message(message)
                if should_stop:
                    break
        except Exception as exc:
            stop_error = str(exc)
            self._state = VoiceGatewayState.FAILED
            with contextlib.suppress(Exception):
                await self._send_error(stop_error)
        finally:
            stop_error = stop_error or self._terminal_error
            await self._shutdown()
            self._state = VoiceGatewayState.FAILED if stop_error else VoiceGatewayState.STOPPED
            if self.on_stop:
                await self.on_stop(stop_error)
            with contextlib.suppress(Exception):
                await self.client.send_json(
                    self._event(
                        "session.stopped",
                        status="failed" if stop_error else "stopped",
                        error=stop_error or "",
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
            await self._start_asr(message)
            return False
        if message_type == "audio.frame":
            await self._send_audio_frame(message)
            return False
        if message_type == "audio.end":
            await self._finish_audio()
            return self._terminal_error is not None
        if message_type == "session.stop":
            if self._state == VoiceGatewayState.LISTENING and self._terminal_error is None:
                await self._finish_audio()
            return True

        await self._send_error("unsupported_gateway_event", {"received_type": message_type})
        return False

    async def _start_asr(self, message: dict[str, Any]) -> None:
        if self._state == VoiceGatewayState.LISTENING:
            await self.client.send_json(
                self._event("session.started", speaker_role=self._turn_context.speaker_role)
            )
            return
        if self._state == VoiceGatewayState.FINISHING:
            await self._send_error("asr_session_finishing")
            return

        await self._close_asr_client()
        self._pending_final_event = None
        self._latest_text_event = None
        self._turn_context = self._turn_context_from_message(message)
        self._state = VoiceGatewayState.LISTENING
        self._asr_client = self.asr_client_factory()
        await self._asr_client.start()
        if self.on_turn_start:
            await self.on_turn_start(self._turn_context)
        if self.on_start:
            await self.on_start()
        self._asr_task = asyncio.create_task(self._forward_asr_events())
        await self.client.send_json(
            self._event(
                "session.started",
                speaker_role=self._turn_context.speaker_role,
            )
        )

    def _turn_context_from_message(self, message: dict[str, Any]) -> VoiceGatewayTurnContext:
        raw_role = message.get("speaker_role") or MemoirSpeakerRole.NARRATOR.value
        speaker_role = str(raw_role).strip()
        if speaker_role not in {MemoirSpeakerRole.NARRATOR.value, MemoirSpeakerRole.INTERVIEWER.value}:
            raise VoiceGatewayClientError("unsupported_speaker_role")
        raw_parent_message_id = message.get("parent_message_id")
        parent_message_id = str(raw_parent_message_id).strip() if raw_parent_message_id else None
        return VoiceGatewayTurnContext(
            speaker_role=speaker_role,
            parent_message_id=parent_message_id,
        )

    async def _send_audio_frame(self, message: dict[str, Any]) -> None:
        if self._state != VoiceGatewayState.LISTENING or self._asr_client is None:
            await self._send_error(self._audio_frame_state_error())
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
            await self._mark_terminal_error("audio_frame_too_large", {"max_bytes": MAX_AUDIO_FRAME_BYTES})
            return
        if self.on_audio_frame:
            try:
                await self.on_audio_frame(audio)
            except VoiceGatewayClientError as exc:
                await self._mark_terminal_error(exc.code)
                return
        await self._asr_client.send_audio(audio)

    async def _finish_audio(self) -> None:
        if self._state == VoiceGatewayState.READY and self._asr_client is None:
            return
        if self._state != VoiceGatewayState.LISTENING or self._asr_client is None:
            return
        self._state = VoiceGatewayState.FINISHING
        await self._asr_client.finish()
        await self._drain_asr_events()
        if self._terminal_error is None:
            await self._persist_pending_final_event()
        if self._terminal_error is None:
            self._state = VoiceGatewayState.READY
            await self.client.send_json(self._event("audio.ended"))
        await self._close_asr_client()

    async def _drain_asr_events(self) -> None:
        if self._asr_task is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(self._asr_task), timeout=ASR_FINISH_DRAIN_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            await self._mark_terminal_error("asr_finish_timeout")

    async def _forward_asr_events(self) -> None:
        if self._asr_client is None:
            return
        try:
            async for event in self._asr_client.events():
                await self._send_asr_event(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._mark_terminal_error(str(exc))

    async def _send_asr_event(self, event: StreamingASREvent) -> None:
        if event.type == "error":
            await self._mark_terminal_error(event.error or "asr_provider_error", event.raw)
            return

        if event.text.strip():
            self._latest_text_event = event

        if event.type == "final":
            self._pending_final_event = event
            await self.client.send_json(
                self._asr_event_payload(
                    event,
                    event_type="asr.partial",
                    provider_event_type="final",
                )
            )
            return

        await self.client.send_json(self._asr_event_payload(event, event_type=f"asr.{event.type}"))

    async def _persist_asr_final(self, event: StreamingASREvent, *, turn_final: bool) -> dict[str, Any] | None:
        text = (event.text or "").strip()
        if not text:
            return None

        payload = self._asr_event_payload(
            event,
            event_type="asr.final",
            provider_event_type=(
                "partial_fallback" if (event.raw or {}).get("fallback_from_partial") else event.type
            ),
            segment_final=True,
            turn_final=turn_final,
            speaker_role=self._turn_context.speaker_role,
        )
        try:
            persisted = await self.on_asr_final(event) if self.on_asr_final else None
        except Exception:
            logger.exception("Failed to persist final ASR event")
            await self._mark_terminal_error("asr_final_persistence_failed")
            return None
        if persisted:
            payload.update(persisted)
        self._latest_text_event = None
        await self.client.send_json(payload)
        return payload

    async def _persist_pending_final_event(self) -> None:
        if self.on_asr_final:
            event = self._final_event_for_persistence()
            if event is not None:
                payload = await self._persist_asr_final(event, turn_final=True)
                if payload is not None:
                    return
            return

        event = self._final_event_for_persistence()
        if event is None:
            return
        payload = self._asr_event_payload(event, event_type="asr.final")
        if self._pending_final_event is None:
            payload["provider_event_type"] = "partial_fallback"
        self._pending_final_event = None
        self._latest_text_event = None
        await self.client.send_json(payload)

    def _final_event_for_persistence(self) -> StreamingASREvent | None:
        if self._pending_final_event is not None:
            return self._pending_final_event
        if self._latest_text_event is None:
            return None
        raw = dict(self._latest_text_event.raw or {})
        raw["fallback_from_partial"] = True
        return StreamingASREvent(
            type="final",
            text=self._latest_text_event.text,
            start_ms=self._latest_text_event.start_ms,
            end_ms=self._latest_text_event.end_ms,
            raw=raw,
            error=self._latest_text_event.error,
        )

    def _asr_event_payload(
        self,
        event: StreamingASREvent,
        *,
        event_type: str,
        **extra: Any,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": event_type,
            "state": self._state.value,
            "text": normalize_memoir_asr_text(event.text),
            "start_ms": event.start_ms,
            "end_ms": event.end_ms,
            "provider_payload": event.raw,
            **extra,
        }
        return payload

    async def _send_error(self, error: str, detail: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = self._event("session.error", error=error)
        if detail:
            payload["detail"] = detail
        await self.client.send_json(payload)

    async def _mark_terminal_error(self, error: str, detail: dict[str, Any] | None = None) -> None:
        if self._terminal_error is None:
            self._terminal_error = error
        self._state = VoiceGatewayState.FAILED
        self._terminal_stop.set()
        await self._send_error(error, detail)

    async def _shutdown(self) -> None:
        await self._close_asr_client()

    async def _close_asr_client(self) -> None:
        if self._asr_task is not None and not self._asr_task.done():
            self._asr_task.cancel()
            try:
                await self._asr_task
            except asyncio.CancelledError:
                pass
        self._asr_task = None
        if self._asr_client is not None:
            await self._asr_client.close()
            self._asr_client = None

    def _audio_frame_state_error(self) -> str:
        if self._state == VoiceGatewayState.FINISHING:
            return "asr_session_finishing"
        if self._state == VoiceGatewayState.FAILED:
            return "asr_session_failed"
        if self._state == VoiceGatewayState.STOPPED:
            return "asr_session_stopped"
        return "asr_session_not_started"

    def _event(self, event_type: str, **payload: Any) -> dict[str, Any]:
        return {
            "type": event_type,
            "state": self._state.value,
            **payload,
        }
