from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import MemoirChatContentStatus, MemoirChatMessageRole
from app.schemas.memoir import MemoirChatMessageCreate, MemoirVoiceUtteranceCreate
from app.services.memoir import audio_files as memoir_audio_files
from app.services.memoir import voice_sessions as memoir_voice_sessions
from app.services.memoir.streaming_asr import StreamingASREvent


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
        self._pending_audio = bytearray()

    async def record_audio_frame(self, audio: bytes) -> None:
        self._pending_audio.extend(audio)

    async def persist_final(self, event: StreamingASREvent) -> dict[str, Any]:
        audio_file_id = await self._save_pending_audio()
        message = await memoir_voice_sessions.append_chat_message(
            self.db,
            self.voice_session_id,
            self.user_id,
            MemoirChatMessageCreate(
                role=MemoirChatMessageRole.USER,
                content_text=event.text,
                content_status=MemoirChatContentStatus.FINAL,
                audio_file_id=audio_file_id,
                metadata_json={"source": "asr.final", "provider": self.provider},
            ),
        )
        utterance = await memoir_voice_sessions.append_utterance(
            self.db,
            self.voice_session_id,
            self.user_id,
            MemoirVoiceUtteranceCreate(
                chat_message_id=message.id,
                audio_file_id=audio_file_id,
                transcript_raw=event.text,
                start_ms=event.start_ms,
                end_ms=event.end_ms,
                provider=self.provider,
                provider_payload_json=event.raw,
            ),
        )
        return {
            "message_id": message.id,
            "utterance_id": utterance.id,
            "audio_file_id": audio_file_id,
        }

    async def flush_unfinalized_audio(self) -> dict[str, Any] | None:
        audio_file_id = await self._save_pending_audio()
        if not audio_file_id:
            return None
        utterance = await memoir_voice_sessions.append_utterance(
            self.db,
            self.voice_session_id,
            self.user_id,
            MemoirVoiceUtteranceCreate(
                audio_file_id=audio_file_id,
                transcript_raw="",
                provider=self.provider,
                provider_payload_json={"source": "unfinalized_audio"},
            ),
        )
        return {
            "utterance_id": utterance.id,
            "audio_file_id": audio_file_id,
        }

    async def _save_pending_audio(self) -> str | None:
        if not self._pending_audio:
            return None
        audio = bytes(self._pending_audio)
        audio_file = await memoir_audio_files.save_audio_bytes(
            self.db,
            self.user_id,
            audio,
            mime_type="audio/pcm",
            extension=".pcm",
        )
        self._pending_audio.clear()
        return audio_file.id
