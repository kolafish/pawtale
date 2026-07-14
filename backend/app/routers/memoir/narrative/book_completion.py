from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.users import User
from app.schemas.memoir_narrative import (
    MemoirBookCompletionOverviewResponse,
    MemoirBookCompletionResponse,
)
from app.services.memoir.narrative import book_completion

from .common import authorize_project


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
    response_model=MemoirBookCompletionResponse,
)
async def prepare_memoir_book_completion_chapter(
    project_id: str,
    chapter_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        response = await book_completion.prepare_book_completion_chapter(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
        )
        await db.commit()
        return response
    except ValueError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail="章节不存在") from error
    except IntegrityError:
        await db.rollback()
        overview = await book_completion.get_book_completion_overview(db, project_id=project_id)
        chapter = next(
            (item for item in overview.chapters if item.chapter_id == chapter_id),
            None,
        )
        if chapter is None:
            raise HTTPException(status_code=404, detail="章节不存在")
        action = {
            "ready": "start_interview",
            "in_progress": "start_interview",
            "preparing": "wait",
            "needs_preparation": "prepare",
            "needs_review": "open_chapter",
            "failed": "retry",
            "complete": "none",
        }[chapter.state]
        return MemoirBookCompletionResponse(
            project_id=project_id,
            state=chapter.state,
            action=action,
            headline=chapter.headline,
            detail=chapter.detail,
            task_id=chapter.task_id,
            chapter_id=chapter.chapter_id,
        )
