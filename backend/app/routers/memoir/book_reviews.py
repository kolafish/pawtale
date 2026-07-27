from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, is_admin_user
from app.database import get_db
from app.models.memoir import MemoirBookReview
from app.models.users import User
from app.schemas.memoir_book_review import (
    MemoirBookReviewGenerateRequest,
    MemoirBookReviewResponse,
)
from app.services.memoir.access import load_memoir_project
from app.services.memoir import book_reviews
from app.services.memoir.project_ai_preferences import select_project_ai_model


router = APIRouter(tags=["memoir-book-reviews"])


def _response(review: MemoirBookReview) -> MemoirBookReviewResponse:
    return MemoirBookReviewResponse(
        id=review.id,
        project_id=review.project_id,
        input_signature=review.input_signature,
        engine=review.engine,
        status=review.status,
        review=review.review_json,
        error_message=review.error_message,
        created_at=review.created_at,
        updated_at=review.updated_at,
    )


@router.get(
    "/projects/{project_id}/book-review",
    response_model=MemoirBookReviewResponse | None,
)
async def get_memoir_book_review(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await load_memoir_project(
        db,
        project_id,
        current_user.id,
        allow_admin=is_admin_user(current_user),
    )
    review = await book_reviews.get_current_book_review(
        db,
        project_id=project_id,
    )
    return _response(review) if review else None


@router.post(
    "/projects/{project_id}/book-review",
    response_model=MemoirBookReviewResponse,
)
async def generate_memoir_book_review(
    project_id: str,
    req: MemoirBookReviewGenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = await load_memoir_project(
        db,
        project_id,
        current_user.id,
        allow_admin=is_admin_user(current_user),
    )
    review = await book_reviews.generate_or_reuse_book_review(
        db,
        project_id=project_id,
        ai_model=select_project_ai_model(project, req.ai_model),
        force_regenerate=req.force_regenerate,
    )
    await db.commit()
    return _response(review)
