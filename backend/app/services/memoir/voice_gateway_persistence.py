from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any
import uuid

import aiofiles
import aiofiles.os
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import MemoirSpeakerRole
from app.services.memoir import audio_files as memoir_audio_files
from app.services.asr.gateway import VoiceGatewayClientError, VoiceGatewayTurnContext
from app.services.asr.streaming import StreamingASREvent
from app.services.memoir.voice_final_persistence import VoiceFinalPersistenceService


class PendingAudioFileBuffer:
    def __init__(self, *, user_id: str, voice_session_id: str) -> None:
        self.user_id = user_id
        self.voice_session_id = voice_session_id
        self._path: Path | None = None
        self._size_bytes = 0

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def size_bytes(self) -> int:
        return self._size_bytes

    async def append(self, audio: bytes) -> None:
        if self._size_bytes + len(audio) > memoir_audio_files.MAX_AUDIO_BYTES:
            raise VoiceGatewayClientError("audio_stream_too_large")
        if not audio:
            return
        path = self._ensure_path()
        async with aiofiles.open(path, "ab") as file:
            await file.write(audio)
        self._size_bytes += len(audio)

    async def stage(self, db: AsyncSession) -> memoir_audio_files.StagedAudioFile | None:
        if self._path is None or self._size_bytes == 0:
            return None
        staged = await memoir_audio_files.stage_audio_path(
            db,
            self.user_id,
            self._path,
            mime_type="audio/pcm",
            extension=".pcm",
            move_source=False,
        )
        return staged

    async def mark_committed(self) -> None:
        with contextlib.suppress(Exception):
            if self._path is not None and self._path.exists():
                await aiofiles.os.remove(self._path)
        self._path = None
        self._size_bytes = 0

    async def discard(self) -> None:
        if self._path is not None and self._path.exists():
            await aiofiles.os.remove(self._path)
        self._path = None
        self._size_bytes = 0

    def _ensure_path(self) -> Path:
        if self._path is None:
            pending_dir = memoir_audio_files.MEMOIR_AUDIO_DIR / "_pending" / self.user_id
            pending_dir.mkdir(parents=True, exist_ok=True)
            self._path = pending_dir / f"{self.voice_session_id}-{uuid.uuid4().hex}.pcm.part"
        return self._path


class VoiceGatewayPersistence:
    def __init__(
        self,
        *,
        db: AsyncSession,
        voice_session_id: str,
        user_id: str,
        provider: str = "volc_streaming_asr",
    ) -> None:
        self.db = db
        self.voice_session_id = voice_session_id
        self.user_id = user_id
        self.provider = provider
        self._service = VoiceFinalPersistenceService(
            db=db,
            voice_session_id=voice_session_id,
            user_id=user_id,
            provider=provider,
        )
        self._pending_audio = PendingAudioFileBuffer(
            user_id=user_id,
            voice_session_id=voice_session_id,
        )
        self._turn_context = VoiceGatewayTurnContext()

    async def set_turn_context(self, context: VoiceGatewayTurnContext) -> None:
        speaker_role = context.speaker_role or MemoirSpeakerRole.NARRATOR.value
        self._turn_context = VoiceGatewayTurnContext(
            speaker_role=speaker_role,
            parent_message_id=context.parent_message_id,
            ai_model=context.ai_model,
        )

    async def record_audio_frame(self, audio: bytes) -> None:
        await self._pending_audio.append(audio)

    async def persist_segment(self, event: StreamingASREvent) -> dict[str, Any]:
        staged_audio = await self._pending_audio.stage(self.db)
        result = await self._service.persist_segment(
            event,
            staged_audio=staged_audio,
            speaker_role=self._turn_context.speaker_role,
            parent_message_id=self._turn_context.parent_message_id,
            ai_model=self._turn_context.ai_model,
        )
        await self._pending_audio.mark_committed()
        return result.as_gateway_payload()

    async def persist_final(self, event: StreamingASREvent) -> dict[str, Any]:
        return await self.persist_segment(event)

    async def flush_unfinalized_audio(self) -> dict[str, Any] | None:
        staged_audio = await self._pending_audio.stage(self.db)
        result = await self._service.persist_unfinalized_audio(
            staged_audio=staged_audio,
            speaker_role=self._turn_context.speaker_role,
        )
        if result is None:
            return None
        await self._pending_audio.mark_committed()
        return result.as_gateway_payload()

    async def discard_pending_audio(self) -> None:
        await self._pending_audio.discard()

    @property
    def pending_audio_path(self) -> Path | None:
        return self._pending_audio.path

    @property
    def pending_audio_size_bytes(self) -> int:
        return self._pending_audio.size_bytes
