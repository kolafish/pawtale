from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import MemoirEditingJobStatus, MemoirEditingJobType
from app.models.memoir import (
    MemoirEditingJob,
    MemoirTranscriptVersion,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.models.projects import Project
from app.services.memoir.editing_jobs import EditingJobResult, enqueue_editing_job
from app.services.memoir.narrative.ingestion import (
    NARRATIVE_EXTRACTION_SCHEMA_VERSION,
    NARRATIVE_INGESTION_ENGINE,
    ingest_transcript_version,
    transcript_text_hash,
)


async def _load_ingestion_source(
    db: AsyncSession,
    *,
    project_id: str,
    utterance_id: str,
    transcript_version_id: str,
) -> tuple[Project, MemoirVoiceUtterance, MemoirTranscriptVersion] | None:
    project = await db.get(Project, project_id)
    if project is None:
        return None
    utterance = await db.scalar(
        select(MemoirVoiceUtterance)
        .join(MemoirVoiceSession, MemoirVoiceSession.id == MemoirVoiceUtterance.voice_session_id)
        .where(
            MemoirVoiceUtterance.id == utterance_id,
            MemoirVoiceSession.project_id == project_id,
        )
        .options(
            selectinload(MemoirVoiceUtterance.chat_message),
            selectinload(MemoirVoiceUtterance.audio_file),
        )
    )
    if utterance is None:
        return None
    version = await db.scalar(
        select(MemoirTranscriptVersion).where(
            MemoirTranscriptVersion.id == transcript_version_id,
            MemoirTranscriptVersion.voice_session_id == utterance.voice_session_id,
            MemoirTranscriptVersion.source_artifact_id == utterance.id,
        )
    )
    if version is None:
        return None
    return project, utterance, version


async def schedule_narrative_ingestion_job(
    db: AsyncSession,
    *,
    project_id: str,
    utterance: MemoirVoiceUtterance,
    source: str,
    ai_model: str | None = None,
) -> MemoirEditingJob | None:
    transcript_version_id = (
        utterance.chat_message.transcript_version_id
        if utterance.chat_message
        else None
    )
    if not transcript_version_id:
        return None
    version = await db.scalar(
        select(MemoirTranscriptVersion).where(
            MemoirTranscriptVersion.id == transcript_version_id,
            MemoirTranscriptVersion.voice_session_id == utterance.voice_session_id,
            MemoirTranscriptVersion.source_artifact_id == utterance.id,
        )
    )
    if version is None or not version.content:
        return None
    current_text = (utterance.transcript_edited or "").strip() or (
        utterance.transcript_raw or ""
    ).strip()
    if (
        not utterance.chat_message
        or utterance.chat_message.content_text.strip() != version.content.strip()
        or current_text != version.content.strip()
    ):
        return None
    payload: dict[str, object] = {
        "utterance_id": utterance.id,
        "transcript_version_id": version.id,
        "source_text_hash": transcript_text_hash(version.content),
        "engine": NARRATIVE_INGESTION_ENGINE,
        "schema_version": NARRATIVE_EXTRACTION_SCHEMA_VERSION,
        "source": source,
    }
    if ai_model:
        payload["ai_model"] = ai_model
    return await enqueue_editing_job(
        db,
        project_id=project_id,
        job_type=MemoirEditingJobType.INGEST_NARRATIVE_STATE,
        payload_json=payload,
        dedupe_key=f"ingest_narrative:{utterance.id}:{version.id}",
    )


async def run_narrative_ingestion_job(
    db: AsyncSession,
    job: MemoirEditingJob,
) -> EditingJobResult:
    payload = job.payload_json if isinstance(job.payload_json, dict) else {}
    utterance_id = str(payload.get("utterance_id") or "").strip()
    transcript_version_id = str(payload.get("transcript_version_id") or "").strip()
    expected_hash = str(payload.get("source_text_hash") or "").strip()
    if not utterance_id or not transcript_version_id or not expected_hash:
        return EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            error_message="narrative ingestion source unavailable",
        )
    loaded = await _load_ingestion_source(
        db,
        project_id=job.project_id,
        utterance_id=utterance_id,
        transcript_version_id=transcript_version_id,
    )
    if loaded is None:
        return EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            error_message="narrative ingestion source unavailable",
        )
    project, utterance, version = loaded
    current_version_id = (
        utterance.chat_message.transcript_version_id
        if utterance.chat_message
        else None
    )
    current_text = (utterance.transcript_edited or "").strip() or (
        utterance.transcript_raw or ""
    ).strip()
    if (
        current_version_id != version.id
        or not utterance.chat_message
        or utterance.chat_message.content_text.strip() != version.content.strip()
        or current_text != version.content.strip()
        or transcript_text_hash(version.content) != expected_hash
    ):
        return EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            result_json={"utterance_id": utterance.id, "stale_transcript_version_id": version.id},
            error_message="narrative ingestion source changed",
        )
    result = await ingest_transcript_version(
        db,
        project=project,
        utterance=utterance,
        version=version,
        ai_model=payload.get("ai_model") if isinstance(payload.get("ai_model"), str) else None,
    )
    return EditingJobResult(
        result_json={
            "utterance_id": utterance.id,
            "transcript_version_id": version.id,
            "created_claim_ids": list(result.created_claim_ids),
            "reused_claim_ids": list(result.reused_claim_ids),
            "created_evidence_ids": list(result.created_evidence_ids),
            "skipped_claim_keys": list(result.skipped_claim_keys),
            "entity_review_candidates": {
                entity_id: list(candidate_ids)
                for entity_id, candidate_ids in result.entity_review_candidates.items()
            },
            "provider": result.provider,
            "model": result.model,
            "engine": NARRATIVE_INGESTION_ENGINE,
            "schema_version": NARRATIVE_EXTRACTION_SCHEMA_VERSION,
        }
    )
