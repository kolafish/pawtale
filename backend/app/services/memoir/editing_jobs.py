from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Awaitable, Callable

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.database import async_session_maker
from app.domain.memoir.types import (
    MemoirAutomationEventStatus,
    MemoirAutomationEventType,
    MemoirEditingJobStatus,
    MemoirEditingJobType,
)
from app.models.memoir import MemoirAutomationEvent, MemoirEditingJob
from app.services.memoir import automation_events
from app.services.memoir.automation_event_metadata import (
    ACKNOWLEDGED_AT_KEY,
    ACKNOWLEDGED_BY_USER_KEY,
    EDITING_JOB_FAILURE_SOURCE,
    RESOLUTION_KEY,
    RESOLUTION_REASON_KEY,
    RESOLVED_AT_KEY,
    VOICE_PLAYED_AT_KEY,
    VOICE_PLAYED_MESSAGE_ID_KEY,
    VOICE_PLAYED_SESSION_ID_KEY,
    VOICE_SELECTED_AT_KEY,
    VOICE_SELECTED_MESSAGE_ID_KEY,
    VOICE_SELECTED_SESSION_ID_KEY,
    _event_signature,
)
from app.services.memoir.job_leases import (
    JobLease,
    JobLeaseLostError,
    lease_deadline,
    new_job_lease,
    run_with_lease_heartbeat,
)


logger = logging.getLogger(__name__)

EditingJobHandler = Callable[[AsyncSession, MemoirEditingJob], Awaitable["EditingJobResult"]]


@dataclass(frozen=True)
class EditingJobResult:
    status: MemoirEditingJobStatus = MemoirEditingJobStatus.SUCCEEDED
    result_json: dict = field(default_factory=dict)
    error_message: str = ""


_HANDLERS: dict[MemoirEditingJobType, EditingJobHandler] = {}
EDITING_JOB_RECOVERY_REASON = "editing_job_succeeded"

_FAILURE_EVENT_TYPES: dict[MemoirEditingJobType, MemoirAutomationEventType] = {
    MemoirEditingJobType.INGEST_NARRATIVE_STATE: MemoirAutomationEventType.NARRATIVE_INGESTED,
    MemoirEditingJobType.RECOMPUTE_CHAPTER_STRUCTURE_AUDIT: MemoirAutomationEventType.AUDIT_COMPLETED,
    MemoirEditingJobType.GENERATE_FOLLOWUP_TASKS: MemoirAutomationEventType.FOLLOWUP_TASKS_GENERATED,
    MemoirEditingJobType.GENERATE_DRAFT_BLOCKS: MemoirAutomationEventType.CHAPTER_DRAFT_GENERATED,
    MemoirEditingJobType.AUDIT_BOOK_CONSISTENCY: MemoirAutomationEventType.AUDIT_COMPLETED,
    MemoirEditingJobType.ROUTE_SOURCES: MemoirAutomationEventType.SOURCE_HELD,
    MemoirEditingJobType.REVISE_CHAPTER: MemoirAutomationEventType.CHAPTER_AUTO_REVISED,
    MemoirEditingJobType.QUALITY_AUDIT: MemoirAutomationEventType.AUDIT_COMPLETED,
    MemoirEditingJobType.GENERATE_CHAPTER_DRAFT: MemoirAutomationEventType.CHAPTER_DRAFT_GENERATED,
    MemoirEditingJobType.GENERATE_BOOK_OUTPUT: MemoirAutomationEventType.BOOK_OUTPUT_GENERATED,
    MemoirEditingJobType.GENERATE_BOOK_EDITORIAL_COPY: MemoirAutomationEventType.BOOK_EDITORIAL_COPY_GENERATED,
}

_FAILURE_SUMMARIES: dict[MemoirEditingJobType, str] = {
    MemoirEditingJobType.INGEST_NARRATIVE_STATE: "素材事实整理失败，原始转写未受影响，可稍后重试。",
    MemoirEditingJobType.RECOMPUTE_CHAPTER_STRUCTURE_AUDIT: "章节完整性检查失败，已有事实和正文未受影响，可稍后重试。",
    MemoirEditingJobType.GENERATE_FOLLOWUP_TASKS: "补访任务生成失败，已有事实和录音未受影响，可稍后重试。",
    MemoirEditingJobType.GENERATE_DRAFT_BLOCKS: "正文块生成失败，已有正文和锁定内容未受影响，可稍后重试。",
    MemoirEditingJobType.AUDIT_BOOK_CONSISTENCY: "全书一致性检查失败，已有事实和正文未受影响，可稍后重试。",
    MemoirEditingJobType.ROUTE_SOURCES: "自动判断讲述归属失败，稍后会继续重试或可手动整理。",
    MemoirEditingJobType.REVISE_CHAPTER: "章节自动补写失败，稍后可重试。",
    MemoirEditingJobType.QUALITY_AUDIT: "章节整理检查失败，稍后可重试。",
    MemoirEditingJobType.GENERATE_CHAPTER_DRAFT: "章节初稿生成失败，稍后可重试。",
    MemoirEditingJobType.GENERATE_BOOK_OUTPUT: "全书稿生成失败，稍后可重试。",
    MemoirEditingJobType.GENERATE_BOOK_EDITORIAL_COPY: "成书文案生成失败，稍后可重试。",
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
        "source": EDITING_JOB_FAILURE_SOURCE,
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
        event_signature=f"editing_job_failed:{job.id}",
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


async def _resolve_job_failure_event(db: AsyncSession, job: MemoirEditingJob) -> None:
    try:
        job_type = MemoirEditingJobType(job.job_type)
    except ValueError:
        return
    event_type = _FAILURE_EVENT_TYPES.get(job_type)
    if event_type is None:
        return
    signature = f"editing_job_failed:{job.id}"
    events = (
        await db.scalars(
            select(MemoirAutomationEvent).where(
                MemoirAutomationEvent.project_id == job.project_id,
                MemoirAutomationEvent.event_type == event_type.value,
            )
        )
    ).all()
    for event in events:
        if _event_signature(event.metadata_json or {}) != signature:
            continue
        event.metadata_json = {
            **(event.metadata_json or {}),
            RESOLUTION_KEY: "confirmed",
            RESOLUTION_REASON_KEY: EDITING_JOB_RECOVERY_REASON,
            RESOLVED_AT_KEY: datetime.now(UTC).isoformat(),
        }
        db.add(event)
        await db.flush()
        return


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
    retry_failed: bool = False,
) -> MemoirEditingJob:
    scheduled_at = run_after or _utcnow()
    payload = payload_json or {}
    existing = None
    if dedupe_key:
        existing = await db.scalar(
            select(MemoirEditingJob).where(MemoirEditingJob.dedupe_key == dedupe_key)
        )
    if existing:
        replaceable_statuses: set[str] = set()
        if replace_completed:
            replaceable_statuses = {
                MemoirEditingJobStatus.SUCCEEDED.value,
                MemoirEditingJobStatus.FAILED.value,
                MemoirEditingJobStatus.SKIPPED.value,
            }
        elif retry_failed:
            replaceable_statuses = {
                MemoirEditingJobStatus.FAILED.value,
                MemoirEditingJobStatus.SKIPPED.value,
            }
        expected_statuses: set[str] = set()
        reset_attempts = False
        if existing.status == MemoirEditingJobStatus.PENDING.value:
            expected_statuses = {MemoirEditingJobStatus.PENDING.value}
        elif existing.status in replaceable_statuses:
            expected_statuses = replaceable_statuses
            reset_attempts = True
        if expected_statuses:
            values: dict[str, object] = {
                "project_id": project_id,
                "chapter_id": chapter_id,
                "job_type": job_type.value,
                "status": MemoirEditingJobStatus.PENDING.value,
                "run_after": scheduled_at,
                "locked_at": None,
                "lease_token": None,
                "lease_expires_at": None,
                "error_message": "",
                "payload_json": payload,
                "result_json": {},
            }
            if reset_attempts:
                values["attempts"] = 0
            await db.execute(
                update(MemoirEditingJob)
                .where(
                    MemoirEditingJob.id == existing.id,
                    MemoirEditingJob.status.in_(expected_statuses),
                )
                .values(**values)
                .execution_options(synchronize_session=False)
            )
            await db.refresh(existing)
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
    try:
        async with db.begin_nested():
            db.add(job)
            await db.flush()
        return job
    except IntegrityError:
        if not dedupe_key:
            raise
        concurrent = await db.scalar(
            select(MemoirEditingJob).where(MemoirEditingJob.dedupe_key == dedupe_key)
        )
        if concurrent is None:
            raise
        return concurrent


def _claimable_editing_job(due_at: datetime):
    return or_(
        and_(
            MemoirEditingJob.status == MemoirEditingJobStatus.PENDING.value,
            MemoirEditingJob.run_after <= due_at,
        ),
        and_(
            MemoirEditingJob.status == MemoirEditingJobStatus.RUNNING.value,
            or_(
                MemoirEditingJob.lease_expires_at.is_(None),
                MemoirEditingJob.lease_expires_at <= due_at,
            ),
        ),
    )


async def _claim_editing_job_ids(
    db: AsyncSession,
    job_ids: list[str],
    *,
    claimed_at: datetime,
    lease_seconds: int,
) -> list[JobLease]:
    claimable = _claimable_editing_job(claimed_at)
    leases: list[JobLease] = []
    for job_id in job_ids:
        lease = new_job_lease(job_id)
        result = await db.execute(
            update(MemoirEditingJob)
            .where(
                MemoirEditingJob.id == job_id,
                claimable,
            )
            .values(
                status=MemoirEditingJobStatus.RUNNING.value,
                locked_at=claimed_at,
                lease_token=lease.token,
                lease_expires_at=lease_deadline(claimed_at, lease_seconds),
                attempts=MemoirEditingJob.attempts + 1,
                error_message="",
            )
        )
        if result.rowcount == 1:
            leases.append(lease)
    await db.commit()
    return leases


async def claim_due_editing_jobs(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int | None = None,
    lease_seconds: int | None = None,
    job_type: MemoirEditingJobType | None = None,
    project_id: str | None = None,
) -> list[JobLease]:
    settings = get_settings()
    due_at = now or _utcnow()
    batch_size = limit or max(1, settings.memoir_editing_batch_size)
    lease_duration = lease_seconds or max(1, settings.memoir_editing_lease_seconds)
    statement = select(MemoirEditingJob.id).where(_claimable_editing_job(due_at))
    if job_type is not None:
        statement = statement.where(MemoirEditingJob.job_type == job_type.value)
    if project_id is not None:
        statement = statement.where(MemoirEditingJob.project_id == project_id)
    candidate_ids = (
        await db.scalars(
            statement.order_by(
                MemoirEditingJob.run_after,
                MemoirEditingJob.created_at,
            ).limit(batch_size)
        )
    ).all()
    await db.commit()
    return await _claim_editing_job_ids(
        db,
        candidate_ids,
        claimed_at=due_at,
        lease_seconds=lease_duration,
    )


async def claim_editing_job(
    db: AsyncSession,
    job_id: str,
    *,
    now: datetime | None = None,
    lease_seconds: int | None = None,
) -> JobLease | None:
    settings = get_settings()
    claimed_at = now or _utcnow()
    lease_duration = lease_seconds or max(1, settings.memoir_editing_lease_seconds)
    await db.commit()
    leases = await _claim_editing_job_ids(
        db,
        [job_id],
        claimed_at=claimed_at,
        lease_seconds=lease_duration,
    )
    return leases[0] if leases else None


async def renew_editing_job_lease(
    db: AsyncSession,
    lease: JobLease,
    *,
    now: datetime | None = None,
    lease_seconds: int | None = None,
) -> bool:
    settings = get_settings()
    renewed_at = now or _utcnow()
    lease_duration = lease_seconds or max(1, settings.memoir_editing_lease_seconds)
    result = await db.execute(
        update(MemoirEditingJob)
        .where(
            MemoirEditingJob.id == lease.job_id,
            MemoirEditingJob.status == MemoirEditingJobStatus.RUNNING.value,
            MemoirEditingJob.lease_token == lease.token,
        )
        .values(lease_expires_at=lease_deadline(renewed_at, lease_duration))
    )
    await db.commit()
    return result.rowcount == 1


async def _load_leased_editing_job(db: AsyncSession, lease: JobLease) -> MemoirEditingJob | None:
    return await db.scalar(
        select(MemoirEditingJob).where(
            MemoirEditingJob.id == lease.job_id,
            MemoirEditingJob.status == MemoirEditingJobStatus.RUNNING.value,
            MemoirEditingJob.lease_token == lease.token,
        )
    )


async def _mark_job(
    db: AsyncSession,
    job: MemoirEditingJob,
    lease: JobLease,
    status: MemoirEditingJobStatus,
    *,
    result_json: dict | None = None,
    error_message: str = "",
) -> bool:
    result = await db.execute(
        update(MemoirEditingJob)
        .where(
            MemoirEditingJob.id == lease.job_id,
            MemoirEditingJob.status == MemoirEditingJobStatus.RUNNING.value,
            MemoirEditingJob.lease_token == lease.token,
        )
        .values(
            status=status.value,
            locked_at=None,
            lease_token=None,
            lease_expires_at=None,
            result_json=result_json or {},
            error_message=error_message,
        )
    )
    if result.rowcount != 1:
        await db.rollback()
        return False
    if status == MemoirEditingJobStatus.FAILED:
        await _record_job_failure_event(db, job, error_message=error_message)
    elif status == MemoirEditingJobStatus.SUCCEEDED:
        await _resolve_job_failure_event(db, job)
    await db.commit()
    return True


async def run_editing_job(db: AsyncSession, lease: JobLease) -> bool:
    job = await _load_leased_editing_job(db, lease)
    if job is None:
        return False

    try:
        job_type = MemoirEditingJobType(job.job_type)
        handler = _HANDLERS.get(job_type)
        if handler is None:
            await _mark_job(
                db,
                job,
                lease,
                MemoirEditingJobStatus.SKIPPED,
                error_message=f"no handler registered for {job_type.value}",
            )
            return False
        handler_result = await handler(db, job)
        marked = await _mark_job(
            db,
            job,
            lease,
            handler_result.status,
            result_json=handler_result.result_json,
            error_message=handler_result.error_message,
        )
        return marked and handler_result.status == MemoirEditingJobStatus.SUCCEEDED
    except Exception as exc:
        await db.rollback()
        logger.exception("memoir editing job failed job_id=%s", lease.job_id)
        job = await _load_leased_editing_job(db, lease)
        if job:
            await _mark_job(
                db,
                job,
                lease,
                MemoirEditingJobStatus.FAILED,
                error_message=str(exc),
            )
        return False


async def _run_claimed_editing_job(
    session_maker: async_sessionmaker[AsyncSession],
    lease: JobLease,
) -> bool:
    settings = get_settings()
    lease_seconds = max(1, settings.memoir_editing_lease_seconds)
    heartbeat_seconds = min(
        max(1, settings.memoir_editing_heartbeat_seconds),
        max(1, lease_seconds // 3),
    )
    async def renew() -> bool:
        try:
            async with session_maker() as heartbeat_db:
                return await renew_editing_job_lease(
                    heartbeat_db,
                    lease,
                    lease_seconds=lease_seconds,
                )
        except Exception:
            logger.exception("memoir editing job lease heartbeat failed job_id=%s", lease.job_id)
            return False

    async def work() -> bool:
        async with session_maker() as db:
            return await run_editing_job(db, lease)

    try:
        return await run_with_lease_heartbeat(
            work,
            renew,
            interval_seconds=heartbeat_seconds,
        )
    except JobLeaseLostError:
        logger.warning("memoir editing job lease lost job_id=%s", lease.job_id)
        return False
    except Exception:
        logger.exception("memoir editing job worker failed job_id=%s", lease.job_id)
        return False


async def run_due_editing_jobs(
    session_maker: async_sessionmaker[AsyncSession] = async_session_maker,
    *,
    limit: int | None = None,
    job_type: MemoirEditingJobType | None = None,
    project_id: str | None = None,
) -> int:
    _, succeeded = await _run_due_editing_job_batch(
        session_maker,
        limit=limit,
        job_type=job_type,
        project_id=project_id,
    )
    return succeeded


async def _run_due_editing_job_batch(
    session_maker: async_sessionmaker[AsyncSession],
    *,
    limit: int | None = None,
    job_type: MemoirEditingJobType | None = None,
    project_id: str | None = None,
) -> tuple[int, int]:
    async with session_maker() as db:
        serialize_writes = db.get_bind().dialect.name == "sqlite"
        leases = await claim_due_editing_jobs(
            db,
            limit=1 if serialize_writes else limit,
            job_type=job_type,
            project_id=project_id,
        )
    if serialize_writes:
        results = [
            await _run_claimed_editing_job(session_maker, lease)
            for lease in leases
        ]
    else:
        results = await asyncio.gather(
            *(_run_claimed_editing_job(session_maker, lease) for lease in leases)
        )
    return len(leases), sum(results)


async def run_available_editing_jobs(
    session_maker: async_sessionmaker[AsyncSession] = async_session_maker,
    *,
    should_stop: Callable[[], bool] | None = None,
) -> int:
    succeeded = 0
    while True:
        claimed, batch_succeeded = await _run_due_editing_job_batch(session_maker)
        succeeded += batch_succeeded
        if claimed == 0 or (should_stop is not None and should_stop()):
            return succeeded


async def editing_worker_loop(stop_event: asyncio.Event) -> None:
    settings = get_settings()
    poll_seconds = max(1, settings.memoir_editing_poll_seconds)
    while not stop_event.is_set():
        try:
            await run_available_editing_jobs(should_stop=stop_event.is_set)
        except Exception:
            logger.exception("memoir editing worker tick failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_seconds)
        except asyncio.TimeoutError:
            pass
