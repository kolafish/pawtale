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
    MemoirGuidanceMode,
    MemoirInterviewType,
    MemoirSpeakerRole,
    MemoirTranscriptionStatus,
    MemoirTranscriptVersionType,
)
from app.models.memoir import MemoirChatMessage, MemoirTranscriptVersion, MemoirVoiceSession, MemoirVoiceUtterance
from app.models.memoir_narrative import MemoirFrameworkItem
from app.models.projects import Project
from app.services.memoir import audio_files as memoir_audio_files
from app.services.memoir import completion_interviews, completion_issues
from app.services.memoir import narrator_profile, voice_transcript_correction
from app.services.memoir import share_video_candidate_scheduling
from app.services.memoir import voice_progress_notices
from app.services.memoir.project_ai_preferences import load_project_transcript_polish_level
from app.services.memoir.retrieval_source_index import schedule_source_indexing_job
from app.services.asr.streaming import StreamingASREvent
from app.services.memoir.narrative import interview_policy, interview_policy_outcomes, interview_topics
from app.services.memoir.voice_transcripts import (
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


async def _assign_interview_target(
    db: AsyncSession,
    *,
    voice_session: MemoirVoiceSession,
    utterance: MemoirVoiceUtterance,
) -> None:
    policy_decision = await interview_policy.policy_decision_for_utterance(db, utterance=utterance)
    completion_issue = None
    framework_item = None
    # The question's frozen target (including no target) owns this answer.
    # Session/history targets only apply when no active policy produced the question.
    if policy_decision is None:
        completion_issue = (
            await completion_issues.get_issue(
                db,
                project_id=voice_session.project_id,
                issue_id=voice_session.current_completion_issue_id,
            )
            if voice_session.current_completion_issue_id
            else None
        )
        anchor_item_id = await interview_topics.current_framework_item_id(db, voice_session)
        framework_item = (
            await db.get(MemoirFrameworkItem, anchor_item_id)
            if anchor_item_id
            else None
        )
    assigned = await interview_policy.assign_utterance(
        db,
        utterance=utterance,
        completion_issue=completion_issue,
        framework_item=framework_item,
        policy_decision=policy_decision,
    )
    if not (utterance.transcript_edited or utterance.transcript_raw).strip():
        return  # Capture the original target without claiming an answer or evaluating silence.
    if assigned.completion_issue_id is not None:
        await completion_interviews.bind_answer_segment(
            db,
            voice_session=voice_session,
            utterance=assigned,
        )
    if (
        assigned.framework_item_id
        and voice_session.guidance_mode == MemoirGuidanceMode.GUIDED_INTERVIEW.value
        and voice_session.anchor_framework_item_id != assigned.framework_item_id
    ):
        voice_session.anchor_framework_item_id = assigned.framework_item_id
        db.add(voice_session)
        await db.flush()
    await interview_policy_outcomes.schedule_policy_outcome_evaluation(
        db,
        project_id=voice_session.project_id,
        utterance=utterance,
    )


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

    async def persist_segment(
        self,
        event: StreamingASREvent,
        *,
        audio: bytes | None = None,
        staged_audio: memoir_audio_files.StagedAudioFile | None = None,
        speaker_role: str = MemoirSpeakerRole.NARRATOR.value,
        parent_message_id: str | None = None,
        ai_model: str | None = None,
        transcript_polish_level: str | None = None,
    ) -> VoiceFinalPersistenceResult:
        owned_staged_audio = staged_audio
        try:
            content = event.text.strip()
            if not content:
                raise HTTPException(status_code=400, detail="识别文字不能为空")
            _validate_audio_span(event.start_ms, event.end_ms)

            voice_session = await self._load_voice_session()
            normalized_speaker_role = _normalize_speaker_role(speaker_role)
            _validate_speaker_role_for_session(voice_session, normalized_speaker_role)
            chat_role = _chat_role_for_speaker(normalized_speaker_role)
            resolved_parent_message_id = await self._resolve_parent_message_id(
                voice_session,
                normalized_speaker_role,
                parent_message_id,
            )
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

            message = MemoirChatMessage(
                project_id=voice_session.project_id,
                voice_session_id=voice_session.id,
                role=chat_role,
                content_text=content,
                content_status=MemoirChatContentStatus.FINAL.value,
                audio_file_id=audio_file_id,
                transcript_version_id=transcript_version.id,
                parent_message_id=resolved_parent_message_id,
                turn_index=await self._next_turn_index(voice_session.id),
                metadata_json={
                    "source": "asr.final",
                    "provider": self.provider,
                    "speaker_role": normalized_speaker_role,
                    "interview_type": voice_session.interview_type,
                    "auto_corrected": False,
                    "correction_status": (
                        "skipped" if normalized_speaker_role == MemoirSpeakerRole.INTERVIEWER.value else "pending"
                    ),
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
                transcript_edited="",
                start_ms=event.start_ms,
                end_ms=event.end_ms,
                provider=self.provider,
                provider_payload_json=event.raw,
            )
            self.db.add(utterance)
            await self.db.flush()
            transcript_version.source_artifact_id = utterance.id
            await self.db.flush()
            if normalized_speaker_role == MemoirSpeakerRole.NARRATOR.value:
                await _assign_interview_target(
                    self.db,
                    voice_session=voice_session,
                    utterance=utterance,
                )
            await prune_transcript_versions_for_utterance(
                self.db,
                utterance_id=utterance.id,
                version_type=MemoirTranscriptVersionType.ASR_RAW,
            )
            if normalized_speaker_role == MemoirSpeakerRole.NARRATOR.value:
                polish_level = await load_project_transcript_polish_level(
                    self.db,
                    voice_session.project_id,
                    transcript_polish_level,
                )
                await voice_transcript_correction.schedule_transcript_correction_job(
                    self.db,
                    project_id=voice_session.project_id,
                    utterance_id=utterance.id,
                    voice_session_id=voice_session.id,
                    raw_transcript_version_id=transcript_version.id,
                    ai_model=ai_model,
                    transcript_polish_level=polish_level,
                )
                await schedule_source_indexing_job(
                    self.db,
                    project_id=voice_session.project_id,
                    utterance=utterance,
                    source="asr_final",
                )
                await narrator_profile.pin_profile_session(self.db, voice_session=voice_session)
                await narrator_profile.schedule_narrator_profile(self.db, utterance=utterance)
                await voice_progress_notices.enqueue_utterance_milestone_notice(
                    self.db,
                    project_id=voice_session.project_id,
                    utterance_id=utterance.id,
                )
                await share_video_candidate_scheduling.schedule_share_video_candidate_evaluation(
                    self.db,
                    project_id=voice_session.project_id,
                    voice_session_id=voice_session.id,
                )
            await self.db.commit()
            return VoiceFinalPersistenceResult(
                message_id=message.id,
                utterance_id=utterance.id,
                audio_file_id=audio_file_id,
                transcript_version_id=transcript_version.id,
            )
        except Exception:
            await self.db.rollback()
            await memoir_audio_files.discard_staged_audio(owned_staged_audio)
            raise

    async def persist_final(
        self,
        event: StreamingASREvent,
        *,
        audio: bytes | None = None,
        staged_audio: memoir_audio_files.StagedAudioFile | None = None,
        speaker_role: str = MemoirSpeakerRole.NARRATOR.value,
        parent_message_id: str | None = None,
        ai_model: str | None = None,
        transcript_polish_level: str | None = None,
    ) -> VoiceFinalPersistenceResult:
        return await self.persist_segment(
            event,
            audio=audio,
            staged_audio=staged_audio,
            speaker_role=speaker_role,
            parent_message_id=parent_message_id,
            ai_model=ai_model,
            transcript_polish_level=transcript_polish_level,
        )

    async def persist_unfinalized_audio(
        self,
        audio: bytes | None = None,
        *,
        staged_audio: memoir_audio_files.StagedAudioFile | None = None,
        speaker_role: str = MemoirSpeakerRole.NARRATOR.value,
        transcription_status: MemoirTranscriptionStatus = MemoirTranscriptionStatus.PENDING,
    ) -> VoiceFinalPersistenceResult | None:
        if not audio and staged_audio is None:
            return None

        owned_staged_audio = staged_audio
        try:
            voice_session = await self._load_voice_session()
            normalized_speaker_role = _normalize_speaker_role(speaker_role)
            _validate_speaker_role_for_session(
                voice_session,
                normalized_speaker_role,
            )
            owned_staged_audio = await self._stage_audio_input(audio, owned_staged_audio)
            if owned_staged_audio is None:
                return None
            utterance = MemoirVoiceUtterance(
                voice_session_id=voice_session.id,
                audio_file_id=owned_staged_audio.audio_file.id,
                transcript_raw="",
                transcript_edited="",
                provider=self.provider,
                provider_payload_json={
                    "source": "unfinalized_audio",
                    "speaker_role": normalized_speaker_role,
                    **({"transcription_status": transcription_status.value} if transcription_status != MemoirTranscriptionStatus.PENDING else {}),
                },
            )
            self.db.add(utterance)
            await self.db.flush()
            if normalized_speaker_role == MemoirSpeakerRole.NARRATOR.value:
                await _assign_interview_target(
                    self.db,
                    voice_session=voice_session,
                    utterance=utterance,
                )
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


    async def _resolve_parent_message_id(
        self,
        voice_session: MemoirVoiceSession,
        speaker_role: str,
        parent_message_id: str | None,
    ) -> str | None:
        normalized_parent_id = (parent_message_id or "").strip()
        if normalized_parent_id:
            exists = await self.db.scalar(
                select(MemoirChatMessage.id).where(
                    MemoirChatMessage.id == normalized_parent_id,
                    MemoirChatMessage.voice_session_id == voice_session.id,
                )
            )
            if not exists:
                raise HTTPException(status_code=400, detail="父级消息不属于本次访谈")
            return normalized_parent_id
        if (
            voice_session.interview_type != MemoirInterviewType.HUMAN_INTERVIEW.value
            or speaker_role != MemoirSpeakerRole.NARRATOR.value
        ):
            return None
        return await self.db.scalar(
            select(MemoirChatMessage.id)
            .where(
                MemoirChatMessage.voice_session_id == voice_session.id,
                MemoirChatMessage.role == MemoirChatMessageRole.INTERVIEWER.value,
            )
            .order_by(MemoirChatMessage.turn_index.desc(), MemoirChatMessage.created_at.desc())
            .limit(1)
        )

def _validate_audio_span(start_ms: int | None, end_ms: int | None) -> None:
    if start_ms is not None and end_ms is not None and end_ms < start_ms:
        raise HTTPException(status_code=400, detail="音频结束时间不能早于开始时间")


def _normalize_speaker_role(speaker_role: str) -> str:
    value = (speaker_role or MemoirSpeakerRole.NARRATOR.value).strip()
    if value not in {MemoirSpeakerRole.NARRATOR.value, MemoirSpeakerRole.INTERVIEWER.value}:
        raise HTTPException(status_code=400, detail="不支持的说话人角色")
    return value


def _validate_speaker_role_for_session(voice_session: MemoirVoiceSession, speaker_role: str) -> None:
    if (
        speaker_role == MemoirSpeakerRole.INTERVIEWER.value
        and voice_session.interview_type != MemoirInterviewType.HUMAN_INTERVIEW.value
    ):
        raise HTTPException(status_code=400, detail="只有人工访谈支持采访者录音")


def _chat_role_for_speaker(speaker_role: str) -> str:
    if speaker_role == MemoirSpeakerRole.INTERVIEWER.value:
        return MemoirChatMessageRole.INTERVIEWER.value
    return MemoirChatMessageRole.USER.value
