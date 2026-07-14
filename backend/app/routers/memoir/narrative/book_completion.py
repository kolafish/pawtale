from fastapi import APIRouter, Depends
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.users import User
from app.schemas.memoir_narrative import MemoirBookCompletionResponse
from app.services.memoir.narrative import book_completion

from .common import authorize_project


router = APIRouter()


@router.get(
    "/projects/{project_id}/book-completion",
    response_model=MemoirBookCompletionResponse,
)
async def get_memoir_book_completion(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    return await book_completion.get_book_completion_status(db, project_id=project_id)


@router.post(
    "/projects/{project_id}/book-completion/prepare",
    response_model=MemoirBookCompletionResponse,
)
async def prepare_memoir_book_completion(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        response = await book_completion.prepare_book_completion(db, project_id=project_id)
        await db.commit()
        return response
    except IntegrityError:
        await db.rollback()
        return await book_completion.get_book_completion_status(db, project_id=project_id)
