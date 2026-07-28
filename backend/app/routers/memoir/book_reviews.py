from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, is_admin_user
from app.database import get_db
from app.models.memoir import MemoirBookReview
from app.models.users import User
from app.schemas.memoir_book_review import (
    MemoirBookReviewAnswerApplicationJobResponse,
    MemoirBookReviewAnswerApplyRequest,
    MemoirBookReviewGenerateRequest,
    MemoirBookReviewJobResponse,
    MemoirBookReviewResponse,
)
from app.services.memoir.access import load_memoir_project
from app.services.memoir import (
    book_review_answer_jobs,
    book_review_jobs,
    book_reviews,
)
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


@router.post(
    "/projects/{project_id}/book-review/jobs",
    response_model=MemoirBookReviewJobResponse,
    status_code=202,
)
async def enqueue_memoir_book_review_job(
    project_id: str,
    req: MemoirBookReviewGenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return await book_review_jobs.enqueue_book_review_job(
            db,
            user_id=current_user.id,
            project_id=project_id,
            req=req,
            allow_admin=is_admin_user(current_user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/projects/{project_id}/book-review/jobs/current",
    response_model=MemoirBookReviewJobResponse,
)
async def get_current_memoir_book_review_job(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await book_review_jobs.get_book_review_job(
        db,
        user_id=current_user.id,
        project_id=project_id,
        allow_admin=is_admin_user(current_user),
    )


@router.delete(
    "/projects/{project_id}/book-review/jobs/{job_id}",
    response_model=MemoirBookReviewJobResponse,
)
async def cancel_memoir_book_review_job(
    project_id: str,
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return await book_review_jobs.cancel_book_review_job(
            db,
            user_id=current_user.id,
            project_id=project_id,
            job_id=job_id,
            allow_admin=is_admin_user(current_user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/projects/{project_id}/book-review/answer-application/jobs",
    response_model=MemoirBookReviewAnswerApplicationJobResponse,
    status_code=202,
)
async def enqueue_memoir_book_review_answer_application_job(
    project_id: str,
    req: MemoirBookReviewAnswerApplyRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return await (
            book_review_answer_jobs.enqueue_book_review_answer_application_job(
                db,
                user_id=current_user.id,
                project_id=project_id,
                req=req,
                allow_admin=is_admin_user(current_user),
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/projects/{project_id}/book-review/answer-application/jobs/current",
    response_model=MemoirBookReviewAnswerApplicationJobResponse,
)
async def get_current_memoir_book_review_answer_application_job(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await (
        book_review_answer_jobs.get_book_review_answer_application_job(
            db,
            user_id=current_user.id,
            project_id=project_id,
            allow_admin=is_admin_user(current_user),
        )
    )


@router.delete(
    "/projects/{project_id}/book-review/answer-application/jobs/{job_id}",
    response_model=MemoirBookReviewAnswerApplicationJobResponse,
)
async def cancel_memoir_book_review_answer_application_job(
    project_id: str,
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return await (
            book_review_answer_jobs.cancel_book_review_answer_application_job(
                db,
                user_id=current_user.id,
                project_id=project_id,
                job_id=job_id,
                allow_admin=is_admin_user(current_user),
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
