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
from app.models.memoir import (
    MemoirEditingJob,
    MemoirSectionMaterial,
    MemoirTranscriptVersion,
    MemoirVoiceUtterance,
)
from app.services.memoir import completion_interviews, editing_jobs, sections
from app.services.memoir.editing_jobs import EditingJobResult
from app.services.memoir.project_ai_preferences import (
    load_project_ai_model,
    normalize_transcript_polish_level,
)
from app.services.memoir.utterance_editing_pipeline import (
    claim_transcript_mutation_guard,
    schedule_ready_utterance_background_work,
    transcript_mutation_guard,
)
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
    ai_model: str | None = None,
    transcript_polish_level: str | None = None,
) -> MemoirEditingJob:
    return await editing_jobs.enqueue_editing_job(
        db,
        project_id=project_id,
        job_type=MemoirEditingJobType.CORRECT_TRANSCRIPT,
        payload_json={
            "utterance_id": utterance_id,
            "voice_session_id": voice_session_id,
            "raw_transcript_version_id": raw_transcript_version_id,
            "ai_model": (ai_model or "").strip() or None,
            "transcript_polish_level": normalize_transcript_polish_level(
                transcript_polish_level
            ).value,
        },
        dedupe_key=f"correct_transcript:{utterance_id}",
    )


async def _refresh_layered_material(
    db: AsyncSession,
    *,
    project_id: str,
    utterance: MemoirVoiceUtterance,
    transcript_version_id: str | None,
) -> bool:
    layered_material = await db.scalar(
        select(MemoirSectionMaterial.id).where(
            MemoirSectionMaterial.project_id == project_id,
            MemoirSectionMaterial.utterance_id == utterance.id,
        )
    )
    if utterance.completion_issue_id:
        await completion_interviews.bind_answer_segment(
            db,
            voice_session=utterance.voice_session,
            utterance=utterance,
        )
        layered_material = layered_material or await db.scalar(
            select(MemoirSectionMaterial.id).where(
                MemoirSectionMaterial.project_id == project_id,
                MemoirSectionMaterial.utterance_id == utterance.id,
            )
        )
    if transcript_version_id:
        await sections.refresh_material_transcript_version(
            db,
            project_id=project_id,
            utterance_id=utterance.id,
            transcript_version_id=transcript_version_id,
        )
    return layered_material is not None


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
    ai_model = await load_project_ai_model(
        db,
        job.project_id,
        str(payload.get("ai_model") or "").strip() or None,
    )
    polish_level = normalize_transcript_polish_level(
        payload.get("transcript_polish_level")
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
        is_layered = await _refresh_layered_material(
            db,
            project_id=job.project_id,
            utterance=utterance,
            transcript_version_id=(
                message.transcript_version_id if message else None
            ),
        )
        if not is_layered:
            await schedule_ready_utterance_background_work(
                db,
                project_id=job.project_id,
                utterance=utterance,
                source="transcript_already_edited",
                ai_model=ai_model,
            )
        return EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            result_json={"utterance_id": utterance.id},
            error_message="transcript already edited",
        )
    if message and raw_transcript_version_id and message.transcript_version_id != raw_transcript_version_id:
        is_layered = await _refresh_layered_material(
            db,
            project_id=job.project_id,
            utterance=utterance,
            transcript_version_id=message.transcript_version_id,
        )
        if not is_layered:
            await schedule_ready_utterance_background_work(
                db,
                project_id=job.project_id,
                utterance=utterance,
                source="transcript_version_already_changed",
                ai_model=ai_model,
            )
        return EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            result_json={"utterance_id": utterance.id},
            error_message="transcript version changed",
        )

    expected = transcript_mutation_guard(utterance)
    cleanup_context = await load_voice_transcript_cleanup_context(
        db,
        utterance.voice_session,
        exclude_message_id=utterance.chat_message_id,
    )
    # The provider call may take seconds. End the read phase before crossing the
    # network; the mutation guard below re-locks and revalidates the source.
    await db.commit()
    cleaned = await generate_cleaned_transcript_text(
        source_text,
        ai_model=ai_model,
        transcript_polish_level=polish_level,
        context=cleanup_context,
        strict=False,
    )
    if not await claim_transcript_mutation_guard(db, utterance=utterance, expected=expected):
        return EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            result_json={"utterance_id": utterance.id},
            error_message="transcript version changed",
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
            "correction_requested_model": cleaned.requested_model,
            "correction_resolved_model": cleaned.resolved_model,
            "correction_response_model": cleaned.response_model,
            "correction_created_by": cleaned.created_by.value,
            "correction_polish_level": polish_level.value,
            "correction_polish_fallback": cleaned.provider is None,
            "corrected_transcript_version_id": corrected_version.id,
        }
        db.add(message)

    db.add(utterance)
    await prune_transcript_versions_for_utterance(
        db,
        utterance_id=utterance.id,
        version_type=MemoirTranscriptVersionType.DIALECT_CORRECTED,
    )
    is_layered = await _refresh_layered_material(
        db,
        project_id=job.project_id,
        utterance=utterance,
        transcript_version_id=corrected_version.id,
    )
    if not is_layered:
        await schedule_ready_utterance_background_work(
            db,
            project_id=job.project_id,
            utterance=utterance,
            source="transcript_correction_succeeded",
            ai_model=ai_model,
        )
    return EditingJobResult(
        result_json={
            "utterance_id": utterance.id,
            "transcript_version_id": corrected_version.id,
            "provider": cleaned.provider,
            "requested_model": cleaned.requested_model,
            "resolved_model": cleaned.resolved_model,
            "response_model": cleaned.response_model,
            "created_by": cleaned.created_by.value,
            "transcript_polish_level": polish_level.value,
            "polish_fallback": cleaned.provider is None,
        }
    )
