from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import commit_or_rollback, get_db
from app.models.users import User
from app.schemas.memoir_book import MemoirBookAssemblyResponse
from app.services.memoir.narrative import book_output_assembly

from .common import authorize_project, decision_error


router = APIRouter()


@router.post(
    "/projects/{project_id}/book-output/assemble",
    response_model=MemoirBookAssemblyResponse,
)
async def assemble_memoir_book_output(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        async with commit_or_rollback(db):
            return await book_output_assembly.assemble_book_output(
                db,
                project_id=project_id,
            )
    except ValueError as exc:
        raise decision_error(exc) from exc
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail="book output changed concurrently",
        ) from exc
