from __future__ import annotations

import asyncio
import base64
import binascii
from typing import Any, Awaitable, Callable, Protocol

from app.services.memoir.streaming_asr import StreamingASRClient, StreamingASREvent


class VoiceGatewayClient(Protocol):
    async def receive_json(self) -> dict[str, Any]: ...

    async def send_json(self, payload: dict[str, Any]) -> None: ...

    async def close(self, code: int = 1000) -> None: ...


StreamingASRClientFactory = Callable[[], StreamingASRClient]
SessionStopHandler = Callable[[str | None], Awaitable[None]]
AudioFrameHandler = Callable[[bytes], Awaitable[None]]
ASRFinalHandler = Callable[[StreamingASREvent], Awaitable[dict[str, Any] | None]]


class VoiceGatewaySession:
    def __init__(
        self,
        *,
        client: VoiceGatewayClient,
        asr_client_factory: StreamingASRClientFactory,
        on_stop: SessionStopHandler | None = None,
        on_audio_frame: AudioFrameHandler | None = None,
        on_asr_final: ASRFinalHandler | None = None,
    ) -> None:
        self.client = client
        self.asr_client_factory = asr_client_factory
        self.on_stop = on_stop
        self.on_audio_frame = on_audio_frame
        self.on_asr_final = on_asr_final
        self._asr_client: StreamingASRClient | None = None
        self._asr_task: asyncio.Task[None] | None = None

    async def run(self) -> None:
        await self.client.send_json({"type": "session.ready"})
        stop_error: str | None = None
        try:
            while True:
                message = await self.client.receive_json()
                should_stop = await self._handle_client_message(message)
                if should_stop:
                    break
        except Exception as exc:
            stop_error = str(exc)
            await self._send_error(stop_error)
        finally:
            await self._shutdown()
            if self.on_stop:
                await self.on_stop(stop_error)

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
            return False
        if message_type == "session.stop":
            return True

        await self._send_error("unsupported_gateway_event", {"received_type": message_type})
        return False

    async def _start_asr(self) -> None:
        if self._asr_client is not None:
            await self.client.send_json({"type": "session.started"})
            return

        self._asr_client = self.asr_client_factory()
        await self._asr_client.start()
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
        if self._asr_client is None:
            return
        await self._asr_client.finish()
        await self.client.send_json({"type": "audio.ended"})

    async def _forward_asr_events(self) -> None:
        if self._asr_client is None:
            return
        try:
            async for event in self._asr_client.events():
                await self._send_asr_event(event)
        except Exception as exc:
            await self._send_error(str(exc))

    async def _send_asr_event(self, event: StreamingASREvent) -> None:
        if event.type == "error":
            await self._send_error(event.error or "asr_provider_error", event.raw)
            return

        payload: dict[str, Any] = {
            "type": f"asr.{event.type}",
            "text": event.text,
            "start_ms": event.start_ms,
            "end_ms": event.end_ms,
            "provider_payload": event.raw,
        }
        if event.type == "final" and self.on_asr_final:
            persisted = await self.on_asr_final(event)
            if persisted:
                payload.update(persisted)
        await self.client.send_json(payload)

    async def _send_error(self, error: str, detail: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"type": "session.error", "error": error}
        if detail:
            payload["detail"] = detail
        await self.client.send_json(payload)

    async def _shutdown(self) -> None:
        if self._asr_task is not None:
            self._asr_task.cancel()
            try:
                await self._asr_task
            except asyncio.CancelledError:
                pass
            self._asr_task = None
        if self._asr_client is not None:
            await self._asr_client.close()
            self._asr_client = None
