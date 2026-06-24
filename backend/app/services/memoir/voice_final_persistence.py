from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import (
    MemoirChatContentStatus,
    MemoirChatMessageRole,
    MemoirCreatedBy,
    MemoirTranscriptVersionType,
)
from app.models.memoir import MemoirChatMessage, MemoirTranscriptVersion, MemoirVoiceSession, MemoirVoiceUtterance
from app.models.projects import Project
from app.services.memoir import audio_files as memoir_audio_files
from app.services.memoir.asr import StreamingASREvent
from app.services.memoir.voice_transcripts import (
    generate_cleaned_transcript_text,
    prune_transcript_versions_for_utterance,
)


@dataclass(frozen=True)
class VoiceFinalPersistenceResult:
    message_id: str | None
    utterance_id: str
    audio_file_id: str | None
    transcript_version_id: str | None

    def as_gateway_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "utterance_id": self.utterance_id,
            "audio_file_id": self.audio_file_id,
        }
        if self.message_id:
            payload["message_id"] = self.message_id
        if self.transcript_version_id:
            payload["transcript_version_id"] = self.transcript_version_id
        return payload


class VoiceFinalPersistenceService:
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

    async def persist_final(
        self,
        event: StreamingASREvent,
        *,
        audio: bytes | None = None,
        staged_audio: memoir_audio_files.StagedAudioFile | None = None,
    ) -> VoiceFinalPersistenceResult:
        owned_staged_audio = staged_audio
        try:
            content = event.text.strip()
            if not content:
                raise HTTPException(status_code=400, detail="识别文字不能为空")
            _validate_audio_span(event.start_ms, event.end_ms)
            cleaned = await generate_cleaned_transcript_text(content, strict=False)

            voice_session = await self._load_voice_session()
            owned_staged_audio = await self._stage_audio_input(audio, owned_staged_audio)
            audio_file_id = owned_staged_audio.audio_file.id if owned_staged_audio else None

            transcript_version = MemoirTranscriptVersion(
                voice_session_id=voice_session.id,
                version_type=MemoirTranscriptVersionType.ASR_RAW.value,
                content=content,
                provider=self.provider,
                created_by=MemoirCreatedBy.ASR.value,
            )
            self.db.add(transcript_version)
            await self.db.flush()

            corrected_version = MemoirTranscriptVersion(
                voice_session_id=voice_session.id,
                version_type=MemoirTranscriptVersionType.DIALECT_CORRECTED.value,
                content=cleaned.content,
                provider=cleaned.provider,
                created_by=cleaned.created_by.value,
            )
            self.db.add(corrected_version)
            await self.db.flush()

            message = MemoirChatMessage(
                project_id=voice_session.project_id,
                voice_session_id=voice_session.id,
                role=MemoirChatMessageRole.USER.value,
                content_text=cleaned.content,
                content_status=MemoirChatContentStatus.CORRECTED.value,
                audio_file_id=audio_file_id,
                transcript_version_id=corrected_version.id,
                turn_index=await self._next_turn_index(voice_session.id),
                metadata_json={
                    "source": "asr.final",
                    "provider": self.provider,
                    "auto_corrected": True,
                    "correction_provider": cleaned.provider,
                    "correction_created_by": cleaned.created_by.value,
                    "asr_transcript_version_id": transcript_version.id,
                },
            )
            self.db.add(message)
            await self.db.flush()

            utterance = MemoirVoiceUtterance(
                voice_session_id=voice_session.id,
                chat_message_id=message.id,
                audio_file_id=audio_file_id,
                transcript_raw=content,
                transcript_edited=cleaned.content,
                start_ms=event.start_ms,
                end_ms=event.end_ms,
                provider=self.provider,
                provider_payload_json=event.raw,
            )
            self.db.add(utterance)
            await self.db.flush()
            transcript_version.source_artifact_id = utterance.id
            corrected_version.source_artifact_id = utterance.id
            await self.db.flush()
            await prune_transcript_versions_for_utterance(
                self.db,
                utterance_id=utterance.id,
                version_type=MemoirTranscriptVersionType.ASR_RAW,
            )
            await prune_transcript_versions_for_utterance(
                self.db,
                utterance_id=utterance.id,
                version_type=MemoirTranscriptVersionType.DIALECT_CORRECTED,
            )
            await self.db.commit()
            return VoiceFinalPersistenceResult(
                message_id=message.id,
                utterance_id=utterance.id,
                audio_file_id=audio_file_id,
                transcript_version_id=corrected_version.id,
            )
        except Exception:
            await self.db.rollback()
            await memoir_audio_files.discard_staged_audio(owned_staged_audio)
            raise

    async def persist_unfinalized_audio(
        self,
        audio: bytes | None = None,
        *,
        staged_audio: memoir_audio_files.StagedAudioFile | None = None,
    ) -> VoiceFinalPersistenceResult | None:
        if not audio and staged_audio is None:
            return None

        owned_staged_audio = staged_audio
        try:
            voice_session = await self._load_voice_session()
            owned_staged_audio = await self._stage_audio_input(audio, owned_staged_audio)
            if owned_staged_audio is None:
                return None
            utterance = MemoirVoiceUtterance(
                voice_session_id=voice_session.id,
                audio_file_id=owned_staged_audio.audio_file.id,
                transcript_raw="",
                transcript_edited="",
                provider=self.provider,
                provider_payload_json={"source": "unfinalized_audio"},
            )
            self.db.add(utterance)
            await self.db.flush()
            await self.db.commit()
            return VoiceFinalPersistenceResult(
                message_id=None,
                utterance_id=utterance.id,
                audio_file_id=owned_staged_audio.audio_file.id,
                transcript_version_id=None,
            )
        except Exception:
            await self.db.rollback()
            await memoir_audio_files.discard_staged_audio(owned_staged_audio)
            raise

    async def _load_voice_session(self) -> MemoirVoiceSession:
        result = await self.db.execute(
            select(MemoirVoiceSession)
            .join(Project, MemoirVoiceSession.project_id == Project.id)
            .where(MemoirVoiceSession.id == self.voice_session_id, Project.user_id == self.user_id)
        )
        voice_session = result.scalar_one_or_none()
        if not voice_session:
            raise HTTPException(status_code=404, detail="本次语音记录不存在")
        return voice_session

    async def _stage_audio(self, audio: bytes | None) -> memoir_audio_files.StagedAudioFile | None:
        if not audio:
            return None
        return await memoir_audio_files.stage_audio_bytes(
            self.db,
            self.user_id,
            audio,
            mime_type="audio/pcm",
            extension=".pcm",
        )

    async def _stage_audio_input(
        self,
        audio: bytes | None,
        staged_audio: memoir_audio_files.StagedAudioFile | None,
    ) -> memoir_audio_files.StagedAudioFile | None:
        if audio is not None and staged_audio is not None:
            raise RuntimeError("audio bytes and staged audio cannot both be provided")
        if staged_audio is not None:
            return staged_audio
        return await self._stage_audio(audio)

    async def _next_turn_index(self, voice_session_id: str) -> int:
        result = await self.db.execute(
            select(func.max(MemoirChatMessage.turn_index)).where(
                MemoirChatMessage.voice_session_id == voice_session_id
            )
        )
        current = result.scalar_one_or_none()
        return 0 if current is None else int(current) + 1


def _validate_audio_span(start_ms: int | None, end_ms: int | None) -> None:
    if start_ms is not None and end_ms is not None and end_ms < start_ms:
        raise HTTPException(status_code=400, detail="音频结束时间不能早于开始时间")
