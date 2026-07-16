from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import (
    MemoirFollowupTaskStatus,
    MemoirNarrativeActorType,
)
from app.models.memoir import MemoirVoiceSession
from app.services.memoir.narrative import (
    followup_planner,
    followup_tasks,
)


async def defer_task(
    db: AsyncSession,
    *,
    project_id: str,
    task_id: str,
    actor_user_id: str,
    idempotency_key: str,
    reason: str,
    defer_until: datetime | None,
):
    return await followup_tasks.transition_task(
        db,
        project_id=project_id,
        task_id=task_id,
        status=MemoirFollowupTaskStatus.DEFERRED,
        actor_type=MemoirNarrativeActorType.USER,
        actor_user_id=actor_user_id,
        idempotency_key=idempotency_key,
        reason=reason,
        defer_until=defer_until,
    )


async def resume_task(
    db: AsyncSession,
    *,
    project_id: str,
    task_id: str,
    actor_user_id: str,
    idempotency_key: str,
    reason: str,
):
    return await followup_tasks.transition_task(
        db,
        project_id=project_id,
        task_id=task_id,
        status=MemoirFollowupTaskStatus.PENDING,
        actor_type=MemoirNarrativeActorType.USER,
        actor_user_id=actor_user_id,
        idempotency_key=idempotency_key,
        reason=reason or "user resumed the follow-up task",
    )


async def mark_task_unable_to_answer(
    db: AsyncSession,
    *,
    project_id: str,
    task_id: str,
    actor_user_id: str,
    idempotency_key: str,
    reason: str,
):
    task = await followup_tasks.get_task(db, project_id=project_id, task_id=task_id)
    if task is None:
        raise ValueError("follow-up task does not belong to the project")
    return await followup_tasks.transition_task(
        db,
        project_id=project_id,
        task_id=task_id,
        status=MemoirFollowupTaskStatus.UNABLE_TO_ANSWER,
        actor_type=MemoirNarrativeActorType.USER,
        actor_user_id=actor_user_id,
        idempotency_key=idempotency_key,
        reason=reason,
    )


async def stop_asking_task(
    db: AsyncSession,
    *,
    project_id: str,
    task_id: str,
    actor_user_id: str,
    idempotency_key: str,
    reason: str,
):
    if not reason.strip():
        raise ValueError("stopping a follow-up task requires a reason")
    task = await followup_tasks.get_task(db, project_id=project_id, task_id=task_id)
    if task is None:
        raise ValueError("follow-up task does not belong to the project")
    return await followup_tasks.transition_task(
        db,
        project_id=project_id,
        task_id=task_id,
        status=MemoirFollowupTaskStatus.CLOSED_WITHOUT_ANSWER,
        actor_type=MemoirNarrativeActorType.USER,
        actor_user_id=actor_user_id,
        idempotency_key=idempotency_key,
        reason=reason,
    )


async def cancel_task(
    db: AsyncSession,
    *,
    project_id: str,
    task_id: str,
    actor_user_id: str,
    idempotency_key: str,
    reason: str,
):
    return await followup_tasks.transition_task(
        db,
        project_id=project_id,
        task_id=task_id,
        status=MemoirFollowupTaskStatus.CANCELLED,
        actor_type=MemoirNarrativeActorType.USER,
        actor_user_id=actor_user_id,
        idempotency_key=idempotency_key,
        reason=reason or "user cancelled the follow-up task",
    )


async def switch_session_task(
    db: AsyncSession,
    *,
    project_id: str,
    voice_session_id: str,
    current_task_id: str,
    actor_user_id: str,
    idempotency_key: str,
    reason: str,
):
    voice_session = await db.scalar(
        select(MemoirVoiceSession).where(
            MemoirVoiceSession.project_id == project_id,
            MemoirVoiceSession.id == voice_session_id,
        )
    )
    if voice_session is None:
        raise ValueError("voice session does not belong to the project")
    current = await followup_tasks.get_task(
        db,
        project_id=project_id,
        task_id=current_task_id,
    )
    if current is None:
        raise ValueError("follow-up task does not belong to the project")
    if voice_session.current_followup_task_id != current_task_id:
        return (
            await followup_tasks.get_task(
                db,
                project_id=project_id,
                task_id=voice_session.current_followup_task_id,
            )
            if voice_session.current_followup_task_id
            else None
        )
    if current.status == MemoirFollowupTaskStatus.IN_PROGRESS.value:
        await defer_task(
            db,
            project_id=project_id,
            task_id=current.id,
            actor_user_id=actor_user_id,
            idempotency_key=f"{idempotency_key}:defer",
            reason=reason,
            defer_until=None,
        )
    elif current.status not in {
        status.value for status in followup_tasks.TERMINAL_TASK_STATUSES
    }:
        raise ValueError("current follow-up task is not switchable")
    next_task = await followup_planner.select_next_task(
        db,
        project_id=project_id,
        preferred_chapter_id=current.chapter_id,
        eligible_chapter_ids={current.chapter_id} if current.chapter_id else None,
        avoid_story_id=current.story_id,
    )
    voice_session.current_followup_task_id = None
    if next_task is not None:
        next_task = await followup_planner.start_task_for_session(
            db,
            task=next_task,
            voice_session=voice_session,
            actor_user_id=actor_user_id,
        )
    db.add(voice_session)
    await db.flush()
    return next_task
