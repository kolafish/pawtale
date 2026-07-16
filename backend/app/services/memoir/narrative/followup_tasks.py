from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.narrative_types import (
    MemoirFollowupTaskStatus,
    MemoirNarrativeActorType,
    MemoirNarrativeDecisionAction,
    MemoirNarrativeDecisionTargetType,
)
from app.models.memoir_narrative import MemoirFollowupTask
from app.schemas.memoir_narrative import MemoirFollowupTaskResponse
from app.services.memoir.narrative import decision_log


ACTIVE_TASK_STATUSES = {
    MemoirFollowupTaskStatus.PENDING,
    MemoirFollowupTaskStatus.IN_PROGRESS,
    MemoirFollowupTaskStatus.DEFERRED,
}
TERMINAL_TASK_STATUSES = {
    MemoirFollowupTaskStatus.ANSWERED,
    MemoirFollowupTaskStatus.UNABLE_TO_ANSWER,
    MemoirFollowupTaskStatus.CLOSED_WITHOUT_ANSWER,
    MemoirFollowupTaskStatus.CANCELLED,
}
ALLOWED_TASK_TRANSITIONS = {
    MemoirFollowupTaskStatus.PENDING: {
        MemoirFollowupTaskStatus.IN_PROGRESS,
        MemoirFollowupTaskStatus.ANSWERED,
        MemoirFollowupTaskStatus.CANCELLED,
    },
    MemoirFollowupTaskStatus.IN_PROGRESS: {
        MemoirFollowupTaskStatus.ANSWERED,
        MemoirFollowupTaskStatus.DEFERRED,
        MemoirFollowupTaskStatus.UNABLE_TO_ANSWER,
        MemoirFollowupTaskStatus.CLOSED_WITHOUT_ANSWER,
        MemoirFollowupTaskStatus.CANCELLED,
    },
    MemoirFollowupTaskStatus.DEFERRED: {
        MemoirFollowupTaskStatus.PENDING,
        MemoirFollowupTaskStatus.ANSWERED,
        MemoirFollowupTaskStatus.CANCELLED,
    },
}


class FollowupTaskConflictError(ValueError):
    pass


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def task_load_options():
    return (
        selectinload(MemoirFollowupTask.claims),
        selectinload(MemoirFollowupTask.utterances),
    )


async def get_task(
    db: AsyncSession,
    *,
    project_id: str,
    task_id: str,
) -> MemoirFollowupTask | None:
    return await db.scalar(
        select(MemoirFollowupTask)
        .options(*task_load_options())
        .execution_options(populate_existing=True)
        .where(
            MemoirFollowupTask.project_id == project_id,
            MemoirFollowupTask.id == task_id,
        )
    )


async def task_response(
    db: AsyncSession,
    *,
    task: MemoirFollowupTask,
) -> MemoirFollowupTaskResponse:
    if "claims" not in task.__dict__ or "utterances" not in task.__dict__:
        loaded = await get_task(db, project_id=task.project_id, task_id=task.id)
        if loaded is None:
            raise ValueError("follow-up task does not belong to the project")
        task = loaded
    return MemoirFollowupTaskResponse.model_validate(task)


async def list_tasks(
    db: AsyncSession,
    *,
    project_id: str,
    statuses: set[MemoirFollowupTaskStatus] | None = None,
    chapter_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[MemoirFollowupTask], int]:
    filters = [MemoirFollowupTask.project_id == project_id]
    if statuses:
        filters.append(MemoirFollowupTask.status.in_([status.value for status in statuses]))
    if chapter_id:
        filters.append(MemoirFollowupTask.chapter_id == chapter_id)
    total = await db.scalar(select(func.count(MemoirFollowupTask.id)).where(*filters)) or 0
    items = list(
        (
            await db.scalars(
                select(MemoirFollowupTask)
                .options(*task_load_options())
                .execution_options(populate_existing=True)
                .where(*filters)
                .order_by(
                    MemoirFollowupTask.priority.desc(),
                    MemoirFollowupTask.created_at,
                    MemoirFollowupTask.id,
                )
                .offset(offset)
                .limit(limit)
            )
        ).unique().all()
    )
    return items, int(total)


def task_state(task: MemoirFollowupTask) -> dict[str, object]:
    return {
        "status": task.status,
        "assigned_session_id": task.assigned_session_id,
        "defer_until": task.defer_until.isoformat() if task.defer_until else None,
        "defer_reason": task.defer_reason,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
    }


def _decision_action(status: MemoirFollowupTaskStatus) -> MemoirNarrativeDecisionAction:
    if status == MemoirFollowupTaskStatus.UNABLE_TO_ANSWER:
        return MemoirNarrativeDecisionAction.UNABLE_TO_ANSWER
    if status == MemoirFollowupTaskStatus.CLOSED_WITHOUT_ANSWER:
        return MemoirNarrativeDecisionAction.STOP_ASKING
    return MemoirNarrativeDecisionAction.RESOLVE


def _transition_values(
    *,
    status: MemoirFollowupTaskStatus,
    assigned_session_id: str | None,
    defer_until: datetime | None,
    reason: str,
) -> dict[str, object]:
    now = _utcnow()
    if status == MemoirFollowupTaskStatus.PENDING:
        return {
            "status": status.value,
            "assigned_session_id": None,
            "started_at": None,
            "completed_at": None,
            "defer_until": None,
            "defer_reason": "",
            "updated_at": now,
        }
    if status == MemoirFollowupTaskStatus.IN_PROGRESS:
        if not assigned_session_id:
            raise ValueError("starting a follow-up task requires a voice session")
        return {
            "status": status.value,
            "assigned_session_id": assigned_session_id,
            "started_at": now,
            "completed_at": None,
            "defer_until": None,
            "defer_reason": "",
            "updated_at": now,
        }
    if status == MemoirFollowupTaskStatus.DEFERRED:
        if not reason.strip():
            raise ValueError("deferring a follow-up task requires a reason")
        return {
            "status": status.value,
            "assigned_session_id": None,
            "completed_at": None,
            "defer_until": defer_until,
            "defer_reason": reason.strip(),
            "updated_at": now,
        }
    return {
        "status": status.value,
        "completed_at": now,
        "defer_until": None,
        "defer_reason": "",
        "updated_at": now,
    }


async def transition_task(
    db: AsyncSession,
    *,
    project_id: str,
    task_id: str,
    status: MemoirFollowupTaskStatus,
    actor_type: MemoirNarrativeActorType,
    actor_user_id: str | None,
    idempotency_key: str,
    reason: str,
    assigned_session_id: str | None = None,
    defer_until: datetime | None = None,
) -> MemoirFollowupTask:
    action = _decision_action(status)
    existing = await decision_log.existing_decision(
        db,
        project_id=project_id,
        idempotency_key=idempotency_key,
        target_type=MemoirNarrativeDecisionTargetType.FOLLOWUP_TASK,
        target_id=task_id,
        action=action,
        actor_type=actor_type,
        actor_user_id=actor_user_id,
    )
    task = await get_task(db, project_id=project_id, task_id=task_id)
    if task is None:
        raise ValueError("follow-up task does not belong to the project")
    if existing is not None:
        if existing.after_state_json.get("status") != status.value:
            raise ValueError("idempotency key was already used for another task transition")
        return task

    current_status = MemoirFollowupTaskStatus(task.status)
    if status not in ALLOWED_TASK_TRANSITIONS.get(current_status, set()):
        raise ValueError(f"follow-up task cannot transition from {current_status.value} to {status.value}")
    before = task_state(task)
    values = _transition_values(
        status=status,
        assigned_session_id=assigned_session_id,
        defer_until=defer_until,
        reason=reason,
    )
    result = await db.execute(
        update(MemoirFollowupTask)
        .where(
            MemoirFollowupTask.project_id == project_id,
            MemoirFollowupTask.id == task_id,
            MemoirFollowupTask.status == current_status.value,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise FollowupTaskConflictError(
            "follow-up task state changed concurrently; reload and retry"
        )
    await db.refresh(task)
    await decision_log.record_decision(
        db,
        project_id=project_id,
        target_type=MemoirNarrativeDecisionTargetType.FOLLOWUP_TASK,
        target_id=task.id,
        action=action,
        actor_type=actor_type,
        actor_user_id=actor_user_id,
        before_state=before,
        after_state=task_state(task),
        reason=reason.strip(),
        idempotency_key=idempotency_key,
    )
    return task


async def cancel_active_tasks_for_gap(
    db: AsyncSession,
    *,
    project_id: str,
    gap_id: str,
    reason: str,
    decision_identity: str,
) -> int:
    tasks = list(
        (
            await db.scalars(
                select(MemoirFollowupTask).where(
                    MemoirFollowupTask.project_id == project_id,
                    MemoirFollowupTask.gap_id == gap_id,
                    MemoirFollowupTask.status.in_(
                        [status.value for status in ACTIVE_TASK_STATUSES]
                    ),
                )
            )
        ).all()
    )
    cancelled = 0
    for task in tasks:
        await transition_task(
            db,
            project_id=project_id,
            task_id=task.id,
            status=MemoirFollowupTaskStatus.CANCELLED,
            actor_type=MemoirNarrativeActorType.SYSTEM,
            actor_user_id=None,
            idempotency_key=f"cancel-followup:{decision_identity}:{task.id}",
            reason=reason,
        )
        cancelled += 1
    return cancelled


async def reactivate_cancelled_task_for_gap(
    db: AsyncSession,
    *,
    project_id: str,
    gap_id: str,
    reason: str,
    decision_identity: str,
) -> int:
    """Restore the one-to-one task when a system-resolved Gap recurs."""
    task = await db.scalar(
        select(MemoirFollowupTask).where(
            MemoirFollowupTask.project_id == project_id,
            MemoirFollowupTask.gap_id == gap_id,
            MemoirFollowupTask.status == MemoirFollowupTaskStatus.CANCELLED.value,
        )
    )
    if task is None:
        return 0

    idempotency_key = f"reactivate-followup:{decision_identity}:{task.id}"
    existing = await decision_log.existing_decision(
        db,
        project_id=project_id,
        idempotency_key=idempotency_key,
        target_type=MemoirNarrativeDecisionTargetType.FOLLOWUP_TASK,
        target_id=task.id,
        action=MemoirNarrativeDecisionAction.NEEDS_REVIEW,
        actor_type=MemoirNarrativeActorType.SYSTEM,
        actor_user_id=None,
    )
    if existing is not None:
        return int(task.status == MemoirFollowupTaskStatus.PENDING.value)

    before = task_state(task)
    result = await db.execute(
        update(MemoirFollowupTask)
        .where(
            MemoirFollowupTask.project_id == project_id,
            MemoirFollowupTask.id == task.id,
            MemoirFollowupTask.status == MemoirFollowupTaskStatus.CANCELLED.value,
        )
        .values(
            **_transition_values(
                status=MemoirFollowupTaskStatus.PENDING,
                assigned_session_id=None,
                defer_until=None,
                reason="",
            )
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise FollowupTaskConflictError(
            "follow-up task state changed concurrently; reload and retry"
        )
    await db.refresh(task)
    await decision_log.record_decision(
        db,
        project_id=project_id,
        target_type=MemoirNarrativeDecisionTargetType.FOLLOWUP_TASK,
        target_id=task.id,
        action=MemoirNarrativeDecisionAction.NEEDS_REVIEW,
        actor_type=MemoirNarrativeActorType.SYSTEM,
        actor_user_id=None,
        before_state=before,
        after_state=task_state(task),
        reason=reason.strip(),
        idempotency_key=idempotency_key,
    )
    return 1
