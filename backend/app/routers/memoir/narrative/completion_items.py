from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.domain.memoir.narrative_types import MemoirCompletionItemStatus
from app.models.memoir import MemoirVoiceSession, MemoirVoiceUtterance
from app.models.users import User
from app.schemas.memoir_narrative import (
    MemoirChapterWorkspaceResponse,
    MemoirCompletionItemActionRequest,
    MemoirCompletionItemDeferRequest,
    MemoirCompletionItemResponse,
    MemoirCompletionItemSwitchRequest,
    MemoirCompletionOutcomeResponse,
)
from app.services.memoir.narrative import chapter_workspace, completion_items

from .common import authorize_project, decision_error


router = APIRouter()


@router.get(
    "/projects/{project_id}/chapters/{chapter_id}/workspace",
    response_model=MemoirChapterWorkspaceResponse,
)
async def get_memoir_chapter_workspace(
    project_id: str,
    chapter_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        return await chapter_workspace.get_chapter_workspace(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
        )
    except ValueError as exc:
        raise decision_error(exc) from exc


@router.get(
    "/projects/{project_id}/completion-items/{item_id}",
    response_model=MemoirCompletionItemResponse,
)
async def get_completion_item(
    project_id: str,
    item_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    item = await completion_items.get_item(db, project_id=project_id, item_id=item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="补全项不存在")
    return item


async def _finish_item(
    *,
    db: AsyncSession,
    project_id: str,
    item_id: str,
    status: MemoirCompletionItemStatus,
    note: str,
):
    try:
        item = await completion_items.resolve_item(
            db,
            project_id=project_id,
            item_id=item_id,
            status=status,
            note=note,
        )
        await db.commit()
        return item
    except ValueError as exc:
        await db.rollback()
        raise decision_error(exc) from exc


@router.post(
    "/projects/{project_id}/completion-items/{item_id}/resolve",
    response_model=MemoirCompletionItemResponse,
)
async def resolve_completion_item(
    project_id: str,
    item_id: str,
    req: MemoirCompletionItemActionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    return await _finish_item(
        db=db,
        project_id=project_id,
        item_id=item_id,
        status=MemoirCompletionItemStatus.RESOLVED,
        note=req.note,
    )


@router.post(
    "/projects/{project_id}/completion-items/{item_id}/defer",
    response_model=MemoirCompletionItemResponse,
)
async def defer_completion_item(
    project_id: str,
    item_id: str,
    req: MemoirCompletionItemDeferRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        item = await completion_items.defer_item(
            db,
            project_id=project_id,
            item_id=item_id,
            note=req.note,
            defer_until=req.defer_until,
        )
        await db.commit()
        return item
    except ValueError as exc:
        await db.rollback()
        raise decision_error(exc) from exc


@router.post(
    "/projects/{project_id}/completion-items/{item_id}/unable-to-answer",
    response_model=MemoirCompletionItemResponse,
)
async def mark_completion_item_unable_to_answer(
    project_id: str,
    item_id: str,
    req: MemoirCompletionItemActionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    return await _finish_item(
        db=db,
        project_id=project_id,
        item_id=item_id,
        status=MemoirCompletionItemStatus.UNABLE_TO_ANSWER,
        note=req.note,
    )


@router.post(
    "/projects/{project_id}/voice-sessions/{voice_session_id}/completion-item/switch",
    response_model=MemoirCompletionItemResponse | None,
)
async def switch_completion_item(
    project_id: str,
    voice_session_id: str,
    req: MemoirCompletionItemSwitchRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    session = await db.get(MemoirVoiceSession, voice_session_id)
    if session is None or session.project_id != project_id:
        raise HTTPException(status_code=404, detail="访谈会话不存在")
    if session.current_completion_item_id != req.current_item_id:
        raise HTTPException(status_code=409, detail="当前补全项已经变化，请刷新后重试")
    current_item_id = session.current_completion_item_id
    await completion_items.release_session_item(
        db,
        voice_session=session,
        reason=req.note or "用户选择了下一项",
    )
    item = await completion_items.select_next_item(
        db,
        project_id=project_id,
        exclude_item_id=current_item_id,
    )
    if item is not None:
        await completion_items.start_item_for_session(
            db,
            item=item,
            voice_session=session,
        )
    await db.commit()
    return item


@router.get(
    "/projects/{project_id}/voice-sessions/{voice_session_id}/completion-outcome",
    response_model=MemoirCompletionOutcomeResponse,
)
async def get_completion_outcome(
    project_id: str,
    voice_session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    session = await db.get(MemoirVoiceSession, voice_session_id)
    if session is None or session.project_id != project_id:
        raise HTTPException(status_code=404, detail="补全会话不存在")
    latest_answer = await db.scalar(
        select(MemoirVoiceUtterance)
        .where(
            MemoirVoiceUtterance.voice_session_id == voice_session_id,
            MemoirVoiceUtterance.completion_item_id.is_not(None),
        )
        .order_by(MemoirVoiceUtterance.created_at.desc())
        .limit(1)
    )
    item_id = session.current_completion_item_id or (
        latest_answer.completion_item_id if latest_answer else None
    )
    item = (
        await completion_items.get_item(db, project_id=project_id, item_id=item_id)
        if item_id
        else None
    )
    if item is None:
        return MemoirCompletionOutcomeResponse(
            status="not_used",
            voice_session_id=voice_session_id,
            message="本次会话没有关联补全项。",
        )
    incorporated = item.status == MemoirCompletionItemStatus.RESOLVED.value
    utterance_id = (
        latest_answer.id
        if latest_answer is not None
        and latest_answer.completion_item_id == item.id
        else await db.scalar(
            select(MemoirVoiceUtterance.id)
            .where(
                MemoirVoiceUtterance.voice_session_id == voice_session_id,
                MemoirVoiceUtterance.completion_item_id == item.id,
            )
            .order_by(MemoirVoiceUtterance.created_at.desc())
            .limit(1)
        )
    )
    return MemoirCompletionOutcomeResponse(
        status="incorporated" if incorporated else "processing",
        voice_session_id=voice_session_id,
        item_id=item.id,
        item_status=item.status,
        chapter_id=item.chapter_id,
        utterance_id=utterance_id,
        message="回答已保存为本章素材。" if incorporated else "回答正在保存和整理。",
    )
