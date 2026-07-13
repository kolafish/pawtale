from __future__ import annotations

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.projects.types import ProjectType
from app.models.memoir import MemoirChatMessage, MemoirEditingJob, MemoirVoiceSession, MemoirVoiceUtterance
from app.models.memoir_narrative import MemoirClaimEvidence
from app.models.projects import Project
from app.services.memoir.narrative.ingestion_jobs import schedule_narrative_ingestion_job
from app.services.memoir.source_disposition import (
    INTERVIEWER_SOURCE_MATERIAL_TYPE,
    is_ignored_non_memoir_source,
)


async def run_narrative_ingestion_backfill(
    db: AsyncSession,
    *,
    project_id: str | None = None,
    dry_run: bool = True,
    limit: int | None = None,
    ai_model: str | None = None,
) -> dict[str, object]:
    statement = (
        select(MemoirVoiceUtterance, MemoirVoiceSession.project_id)
        .join(MemoirVoiceSession, MemoirVoiceUtterance.voice_session_id == MemoirVoiceSession.id)
        .join(MemoirChatMessage, MemoirChatMessage.id == MemoirVoiceUtterance.chat_message_id)
        .join(Project, Project.id == MemoirVoiceSession.project_id)
        .where(
            Project.type == ProjectType.MEMOIR.value,
            MemoirVoiceUtterance.chat_message_id.is_not(None),
            ~exists(
                select(MemoirClaimEvidence.id).where(
                    MemoirClaimEvidence.project_id == Project.id,
                    MemoirClaimEvidence.utterance_id == MemoirVoiceUtterance.id,
                    MemoirClaimEvidence.transcript_version_id
                    == MemoirChatMessage.transcript_version_id,
                )
            ),
        )
        .options(selectinload(MemoirVoiceUtterance.chat_message))
        .order_by(
            MemoirVoiceSession.created_at,
            MemoirVoiceUtterance.created_at,
            MemoirVoiceUtterance.id,
        )
    )
    if project_id:
        statement = statement.where(Project.id == project_id)
    rows = (await db.execute(statement)).all()
    dedupe_keys = {
        f"ingest_narrative:{utterance.id}:{utterance.chat_message.transcript_version_id}"
        for utterance, _current_project_id in rows
        if utterance.chat_message and utterance.chat_message.transcript_version_id
    }
    existing_job_keys: set[str | None] = set()
    if dedupe_keys:
        existing_job_keys = set(
            (
                await db.scalars(
                    select(MemoirEditingJob.dedupe_key).where(
                        MemoirEditingJob.dedupe_key.in_(dedupe_keys)
                    )
                )
            ).all()
        )
    candidates: list[tuple[str, MemoirVoiceUtterance]] = []
    skipped_existing = 0
    skipped_without_version = 0
    skipped_ignored = 0
    max_candidates = None if limit is None else max(0, limit)
    for utterance, current_project_id in rows:
        if max_candidates is not None and len(candidates) >= max_candidates:
            break
        if not utterance.chat_message or not utterance.chat_message.transcript_version_id:
            skipped_without_version += 1
            continue
        dedupe_key = (
            f"ingest_narrative:{utterance.id}:"
            f"{utterance.chat_message.transcript_version_id}"
        )
        if dedupe_key in existing_job_keys:
            skipped_existing += 1
            continue
        if (
            is_ignored_non_memoir_source(utterance)
            and utterance.source_material_type != INTERVIEWER_SOURCE_MATERIAL_TYPE
        ):
            skipped_ignored += 1
            continue
        candidates.append((current_project_id, utterance))

    job_ids: list[str] = []
    if not dry_run:
        for current_project_id, utterance in candidates:
            job = await schedule_narrative_ingestion_job(
                db,
                project_id=current_project_id,
                utterance=utterance,
                source="narrative_backfill",
                ai_model=ai_model,
            )
            if job is not None:
                job_ids.append(job.id)
        await db.commit()
    else:
        await db.rollback()

    return {
        "dry_run": dry_run,
        "project_id": project_id,
        "scanned_utterance_count": len(rows),
        "candidate_count": len(candidates),
        "skipped_without_version_count": skipped_without_version,
        "skipped_ignored_count": skipped_ignored,
        "skipped_existing_job_count": skipped_existing,
        "enqueued_count": len(job_ids),
        "editing_job_ids": job_ids,
    }
