from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Awaitable, Callable

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application_errors import InsufficientAICreditError
from app.clock import utcnow_naive
from app.config import get_settings
from app.services.ai_timeouts import ai_error_metadata, ai_user_error_message
from app.database import async_session_maker
from app.domain.memoir.types import (
    MemoirAutomationEventStatus,
    MemoirEditingJobStatus,
    MemoirEditingJobType,
)
from app.models.memoir import MemoirAutomationEvent, MemoirEditingJob
from app.models.projects import Project
from app.models.ai_credits import AICreditAccount
from app.services.ai_credits import credit_account_summary
from app.services.ai_usage_records import reset_ai_usage_context, set_ai_usage_context
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


class EditingJobDeadlineExceeded(RuntimeError):
    pass


class EditingJobProgressLost(RuntimeError):
    pass


ACTIVE_EDITING_JOB_STATUSES = frozenset(
    {
        MemoirEditingJobStatus.PENDING.value,
        MemoirEditingJobStatus.RUNNING.value,
    }
)
_VISIBLE_EDITING_JOB_STATUSES = {
    MemoirEditingJobStatus.PENDING.value,
    MemoirEditingJobStatus.RUNNING.value,
    MemoirEditingJobStatus.SUCCEEDED.value,
    MemoirEditingJobStatus.FAILED.value,
}


def register_editing_job_handler(
    job_type: MemoirEditingJobType,
    handler: EditingJobHandler,
) -> None:
    _HANDLERS[job_type] = handler


def clear_editing_job_handlers() -> None:
    _HANDLERS.clear()


async def load_project_editing_job(
    db: AsyncSession,
    *,
    project_id: str,
    job_type: MemoirEditingJobType,
    dedupe_key: str,
    job_id: str | None = None,
    chapter_id: str | None = None,
) -> MemoirEditingJob | None:
    statement = select(MemoirEditingJob).where(
        MemoirEditingJob.project_id == project_id,
        MemoirEditingJob.job_type == job_type.value,
    )
    if chapter_id is not None:
        statement = statement.where(MemoirEditingJob.chapter_id == chapter_id)
    if job_id:
        statement = statement.where(MemoirEditingJob.id == job_id)
    else:
        statement = statement.where(MemoirEditingJob.dedupe_key == dedupe_key)
    return await db.scalar(statement)


def project_editing_job_state(
    *,
    project_id: str,
    job: MemoirEditingJob | None,
) -> dict[str, object]:
    if job is None:
        return {
            "project_id": project_id,
            "status": "idle",
        }
    result = dict(job.result_json or {})
    status = (
        "cancelled"
        if result.get("cancelled") is True
        else (
            job.status
            if job.status in _VISIBLE_EDITING_JOB_STATUSES
            else MemoirEditingJobStatus.FAILED.value
        )
    )
    return {
        **result,
        "job_id": job.id,
        "project_id": project_id,
        "status": status,
        "can_cancel": status in ACTIVE_EDITING_JOB_STATUSES,
        "can_retry": status
        in {
            MemoirEditingJobStatus.FAILED.value,
            "cancelled",
        },
        "error_message": (
            job.error_message
            if status == MemoirEditingJobStatus.FAILED.value
            else ""
        ),
    }


async def cancel_project_editing_job(
    db: AsyncSession,
    *,
    job: MemoirEditingJob,
    result_patch: dict[str, object] | None = None,
) -> MemoirEditingJob:
    if job.status not in ACTIVE_EDITING_JOB_STATUSES:
        return job
    result_json = {
        **dict(job.result_json or {}),
        **(result_patch or {}),
        "stage": "cancelled",
        "cancelled": True,
    }
    await db.execute(
        update(MemoirEditingJob)
        .where(
            MemoirEditingJob.id == job.id,
            MemoirEditingJob.status.in_(ACTIVE_EDITING_JOB_STATUSES),
        )
        .values(
            status=MemoirEditingJobStatus.SKIPPED.value,
            locked_at=None,
            lease_token=None,
            lease_expires_at=None,
            result_json=result_json,
        )
    )
    await db.commit()
    await db.refresh(job)
    return job


def editing_job_progress_callback(
    db: AsyncSession,
    *,
    job: MemoirEditingJob,
    progress: dict[str, object],
) -> Callable[[dict[str, object]], Awaitable[None]]:
    job_id = job.id
    lease_token = job.lease_token

    async def update_progress(patch: dict[str, object]) -> None:
        progress.update(patch)
        if not await update_editing_job_progress_for_lease(
            db,
            job_id=job_id,
            lease_token=lease_token,
            result_json=progress,
        ):
            raise EditingJobProgressLost

    return update_progress


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
    preserve_progress: bool = False,
    reschedule_running: bool = False,
) -> MemoirEditingJob:
    scheduled_at = run_after or utcnow_naive()
    payload = payload_json or {}
    existing = None
    if dedupe_key:
        existing = await db.scalar(
            select(MemoirEditingJob).where(MemoirEditingJob.dedupe_key == dedupe_key)
        )
    if existing:
        if existing.status == MemoirEditingJobStatus.RUNNING.value and reschedule_running:
            updated = await db.execute(update(MemoirEditingJob).where(
                MemoirEditingJob.id == existing.id,
                MemoirEditingJob.status == MemoirEditingJobStatus.RUNNING.value,
            ).values(rerun_requested=True, payload_json=payload, run_after=scheduled_at))
            await db.refresh(existing)
            if updated.rowcount:
                return existing
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
                "result_json": dict(existing.result_json or {}) if preserve_progress else {},
            }
            if reset_attempts:
                values.update(attempts=0, first_started_at=None, rerun_requested=False)
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
                first_started_at=func.coalesce(MemoirEditingJob.first_started_at, claimed_at),
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
    due_at = now or utcnow_naive()
    batch_size = limit or max(1, settings.memoir_editing_batch_size)
    lease_duration = lease_seconds or max(1, settings.memoir_editing_lease_seconds)
    statement = select(MemoirEditingJob.id).where(_claimable_editing_job(due_at))
    if job_type is not None:
        statement = statement.where(MemoirEditingJob.job_type == job_type.value)
    if project_id is not None:
        statement = statement.where(MemoirEditingJob.project_id == project_id)
    statement = statement.order_by(
        case(
            (MemoirEditingJob.job_type == MemoirEditingJobType.GENERATE_BOOK_AUDIO_CHUNK.value, -1),
            (
                MemoirEditingJob.job_type.in_(
                    (
                        MemoirEditingJobType.EVALUATE_INTERVIEW_POLICY_SHADOW.value,
                        MemoirEditingJobType.EVALUATE_INTERVIEW_POLICY_OUTCOME.value,
                        MemoirEditingJobType.INDEX_RETRIEVAL_SOURCE.value,
                        MemoirEditingJobType.INDEX_RETRIEVAL_SEMANTICS.value,
                        MemoirEditingJobType.UPDATE_TOPIC_MAP.value,
                    )
                ),
                1,
            ),
            else_=0,
        ),
        MemoirEditingJob.run_after,
        MemoirEditingJob.created_at,
    ).limit(batch_size)
    statement = statement.with_for_update(skip_locked=True)
    candidate_ids = (
        await db.scalars(statement)
    ).all()
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
    claimed_at = now or utcnow_naive()
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
    renewed_at = now or utcnow_naive()
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


async def update_editing_job_progress(
    db: AsyncSession,
    job: MemoirEditingJob,
    result_json: dict,
) -> bool:
    return await update_editing_job_progress_for_lease(
        db,
        job_id=job.id,
        lease_token=job.lease_token,
        result_json=result_json,
    )


async def update_editing_job_progress_for_lease(
    db: AsyncSession,
    *,
    job_id: str,
    lease_token: str | None,
    result_json: dict,
) -> bool:
    result = await db.execute(
        update(MemoirEditingJob)
        .where(
            MemoirEditingJob.id == job_id,
            MemoirEditingJob.status == MemoirEditingJobStatus.RUNNING.value,
            MemoirEditingJob.lease_token == lease_token,
        )
        .values(result_json=result_json)
    )
    await db.commit()
    return result.rowcount == 1


async def _load_leased_editing_job(db: AsyncSession, lease: JobLease) -> MemoirEditingJob | None:
    return await db.scalar(
        select(MemoirEditingJob).where(
            MemoirEditingJob.id == lease.job_id,
            MemoirEditingJob.status == MemoirEditingJobStatus.RUNNING.value,
            MemoirEditingJob.lease_token == lease.token,
        ).execution_options(populate_existing=True)
    )


async def _mark_job(
    db: AsyncSession,
    job: MemoirEditingJob,
    lease: JobLease,
    status: MemoirEditingJobStatus,
    *,
    result_json: dict | None = None,
    error_message: str = "",
) -> str | None:
    rerun = and_(MemoirEditingJob.rerun_requested.is_(True), status == MemoirEditingJobStatus.SUCCEEDED)
    result = await db.execute(
        update(MemoirEditingJob)
        .where(
            MemoirEditingJob.id == lease.job_id,
            MemoirEditingJob.status == MemoirEditingJobStatus.RUNNING.value,
            MemoirEditingJob.lease_token == lease.token,
        )
        .values(
            status=case((rerun, MemoirEditingJobStatus.PENDING.value), else_=status.value),
            first_started_at=case((rerun, None), else_=MemoirEditingJob.first_started_at),
            rerun_requested=False,
            locked_at=None,
            lease_token=None,
            lease_expires_at=None,
            result_json=result_json or {},
            error_message=error_message,
        ).returning(MemoirEditingJob).execution_options(populate_existing=True, synchronize_session="fetch")
    )
    updated_job = result.scalar_one_or_none()
    if updated_job is None:
        await db.rollback()
        return None
    final_status = updated_job.status
    await db.commit()
    return final_status


def _deadline_error_message(timeout_seconds: float) -> str:
    minutes = max(1, round(timeout_seconds / 60))
    return f"后台任务运行超过 {minutes} 分钟，已停止。已保存的进度会保留，请重新开始继续。"


async def _mark_linked_events_timed_out(
    db: AsyncSession,
    job: MemoirEditingJob,
    *,
    error_message: str,
    timeout_seconds: float,
) -> None:
    events = (
        await db.scalars(
            select(MemoirAutomationEvent).where(
                MemoirAutomationEvent.editing_job_id == job.id,
                MemoirAutomationEvent.status == MemoirAutomationEventStatus.PENDING.value,
            )
        )
    ).all()
    label = str((job.payload_json or {}).get("target_label") or "").strip() or "后台任务"
    for event in events:
        event.status = MemoirAutomationEventStatus.FAILED.value
        event.summary_text = f"{label}处理超时，请重试。"
        event.metadata_json = {
            **(event.metadata_json or {}),
            "error_message": error_message,
            "error_type": "job_deadline_exceeded",
            "timeout_seconds": timeout_seconds,
        }
        db.add(event)


async def run_editing_job(
    db: AsyncSession,
    lease: JobLease,
    *,
    timeout_seconds: float | None = None,
) -> bool:
    job = await _load_leased_editing_job(db, lease)
    if job is None:
        return False

    deadline_seconds = max(
        0.01,
        timeout_seconds
        if timeout_seconds is not None
        else get_settings().memoir_editing_job_deadline_seconds,
    )
    elapsed_before_run = (
        max(0.0, (utcnow_naive() - job.first_started_at).total_seconds())
        if job.first_started_at is not None and timeout_seconds is None else 0.0
    )
    try:
        if elapsed_before_run >= deadline_seconds:
            raise EditingJobDeadlineExceeded
        job_type = MemoirEditingJobType(job.job_type)
        handler = _HANDLERS.get(job_type)
        if handler is None:
            await _mark_job(
                db,
                job,
                lease,
                MemoirEditingJobStatus.SKIPPED,
                error_message="后台任务暂不支持此操作，请联系管理员",
            )
            return False
        project_user_id = await db.scalar(
            select(Project.user_id).where(Project.id == job.project_id)
        )
        usage_token = set_ai_usage_context(
            user_id=project_user_id,
            project_id=job.project_id,
            operation=f"memoir.job.{job.job_type}",
        )
        handler_task = asyncio.create_task(handler(db, job))
        try:
            done, _pending = await asyncio.wait(
                {handler_task},
                timeout=deadline_seconds - elapsed_before_run,
            )
            if handler_task not in done:
                raise EditingJobDeadlineExceeded
            handler_result = await handler_task
        finally:
            if not handler_task.done():
                handler_task.cancel()
                await asyncio.gather(handler_task, return_exceptions=True)
            reset_ai_usage_context(usage_token)
        marked = await _mark_job(
            db,
            job,
            lease,
            handler_result.status,
            result_json=handler_result.result_json,
            error_message=handler_result.error_message,
        )
        return marked == MemoirEditingJobStatus.SUCCEEDED.value
    except EditingJobDeadlineExceeded:
        await db.rollback()
        error_message = _deadline_error_message(deadline_seconds)
        logger.warning(
            "memoir editing job deadline exceeded job_id=%s timeout_seconds=%s",
            lease.job_id,
            deadline_seconds,
        )
        job = await _load_leased_editing_job(db, lease)
        if job:
            await _mark_linked_events_timed_out(
                db,
                job,
                error_message=error_message,
                timeout_seconds=deadline_seconds,
            )
            result_json = {
                **(job.result_json or {}),
                "requested_model": (job.payload_json or {}).get("requested_ai_model"),
                "model": (job.result_json or {}).get("resolved_ai_model"),
                "provider": (job.result_json or {}).get("provider"),
                "error_type": "job_deadline_exceeded",
                "timeout_seconds": deadline_seconds,
                "elapsed_seconds": deadline_seconds,
                "failed_at": utcnow_naive().isoformat(),
            }
            await _mark_job(
                db,
                job,
                lease,
                MemoirEditingJobStatus.FAILED,
                result_json=result_json,
                error_message=error_message,
            )
        return False
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
                result_json={**(job.result_json or {}), "failure": {"message": str(exc), **ai_error_metadata(
                    exc, requested_model=(job.payload_json or {}).get("requested_ai_model"),
                    provider=None, resolved_model=(job.result_json or {}).get("resolved_ai_model"),
                    timeout_seconds=(getattr(exc, "diagnostics", None) or {}).get("timeout_seconds", deadline_seconds),
                    elapsed_ms=round(
                        (utcnow_naive() - (job.first_started_at or job.created_at)).total_seconds() * 1000
                    ),
                )}},
                error_message=ai_user_error_message(exc, "后台处理失败，已保存的进度会保留，请重试"),
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
        leases = await claim_due_editing_jobs(
            db,
            limit=limit,
            job_type=job_type,
            project_id=project_id,
        )
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


# Only the narration-to-book chain resumes automatically. Interactive generation
# requests and media jobs retain their existing explicit retry/cancellation policy.
_CREDIT_RESUMABLE_TYPES = (
    MemoirEditingJobType.CORRECT_TRANSCRIPT.value,
    MemoirEditingJobType.ORGANIZE_MATERIAL.value,
    MemoirEditingJobType.REGENERATE_CHAPTER_SECTIONS.value,
    MemoirEditingJobType.PREPARE_CHAPTER.value,
    MemoirEditingJobType.UPDATE_BOOK_DRAFT.value,
    MemoirEditingJobType.GENERATE_BOOK_REVIEW.value,
    MemoirEditingJobType.FILL_NARRATOR_PROFILE.value,
    MemoirEditingJobType.UPDATE_TOPIC_MAP.value,
)


async def resume_credit_blocked_editing_jobs(db: AsyncSession) -> int:
    """Reconcile durable credit failures with the wallet, including late failure writes.

    Keeping recovery in the worker avoids coupling payment delivery to memoir jobs
    and covers grants, repeated callbacks, restarts, and payment-before-failure races.
    """
    blocked = (
        MemoirEditingJob.status == MemoirEditingJobStatus.FAILED.value,
        MemoirEditingJob.job_type.in_(_CREDIT_RESUMABLE_TYPES),
        MemoirEditingJob.dedupe_key.is_not(None),  # Superseded jobs lose their key.
        or_(MemoirEditingJob.job_type != MemoirEditingJobType.PREPARE_CHAPTER.value,
            MemoirEditingJob.payload_json["automatic"].as_boolean().is_(True)),
        or_(MemoirEditingJob.job_type != MemoirEditingJobType.GENERATE_BOOK_REVIEW.value,
            MemoirEditingJob.payload_json["input_signature"].as_string().is_not(None)),
        MemoirEditingJob.result_json["failure"]["error_type"].as_string() == InsufficientAICreditError.__name__,
    )
    user_ids = (await db.scalars(select(Project.user_id).join(MemoirEditingJob).where(
        *blocked, Project.user_id.is_not(None),
    ).distinct())).all()
    resumed = 0
    affected_projects: set[str] = set()
    for user_id in user_ids:
        account = await db.scalar(select(AICreditAccount).where(AICreditAccount.user_id == user_id))
        if account is None or (await credit_account_summary(db, account)).remaining_rmb <= 0:
            continue
        jobs = (await db.scalars(select(MemoirEditingJob).join(Project).where(
            *blocked, Project.user_id == user_id,
        ).order_by(MemoirEditingJob.id).with_for_update(of=MemoirEditingJob, skip_locked=True)
            .execution_options(populate_existing=True))).all()
        for job in jobs:
            if (job.result_json or {}).get("cancelled"):
                continue
            progress = dict(job.result_json or {})
            progress.pop("failure", None)
            # The typed credit guard fails BEFORE sending the next provider request.
            # Earlier completed checkpoints remain valid; uncertain timeouts never enter here.
            progress["provider_request_pending"] = False
            job.result_json = progress
            job.status, job.error_message = MemoirEditingJobStatus.PENDING.value, ""
            job.run_after, job.first_started_at, job.attempts = utcnow_naive(), None, 0
            job.locked_at = job.lease_token = job.lease_expires_at = None
            job.rerun_requested = False
            affected_projects.add(job.project_id)
            resumed += 1
    await db.flush()
    from app.services.memoir.automatic_book_draft import schedule_book_draft
    for project_id in sorted(affected_projects):
        # A parent may have completed with a failed child before credit arrived.
        # Wake that parent without retrying unrelated failed children or failed parents.
        parent = await db.scalar(select(MemoirEditingJob).where(
            MemoirEditingJob.dedupe_key == f"update-book-draft:{project_id}",
        ).with_for_update().execution_options(populate_existing=True))
        if parent and parent.status in {*ACTIVE_EDITING_JOB_STATUSES, MemoirEditingJobStatus.SUCCEEDED.value}:
            await schedule_book_draft(db, project_id=project_id, source="credit_restored", settled=True)
    await db.commit()
    return resumed


async def editing_worker_loop(stop_event: asyncio.Event) -> None:
    settings = get_settings()
    poll_seconds = max(1, settings.memoir_editing_poll_seconds)
    book_policy_reconciled = False
    while not stop_event.is_set():
        try:
            from app.services.memoir.book_completion_jobs import advance_book_completion_jobs
            if not book_policy_reconciled:
                from app.services.memoir.automatic_book_draft import reconcile_books_omitted_by_editorial_review
                async with async_session_maker() as db:
                    await reconcile_books_omitted_by_editorial_review(db)
                book_policy_reconciled = True
            async with async_session_maker() as db:
                await resume_credit_blocked_editing_jobs(db)
                await advance_book_completion_jobs(db)
            await run_available_editing_jobs(should_stop=stop_event.is_set)
        except Exception:
            logger.exception("memoir editing worker tick failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_seconds)
        except asyncio.TimeoutError:
            pass
