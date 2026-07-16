from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.domain.memoir.narrative_types import MemoirFollowupTaskStatus
from app.models.users import User
from app.schemas.memoir_narrative import (
    MemoirCompletionOutcomeResponse,
    MemoirFollowupTaskDeferRequest,
    MemoirFollowupTaskListResponse,
    MemoirFollowupTaskOutcomeRequest,
    MemoirFollowupTaskResponse,
    MemoirFollowupTaskSwitchRequest,
)
from app.services.memoir.narrative import (
    completion_outcomes,
    followup_actions,
    followup_tasks as task_service,
)

from .common import authorize_project, decision_error


router = APIRouter()


@router.get(
    "/projects/{project_id}/voice-sessions/{voice_session_id}/completion-outcome",
    response_model=MemoirCompletionOutcomeResponse,
)
async def get_memoir_completion_outcome(
    project_id: str,
    voice_session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        return await completion_outcomes.completion_outcome(
            db,
            project_id=project_id,
            voice_session_id=voice_session_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="补全会话不存在") from exc


async def _raise_mutation_error(
    db: AsyncSession,
    exc: ValueError | IntegrityError,
) -> NoReturn:
    await db.rollback()
    if isinstance(exc, ValueError):
        raise decision_error(exc) from exc
    raise HTTPException(
        status_code=409,
        detail="follow-up task state changed concurrently; reload and retry",
    ) from exc


async def _commit_task(
    db: AsyncSession,
    task,
) -> MemoirFollowupTaskResponse:
    await db.commit()
    loaded = await task_service.get_task(
        db,
        project_id=task.project_id,
        task_id=task.id,
    )
    if loaded is None:
        raise ValueError("follow-up task does not belong to the project")
    return await task_service.task_response(db, task=loaded)


@router.get(
    "/projects/{project_id}/followup-tasks",
    response_model=MemoirFollowupTaskListResponse,
)
async def list_memoir_followup_tasks(
    project_id: str,
    task_status: list[MemoirFollowupTaskStatus] | None = Query(default=None, alias="status"),
    chapter_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    items, total = await task_service.list_tasks(
        db,
        project_id=project_id,
        statuses=set(task_status) if task_status else None,
        chapter_id=chapter_id,
        limit=limit,
        offset=offset,
    )
    return MemoirFollowupTaskListResponse(
        items=await task_service.task_responses(db, tasks=items),
        total=total,
    )


@router.get(
    "/projects/{project_id}/followup-tasks/{task_id}",
    response_model=MemoirFollowupTaskResponse,
)
async def get_memoir_followup_task(
    project_id: str,
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    task = await task_service.get_task(db, project_id=project_id, task_id=task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="补访任务不存在")
    return await task_service.task_response(db, task=task)


@router.post(
    "/projects/{project_id}/followup-tasks/{task_id}/defer",
    response_model=MemoirFollowupTaskResponse,
)
async def defer_memoir_followup_task(
    project_id: str,
    task_id: str,
    req: MemoirFollowupTaskDeferRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        task = await followup_actions.defer_task(
            db,
            project_id=project_id,
            task_id=task_id,
            actor_user_id=current_user.id,
            idempotency_key=req.idempotency_key,
            reason=req.note,
            defer_until=req.defer_until,
        )
        return await _commit_task(db, task)
    except (ValueError, IntegrityError) as exc:
        await _raise_mutation_error(db, exc)


@router.post(
    "/projects/{project_id}/followup-tasks/{task_id}/unable-to-answer",
    response_model=MemoirFollowupTaskResponse,
)
async def mark_memoir_followup_task_unable_to_answer(
    project_id: str,
    task_id: str,
    req: MemoirFollowupTaskOutcomeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        task = await followup_actions.mark_task_unable_to_answer(
            db,
            project_id=project_id,
            task_id=task_id,
            actor_user_id=current_user.id,
            idempotency_key=req.idempotency_key,
            reason=req.note,
        )
        return await _commit_task(db, task)
    except (ValueError, IntegrityError) as exc:
        await _raise_mutation_error(db, exc)


@router.post(
    "/projects/{project_id}/followup-tasks/{task_id}/stop-asking",
    response_model=MemoirFollowupTaskResponse,
)
async def stop_asking_memoir_followup_task(
    project_id: str,
    task_id: str,
    req: MemoirFollowupTaskOutcomeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        task = await followup_actions.stop_asking_task(
            db,
            project_id=project_id,
            task_id=task_id,
            actor_user_id=current_user.id,
            idempotency_key=req.idempotency_key,
            reason=req.note,
        )
        return await _commit_task(db, task)
    except (ValueError, IntegrityError) as exc:
        await _raise_mutation_error(db, exc)


@router.post(
    "/projects/{project_id}/voice-sessions/{voice_session_id}/followup-task/switch",
    response_model=MemoirFollowupTaskResponse | None,
)
async def switch_memoir_followup_task(
    project_id: str,
    voice_session_id: str,
    req: MemoirFollowupTaskSwitchRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        task = await followup_actions.switch_session_task(
            db,
            project_id=project_id,
            voice_session_id=voice_session_id,
            current_task_id=req.current_task_id,
            actor_user_id=current_user.id,
            idempotency_key=req.idempotency_key,
            reason=req.note,
        )
        await db.commit()
        return await task_service.task_response(db, task=task) if task else None
    except (ValueError, IntegrityError) as exc:
        await _raise_mutation_error(db, exc)
