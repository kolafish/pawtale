from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import Protocol
import uuid

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.memoir.access import load_memoir_project
from app.services.memoir.book_voice_commands import load_book_plan
from app.services.memoir.streaming_tts_events import (
    STREAM_TTS_AUDIO_PROTOCOL_PCM_FRAME,
    normalize_audio_protocol,
    single_text_chunk,
    stream_tts_events,
)
from app.services.memoir.voice_tts import TTSClient, get_tts_client


logger = logging.getLogger(__name__)


class BookVoiceAssistantTTSStreamClient(Protocol):
    async def receive_json(self) -> dict:
        raise NotImplementedError

    async def send_json(self, payload: dict) -> None:
        raise NotImplementedError


@dataclass(frozen=True)
class BookVoiceAssistantTTSStartRequest:
    text: str
    voice: str | None = None
    speed_ratio: float | None = None
    audio_protocol: str = STREAM_TTS_AUDIO_PROTOCOL_PCM_FRAME

    @classmethod
    def from_payload(cls, payload: dict) -> "BookVoiceAssistantTTSStartRequest":
        text = str(payload.get("text") or "").strip()
        speed_ratio = payload.get("speed_ratio")
        try:
            normalized_speed_ratio = float(speed_ratio) if speed_ratio is not None else None
        except (TypeError, ValueError):
            normalized_speed_ratio = None
        if normalized_speed_ratio is not None:
            normalized_speed_ratio = max(0.5, min(2.0, normalized_speed_ratio))
        return cls(
            text=text,
            voice=_optional_text(payload.get("voice")),
            speed_ratio=normalized_speed_ratio,
            audio_protocol=normalize_audio_protocol(payload.get("audio_protocol")),
        )


class BookVoiceAssistantTTSStreamSession:
    def __init__(
        self,
        *,
        db: AsyncSession,
        client: BookVoiceAssistantTTSStreamClient,
        project_id: str,
        user_id: str,
        tts_client_factory=get_tts_client,
    ) -> None:
        self.db = db
        self.client = client
        self.project_id = project_id
        self.user_id = user_id
        self.tts_client_factory = tts_client_factory

    async def run(self) -> None:
        await self.client.send_json({"type": "tts.ready", "project_id": self.project_id, "state": "ready"})
        message = await self.client.receive_json()
        message_type = message.get("type")
        if message_type in {"connection.close", "tts.cancel"}:
            return
        if message_type != "tts.start":
            await self.client.send_json(
                {
                    "type": "tts.failed",
                    "project_id": self.project_id,
                    "state": "failed",
                    "error": "tts_start_required",
                }
            )
            return
        await self._run_stream(BookVoiceAssistantTTSStartRequest.from_payload(message))

    async def _run_stream(self, req: BookVoiceAssistantTTSStartRequest) -> None:
        stream_id = str(uuid.uuid4())
        cancel_event = asyncio.Event()
        cancel_task = asyncio.create_task(self._wait_for_cancel(cancel_event))
        stream_task = asyncio.create_task(self._stream_tts(stream_id, req, cancel_event))
        done, _pending = await asyncio.wait({cancel_task, stream_task}, return_when=asyncio.FIRST_COMPLETED)
        if cancel_task in done:
            cancel_event.set()
            stream_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stream_task
            await self.client.send_json(
                {
                    "type": "tts.cancelled",
                    "stream_id": stream_id,
                    "project_id": self.project_id,
                    "state": "cancelled",
                }
            )
            return

        cancel_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cancel_task
        try:
            await stream_task
        except Exception as exc:
            logger.exception("Book voice assistant TTS stream failed project_id=%s", self.project_id)
            await self.client.send_json(
                {
                    "type": "tts.failed",
                    "stream_id": stream_id,
                    "project_id": self.project_id,
                    "state": "failed",
                    "error": _exception_message(exc),
                }
            )

    async def _wait_for_cancel(self, cancel_event: asyncio.Event) -> None:
        while not cancel_event.is_set():
            message = await self.client.receive_json()
            if message.get("type") in {"tts.cancel", "connection.close"}:
                cancel_event.set()
                return

    async def _stream_tts(
        self,
        stream_id: str,
        req: BookVoiceAssistantTTSStartRequest,
        cancel_event: asyncio.Event,
    ) -> None:
        if not req.text:
            raise HTTPException(status_code=400, detail="AI 回复内容为空")
        await load_memoir_project(self.db, self.project_id, self.user_id)
        plan = await load_book_plan(self.db, self.project_id)
        if not plan:
            raise HTTPException(status_code=404, detail="章节规划不存在")
        tts_client = self.tts_client_factory()
        await self.client.send_json(
            {
                "type": "tts.started",
                "stream_id": stream_id,
                "project_id": self.project_id,
                "state": "streaming",
                "tts_provider": tts_client.provider_name,
                "tts_model": tts_client.model_name,
                "audio_protocol": req.audio_protocol,
            }
        )
        await stream_tts_events(
            client=self.client,
            tts_client=tts_client,
            text_chunks=single_text_chunk(req.text),
            event_context={"stream_id": stream_id, "project_id": self.project_id},
            voice=req.voice,
            speed_ratio=req.speed_ratio,
            cancel_event=cancel_event,
            audio_protocol=req.audio_protocol,
        )
        if cancel_event.is_set():
            return
        await self.client.send_json(
            {
                "type": "tts.completed",
                "stream_id": stream_id,
                "project_id": self.project_id,
                "state": "completed",
            }
        )


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _exception_message(exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        return str(exc.detail)
    return str(exc) or exc.__class__.__name__
