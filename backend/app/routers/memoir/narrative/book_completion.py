from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.users import User
from app.schemas.memoir_narrative import (
    MemoirBookCompletionOverviewResponse,
    MemoirChapterPreparationJobResponse,
)
from app.services.memoir.narrative import book_completion

from .common import authorize_project, decision_error


router = APIRouter()


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
    response_model=MemoirChapterPreparationJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def prepare_memoir_book_completion_chapter(
    project_id: str,
    chapter_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        job = await book_completion.prepare_chapter(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
            user_id=current_user.id,
        )
        await db.commit()
        await db.refresh(job)
        return MemoirChapterPreparationJobResponse.model_validate(job)
    except (ValueError, IntegrityError) as exc:
        await db.rollback()
        if isinstance(exc, ValueError):
            raise decision_error(exc) from exc
        raise HTTPException(
            status_code=409,
            detail="chapter preparation changed concurrently; reload and retry",
        ) from exc
