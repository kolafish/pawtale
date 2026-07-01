from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.database import async_session_maker
from app.domain.memoir.types import (
    MemoirAutomationEventStatus,
    MemoirAutomationEventType,
    MemoirEditingJobStatus,
    MemoirEditingJobType,
)
from app.models.memoir import MemoirEditingJob
from app.services.memoir import automation_events
from app.services.memoir.automation_event_metadata import (
    ACKNOWLEDGED_AT_KEY,
    ACKNOWLEDGED_BY_USER_KEY,
    EVENT_SIGNATURE_KEY,
    VOICE_PLAYED_AT_KEY,
    VOICE_PLAYED_MESSAGE_ID_KEY,
    VOICE_PLAYED_SESSION_ID_KEY,
    VOICE_SELECTED_AT_KEY,
    VOICE_SELECTED_MESSAGE_ID_KEY,
    VOICE_SELECTED_SESSION_ID_KEY,
)


logger = logging.getLogger(__name__)

EditingJobHandler = Callable[[AsyncSession, MemoirEditingJob], Awaitable["EditingJobResult"]]


@dataclass(frozen=True)
class EditingJobResult:
    status: MemoirEditingJobStatus = MemoirEditingJobStatus.SUCCEEDED
    result_json: dict = field(default_factory=dict)
    error_message: str = ""


_HANDLERS: dict[MemoirEditingJobType, EditingJobHandler] = {}
EDITING_JOB_FAILURE_EVENT_SOURCE = "editing_job_failure"

_FAILURE_EVENT_TYPES: dict[MemoirEditingJobType, MemoirAutomationEventType] = {
    MemoirEditingJobType.ROUTE_SOURCES: MemoirAutomationEventType.SOURCE_HELD,
    MemoirEditingJobType.REVISE_CHAPTER: MemoirAutomationEventType.CHAPTER_AUTO_REVISED,
    MemoirEditingJobType.QUALITY_AUDIT: MemoirAutomationEventType.AUDIT_COMPLETED,
    MemoirEditingJobType.GENERATE_CHAPTER_DRAFT: MemoirAutomationEventType.CHAPTER_DRAFT_GENERATED,
    MemoirEditingJobType.GENERATE_BOOK_OUTPUT: MemoirAutomationEventType.BOOK_OUTPUT_GENERATED,
}

_FAILURE_SUMMARIES: dict[MemoirEditingJobType, str] = {
    MemoirEditingJobType.ROUTE_SOURCES: "自动判断讲述归属失败，稍后会继续重试或可手动整理。",
    MemoirEditingJobType.REVISE_CHAPTER: "章节自动补写失败，稍后可重试。",
    MemoirEditingJobType.QUALITY_AUDIT: "章节整理检查失败，稍后可重试。",
    MemoirEditingJobType.GENERATE_CHAPTER_DRAFT: "章节初稿生成失败，稍后可重试。",
    MemoirEditingJobType.GENERATE_BOOK_OUTPUT: "全书稿生成失败，稍后可重试。",
}


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _compact_error_message(message: str) -> str:
    return " ".join(str(message or "").split())[:500]


def _payload_utterance_ids(payload: dict) -> list[str]:
    ids: list[str] = []
    for key in ("utterance_ids", "pending_source_utterance_ids", "removed_source_utterance_ids"):
        value = payload.get(key)
        if isinstance(value, list):
            ids.extend(item for item in value if isinstance(item, str))
    return list(dict.fromkeys(item.strip() for item in ids if item.strip()))


async def _record_job_failure_event(
    db: AsyncSession,
    job: MemoirEditingJob,
    *,
    error_message: str,
) -> None:
    try:
        job_type = MemoirEditingJobType(job.job_type)
    except ValueError:
        return
    event_type = _FAILURE_EVENT_TYPES.get(job_type)
    if event_type is None:
        return

    payload = job.payload_json if isinstance(job.payload_json, dict) else {}
    error = _compact_error_message(error_message)
    metadata: dict[str, object] = {
        "source": EDITING_JOB_FAILURE_EVENT_SOURCE,
        EVENT_SIGNATURE_KEY: f"editing_job_failed:{job.id}",
        "editing_job_id": job.id,
        "job_type": job_type.value,
        "dedupe_key": job.dedupe_key or "",
        "attempts": job.attempts,
        "error_message": error,
    }
    payload_source = payload.get("source")
    if isinstance(payload_source, str) and payload_source.strip():
        metadata["pipeline_source"] = payload_source.strip()

    event = await automation_events.record_or_update_automation_event(
        db,
        project_id=job.project_id,
        event_type=event_type,
        status=MemoirAutomationEventStatus.FAILED,
        chapter_id=job.chapter_id,
        utterance_ids=_payload_utterance_ids(payload),
        summary_text=_FAILURE_SUMMARIES[job_type],
        metadata_json=metadata,
    )
    updated_metadata = dict(event.metadata_json or {})
    for key in (
        ACKNOWLEDGED_AT_KEY,
        ACKNOWLEDGED_BY_USER_KEY,
        VOICE_SELECTED_AT_KEY,
        VOICE_SELECTED_SESSION_ID_KEY,
        VOICE_SELECTED_MESSAGE_ID_KEY,
        VOICE_PLAYED_AT_KEY,
        VOICE_PLAYED_SESSION_ID_KEY,
        VOICE_PLAYED_MESSAGE_ID_KEY,
    ):
        updated_metadata.pop(key, None)
    event.metadata_json = updated_metadata
    db.add(event)
    await db.flush()


def register_editing_job_handler(
    job_type: MemoirEditingJobType,
    handler: EditingJobHandler,
) -> None:
    _HANDLERS[job_type] = handler


def clear_editing_job_handlers() -> None:
    _HANDLERS.clear()


async def enqueue_editing_job(
    db: AsyncSession,
    *,
    project_id: str,
    job_type: MemoirEditingJobType,
    chapter_id: str | None = None,
    payload_json: dict | None = None,
    run_after: datetime | None = None,
    dedupe_key: str | None = None,
    replace_completed: bool = False,
) -> MemoirEditingJob:
    scheduled_at = run_after or _utcnow()
    payload = payload_json or {}
    existing = None
    if dedupe_key:
        existing = await db.scalar(
            select(MemoirEditingJob).where(MemoirEditingJob.dedupe_key == dedupe_key)
        )
    if existing:
        can_replace_completed = existing.status in {
            MemoirEditingJobStatus.SUCCEEDED.value,
            MemoirEditingJobStatus.FAILED.value,
            MemoirEditingJobStatus.SKIPPED.value,
        } and replace_completed
        if existing.status == MemoirEditingJobStatus.PENDING.value or can_replace_completed:
            existing.project_id = project_id
            existing.chapter_id = chapter_id
            existing.job_type = job_type.value
            existing.status = MemoirEditingJobStatus.PENDING.value
            existing.run_after = scheduled_at
            existing.locked_at = None
            existing.error_message = ""
            existing.payload_json = payload
            existing.result_json = {}
            if replace_completed:
                existing.attempts = 0
            db.add(existing)
            await db.flush()
        return existing

    job = MemoirEditingJob(
        project_id=project_id,
        chapter_id=chapter_id,
        job_type=job_type.value,
        status=MemoirEditingJobStatus.PENDING.value,
        run_after=scheduled_at,
        dedupe_key=dedupe_key,
        payload_json=payload,
    )
    db.add(job)
    await db.flush()
    return job


async def claim_due_editing_jobs(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int | None = None,
) -> list[str]:
    settings = get_settings()
    due_at = now or _utcnow()
    batch_size = limit or max(1, settings.memoir_editing_batch_size)
    jobs = (
        await db.execute(
            select(MemoirEditingJob)
            .where(
                MemoirEditingJob.status == MemoirEditingJobStatus.PENDING.value,
                MemoirEditingJob.run_after <= due_at,
            )
            .order_by(MemoirEditingJob.run_after, MemoirEditingJob.created_at)
            .limit(batch_size)
        )
    ).scalars().all()
    job_ids: list[str] = []
    for job in jobs:
        job.status = MemoirEditingJobStatus.RUNNING.value
        job.locked_at = due_at
        db.add(job)
        job_ids.append(job.id)
    await db.commit()
    return job_ids


async def _mark_job(
    db: AsyncSession,
    job: MemoirEditingJob,
    status: MemoirEditingJobStatus,
    *,
    result_json: dict | None = None,
    error_message: str = "",
) -> None:
    job.status = status.value
    job.locked_at = None
    job.result_json = result_json or {}
    job.error_message = error_message
    db.add(job)
    if status == MemoirEditingJobStatus.FAILED:
        await _record_job_failure_event(db, job, error_message=error_message)
    await db.commit()


async def run_editing_job(db: AsyncSession, job_id: str) -> bool:
    job = await db.get(MemoirEditingJob, job_id)
    if not job or job.status not in {
        MemoirEditingJobStatus.PENDING.value,
        MemoirEditingJobStatus.RUNNING.value,
    }:
        return False

    job.status = MemoirEditingJobStatus.RUNNING.value
    job.locked_at = _utcnow()
    job.attempts += 1
    db.add(job)
    await db.commit()

    try:
        job_type = MemoirEditingJobType(job.job_type)
        handler = _HANDLERS.get(job_type)
        if handler is None:
            await _mark_job(
                db,
                job,
                MemoirEditingJobStatus.SKIPPED,
                error_message=f"no handler registered for {job_type.value}",
            )
            return False
        result = await handler(db, job)
        await _mark_job(
            db,
            job,
            result.status,
            result_json=result.result_json,
            error_message=result.error_message,
        )
        return result.status == MemoirEditingJobStatus.SUCCEEDED
    except Exception as exc:
        await db.rollback()
        logger.exception("memoir editing job failed job_id=%s", job_id)
        job = await db.get(MemoirEditingJob, job_id)
        if job:
            await _mark_job(db, job, MemoirEditingJobStatus.FAILED, error_message=str(exc))
        return False


async def run_due_editing_jobs(
    session_maker: async_sessionmaker[AsyncSession] = async_session_maker,
    *,
    limit: int | None = None,
) -> int:
    async with session_maker() as db:
        job_ids = await claim_due_editing_jobs(db, limit=limit)
    completed = 0
    for job_id in job_ids:
        async with session_maker() as db:
            if await run_editing_job(db, job_id):
                completed += 1
    return completed


async def editing_worker_loop(stop_event: asyncio.Event) -> None:
    settings = get_settings()
    poll_seconds = max(1, settings.memoir_editing_poll_seconds)
    while not stop_event.is_set():
        try:
            await run_due_editing_jobs()
        except Exception:
            logger.exception("memoir editing worker tick failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_seconds)
        except asyncio.TimeoutError:
            pass
