from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.users import User
from app.schemas.memoir_narrative import (
    MemoirBookCompletionOverviewResponse,
    MemoirChapterWorkspaceResponse,
)
from app.services.memoir.narrative import book_completion
from app.services.memoir import completion_read_model

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
        return await completion_read_model.chapter_workspace(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
        )
    except ValueError as exc:
        raise decision_error(exc) from exc


@router.get(
    "/projects/{project_id}/book-completion/overview",
    response_model=MemoirBookCompletionOverviewResponse,
)
async def get_memoir_book_completion_overview(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    return await book_completion.get_book_completion_overview(db, project_id=project_id)


@router.post(
    "/projects/{project_id}/book-completion/chapters/{chapter_id}/prepare",
    response_model=MemoirChapterWorkspaceResponse,
)
async def prepare_memoir_book_completion_chapter(
    project_id: str,
    chapter_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        workspace = await book_completion.prepare_chapter(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
            user_id=current_user.id,
        )
        await db.commit()
        return workspace
    except ValueError as exc:
        await db.rollback()
        raise decision_error(exc) from exc


@router.post(
    "/projects/{project_id}/book-completion/chapters/{chapter_id}/rewrite",
    response_model=MemoirChapterWorkspaceResponse,
)
async def rewrite_memoir_book_completion_chapter(
    project_id: str,
    chapter_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        workspace = await book_completion.rewrite_chapter(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
            user_id=current_user.id,
        )
        await db.commit()
        return workspace
    except ValueError as exc:
        await db.rollback()
        raise decision_error(exc) from exc
