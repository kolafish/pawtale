from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import logging
from typing import Any, Awaitable, Callable, Protocol

from app.services.memoir.streaming_asr import StreamingASRClient, StreamingASREvent

logger = logging.getLogger(__name__)


class VoiceGatewayClient(Protocol):
    async def receive_json(self) -> dict[str, Any]: ...

    async def send_json(self, payload: dict[str, Any]) -> None: ...

    async def close(self, code: int = 1000) -> None: ...


StreamingASRClientFactory = Callable[[], StreamingASRClient]
SessionStartHandler = Callable[[], Awaitable[None]]
SessionStopHandler = Callable[[str | None], Awaitable[None]]
AudioFrameHandler = Callable[[bytes], Awaitable[None]]
ASRFinalHandler = Callable[[StreamingASREvent], Awaitable[dict[str, Any] | None]]
ASR_FINISH_DRAIN_TIMEOUT_SECONDS = 10.0


class VoiceGatewaySession:
    def __init__(
        self,
        *,
        client: VoiceGatewayClient,
        asr_client_factory: StreamingASRClientFactory,
        on_start: SessionStartHandler | None = None,
        on_stop: SessionStopHandler | None = None,
        on_audio_frame: AudioFrameHandler | None = None,
        on_asr_final: ASRFinalHandler | None = None,
    ) -> None:
        self.client = client
        self.asr_client_factory = asr_client_factory
        self.on_start = on_start
        self.on_stop = on_stop
        self.on_audio_frame = on_audio_frame
        self.on_asr_final = on_asr_final
        self._asr_client: StreamingASRClient | None = None
        self._asr_task: asyncio.Task[None] | None = None
        self._audio_finished = False
        self._terminal_error: str | None = None
        self._terminal_stop = asyncio.Event()

    async def run(self) -> None:
        await self.client.send_json({"type": "session.ready"})
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
            with contextlib.suppress(Exception):
                await self._send_error(stop_error)
        finally:
            stop_error = stop_error or self._terminal_error
            await self._shutdown()
            if self.on_stop:
                await self.on_stop(stop_error)
            with contextlib.suppress(Exception):
                await self.client.send_json(
                    {
                        "type": "session.stopped",
                        "status": "failed" if stop_error else "stopped",
                        "error": stop_error or "",
                    }
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
            await self._start_asr()
            return False
        if message_type == "audio.frame":
            await self._send_audio_frame(message)
            return False
        if message_type == "audio.end":
            await self._finish_audio()
            return self._terminal_error is not None
        if message_type == "session.stop":
            if self._asr_client is not None and not self._audio_finished and self._terminal_error is None:
                await self._finish_audio()
            return True

        await self._send_error("unsupported_gateway_event", {"received_type": message_type})
        return False

    async def _start_asr(self) -> None:
        if self._asr_client is not None and not self._audio_finished:
            await self.client.send_json({"type": "session.started"})
            return

        await self._close_asr_client()
        self._audio_finished = False
        self._asr_client = self.asr_client_factory()
        await self._asr_client.start()
        if self.on_start:
            await self.on_start()
        self._asr_task = asyncio.create_task(self._forward_asr_events())
        await self.client.send_json({"type": "session.started"})

    async def _send_audio_frame(self, message: dict[str, Any]) -> None:
        if self._asr_client is None:
            await self._send_error("asr_session_not_started")
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
        if self.on_audio_frame:
            await self.on_audio_frame(audio)
        await self._asr_client.send_audio(audio)

    async def _finish_audio(self) -> None:
        if self._asr_client is None or self._audio_finished:
            return
        await self._asr_client.finish()
        await self._drain_asr_events()
        self._audio_finished = True
        if self._terminal_error is None:
            await self.client.send_json({"type": "audio.ended"})
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

        payload: dict[str, Any] = {
            "type": f"asr.{event.type}",
            "text": event.text,
            "start_ms": event.start_ms,
            "end_ms": event.end_ms,
            "provider_payload": event.raw,
        }
        if event.type == "final" and self.on_asr_final:
            try:
                persisted = await self.on_asr_final(event)
            except Exception:
                logger.exception("Failed to persist final ASR event")
                await self._mark_terminal_error("asr_final_persistence_failed")
                return
            if persisted:
                payload.update(persisted)
        await self.client.send_json(payload)

    async def _send_error(self, error: str, detail: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"type": "session.error", "error": error}
        if detail:
            payload["detail"] = detail
        await self.client.send_json(payload)

    async def _mark_terminal_error(self, error: str, detail: dict[str, Any] | None = None) -> None:
        if self._terminal_error is None:
            self._terminal_error = error
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
