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
from app.domain.memoir.types import MemoirEditingJobStatus, MemoirEditingJobType
from app.models.memoir import MemoirEditingJob


logger = logging.getLogger(__name__)

EditingJobHandler = Callable[[AsyncSession, MemoirEditingJob], Awaitable["EditingJobResult"]]


@dataclass(frozen=True)
class EditingJobResult:
    status: MemoirEditingJobStatus = MemoirEditingJobStatus.SUCCEEDED
    result_json: dict = field(default_factory=dict)
    error_message: str = ""


_HANDLERS: dict[MemoirEditingJobType, EditingJobHandler] = {}


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


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
