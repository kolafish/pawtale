from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import (
    MemoirChatContentStatus,
    MemoirEditingJobStatus,
    MemoirEditingJobType,
    MemoirTranscriptVersionType,
)
from app.models.memoir import MemoirEditingJob, MemoirTranscriptVersion, MemoirVoiceUtterance
from app.services.memoir import editing_jobs
from app.services.memoir.editing_jobs import EditingJobResult
from app.services.memoir.utterance_editing_pipeline import schedule_source_processing_for_ready_utterance
from app.services.memoir.voice_transcripts import (
    generate_cleaned_transcript_text,
    load_voice_transcript_cleanup_context,
    prune_transcript_versions_for_utterance,
)


async def schedule_transcript_correction_job(
    db: AsyncSession,
    *,
    project_id: str,
    utterance_id: str,
    voice_session_id: str,
    raw_transcript_version_id: str,
) -> MemoirEditingJob:
    return await editing_jobs.enqueue_editing_job(
        db,
        project_id=project_id,
        job_type=MemoirEditingJobType.CORRECT_TRANSCRIPT,
        payload_json={
            "utterance_id": utterance_id,
            "voice_session_id": voice_session_id,
            "raw_transcript_version_id": raw_transcript_version_id,
        },
        dedupe_key=f"correct_transcript:{utterance_id}",
    )


async def run_transcript_correction_job(
    db: AsyncSession,
    job: MemoirEditingJob,
) -> EditingJobResult:
    payload = job.payload_json or {}
    utterance_id = str(payload.get("utterance_id") or "").strip()
    if not utterance_id:
        return EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            error_message="utterance unavailable",
        )

    utterance = await db.scalar(
        select(MemoirVoiceUtterance)
        .where(MemoirVoiceUtterance.id == utterance_id)
        .options(
            selectinload(MemoirVoiceUtterance.voice_session),
            selectinload(MemoirVoiceUtterance.chat_message),
        )
    )
    if not utterance or utterance.voice_session.project_id != job.project_id:
        return EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            error_message="utterance unavailable",
        )

    source_text = (utterance.transcript_raw or "").strip()
    if not source_text:
        return EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            error_message="transcript empty",
        )

    message = utterance.chat_message
    raw_transcript_version_id = str(payload.get("raw_transcript_version_id") or "").strip()
    if (utterance.transcript_edited or "").strip():
        processing_jobs = await schedule_source_processing_for_ready_utterance(
            db,
            project_id=job.project_id,
            utterance=utterance,
            source="transcript_already_edited",
        )
        return EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            result_json={
                "utterance_id": utterance.id,
                "route_source_job_id": processing_jobs.route_job.id if processing_jobs.route_job else None,
                "source_card_job_id": (
                    processing_jobs.source_card_job.id if processing_jobs.source_card_job else None
                ),
                "narrative_ingestion_job_id": (
                    processing_jobs.narrative_job.id if processing_jobs.narrative_job else None
                ),
            },
            error_message="transcript already edited",
        )
    if message and raw_transcript_version_id and message.transcript_version_id != raw_transcript_version_id:
        processing_jobs = await schedule_source_processing_for_ready_utterance(
            db,
            project_id=job.project_id,
            utterance=utterance,
            source="transcript_version_already_changed",
        )
        return EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            result_json={
                "utterance_id": utterance.id,
                "route_source_job_id": processing_jobs.route_job.id if processing_jobs.route_job else None,
                "source_card_job_id": (
                    processing_jobs.source_card_job.id if processing_jobs.source_card_job else None
                ),
                "narrative_ingestion_job_id": (
                    processing_jobs.narrative_job.id if processing_jobs.narrative_job else None
                ),
            },
            error_message="transcript version changed",
        )

    cleaned = await generate_cleaned_transcript_text(
        source_text,
        context=await load_voice_transcript_cleanup_context(
            db,
            utterance.voice_session,
            exclude_message_id=utterance.chat_message_id,
        ),
        strict=False,
    )
    corrected_version = MemoirTranscriptVersion(
        voice_session_id=utterance.voice_session_id,
        source_artifact_id=utterance.id,
        version_type=MemoirTranscriptVersionType.DIALECT_CORRECTED.value,
        content=cleaned.content,
        provider=cleaned.provider,
        created_by=cleaned.created_by.value,
    )
    db.add(corrected_version)
    await db.flush()

    utterance.transcript_edited = cleaned.content
    if message:
        message.content_text = cleaned.content
        message.content_status = MemoirChatContentStatus.CORRECTED.value
        message.transcript_version_id = corrected_version.id
        message.metadata_json = {
            **(message.metadata_json or {}),
            "auto_corrected": True,
            "correction_status": "succeeded",
            "correction_provider": cleaned.provider,
            "correction_created_by": cleaned.created_by.value,
            "corrected_transcript_version_id": corrected_version.id,
        }
        db.add(message)

    db.add(utterance)
    await prune_transcript_versions_for_utterance(
        db,
        utterance_id=utterance.id,
        version_type=MemoirTranscriptVersionType.DIALECT_CORRECTED,
    )
    processing_jobs = await schedule_source_processing_for_ready_utterance(
        db,
        project_id=job.project_id,
        utterance=utterance,
        source="transcript_correction_succeeded",
    )
    return EditingJobResult(
        result_json={
            "utterance_id": utterance.id,
            "transcript_version_id": corrected_version.id,
            "provider": cleaned.provider,
            "created_by": cleaned.created_by.value,
            "route_source_job_id": processing_jobs.route_job.id if processing_jobs.route_job else None,
            "source_card_job_id": processing_jobs.source_card_job.id if processing_jobs.source_card_job else None,
            "narrative_ingestion_job_id": (
                processing_jobs.narrative_job.id if processing_jobs.narrative_job else None
            ),
        }
    )
