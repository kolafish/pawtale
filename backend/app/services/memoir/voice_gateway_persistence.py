from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.memoir.asr import StreamingASREvent
from app.services.memoir.voice_final_persistence import VoiceFinalPersistenceService


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
        self._pending_audio = bytearray()

    async def record_audio_frame(self, audio: bytes) -> None:
        self._pending_audio.extend(audio)

    async def persist_final(self, event: StreamingASREvent) -> dict[str, Any]:
        audio = self._pending_audio_bytes()
        result = await self._service.persist_final(event, audio=audio)
        self._pending_audio.clear()
        return result.as_gateway_payload()

    async def flush_unfinalized_audio(self) -> dict[str, Any] | None:
        audio = self._pending_audio_bytes()
        result = await self._service.persist_unfinalized_audio(audio)
        if result is None:
            return None
        self._pending_audio.clear()
        return result.as_gateway_payload()

    def _pending_audio_bytes(self) -> bytes | None:
        if not self._pending_audio:
            return None
        return bytes(self._pending_audio)
