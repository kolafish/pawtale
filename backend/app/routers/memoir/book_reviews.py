from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, is_admin_user
from app.database import get_db
from app.models.users import User
from app.schemas.memoir_book_review import (
    MemoirBookReviewGenerateRequest,
    MemoirBookReviewJobResponse,
    MemoirBookRevisionJobResponse,
    MemoirBookRevisionRequest,
    MemoirBookRevisionFeedbackJobResponse,
    MemoirBookRevisionDraftJobResponse,
    MemoirBookRevisionDraftRequest,
    MemoirBookRevisionFeedbackRequest,
    MemoirBookRevisionProposalResponse,
)
from app.services.memoir.access import load_memoir_project
from app.services.memoir import (
    book_review_jobs,
    book_revision_jobs,
    book_revision_proposals,
)


router = APIRouter(tags=["memoir-book-reviews"])


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
        result = await book_review_jobs.enqueue_book_review_job(
            db,
            user_id=current_user.id,
            project_id=project_id,
            req=req,
            allow_admin=is_admin_user(current_user),
        )
        await db.commit()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


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
    "/projects/{project_id}/book-review/revision/jobs",
    response_model=MemoirBookRevisionJobResponse,
    status_code=202,
)
async def enqueue_memoir_book_revision_job(
    project_id: str,
    req: MemoirBookRevisionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return await book_revision_jobs.enqueue_book_revision_job(
            db,
            user_id=current_user.id,
            project_id=project_id,
            source="review_selection",
            ai_model=req.ai_model,
            include_answers=req.include_answers,
            action_ids=req.action_ids,
            allow_admin=is_admin_user(current_user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete(
    "/projects/{project_id}/book-review/revision/jobs/{job_id}",
    response_model=MemoirBookRevisionJobResponse,
)
async def cancel_memoir_book_revision_job(
    project_id: str,
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return await book_revision_jobs.cancel_book_revision_job(
            db,
            user_id=current_user.id,
            project_id=project_id,
            source="review_selection",
            job_id=job_id,
            allow_admin=is_admin_user(current_user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/projects/{project_id}/book-review/revision-proposals/{proposal_id}",
    response_model=MemoirBookRevisionProposalResponse,
)
async def get_memoir_book_revision_proposal(
    project_id: str,
    proposal_id: str,
    preview: Literal["none", "before", "after", "both"] = "both",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await load_memoir_project(
        db,
        project_id,
        current_user.id,
        allow_admin=is_admin_user(current_user),
    )
    proposal = await book_revision_proposals.load_proposal(
        db,
        project_id=project_id,
        proposal_id=proposal_id,
    )
    return await book_revision_proposals.proposal_response(
        db,
        proposal=proposal,
        preview=preview,
    )


@router.post(
    "/projects/{project_id}/book-review/revision-proposals/{proposal_id}/feedback/jobs",
    response_model=MemoirBookRevisionFeedbackJobResponse,
    status_code=202,
)
async def enqueue_memoir_book_revision_feedback_job(
    project_id: str,
    proposal_id: str,
    req: MemoirBookRevisionFeedbackRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return await book_revision_jobs.enqueue_book_revision_job(
            db,
            user_id=current_user.id,
            project_id=project_id,
            source="proposal_feedback",
            proposal_id=proposal_id,
            feedback=req.feedback,
            ai_model=req.ai_model,
            allow_admin=is_admin_user(current_user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/projects/{project_id}/book-review/revision-proposals/{proposal_id}/feedback/jobs/current",
    response_model=MemoirBookRevisionFeedbackJobResponse,
)
async def get_current_memoir_book_revision_feedback_job(
    project_id: str,
    proposal_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await book_revision_jobs.get_book_revision_job(
        db,
        user_id=current_user.id,
        project_id=project_id,
        source="proposal_feedback",
        proposal_id=proposal_id,
        allow_admin=is_admin_user(current_user),
    )


@router.post(
    "/projects/{project_id}/book-review/revision-proposals/{proposal_id}/draft/jobs",
    response_model=MemoirBookRevisionDraftJobResponse,
    status_code=202,
)
async def enqueue_memoir_book_revision_draft_job(
    project_id: str,
    proposal_id: str,
    req: MemoirBookRevisionDraftRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return await book_revision_jobs.enqueue_book_revision_job(
            db,
            user_id=current_user.id,
            project_id=project_id,
            source="proposal_draft",
            proposal_id=proposal_id,
            ai_model=req.ai_model,
            allow_admin=is_admin_user(current_user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/projects/{project_id}/book-review/revision-proposals/{proposal_id}/draft/jobs/current",
    response_model=MemoirBookRevisionDraftJobResponse,
)
async def get_current_memoir_book_revision_draft_job(
    project_id: str,
    proposal_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await book_revision_jobs.get_book_revision_job(
        db,
        user_id=current_user.id,
        project_id=project_id,
        source="proposal_draft",
        proposal_id=proposal_id,
        allow_admin=is_admin_user(current_user),
    )


@router.post(
    "/projects/{project_id}/book-review/revision-proposals/{proposal_id}/apply",
    response_model=MemoirBookRevisionProposalResponse,
)
async def apply_memoir_book_revision_proposal(
    project_id: str,
    proposal_id: str,
    preview: Literal["none", "before", "after", "both"] = "both",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await load_memoir_project(
        db,
        project_id,
        current_user.id,
        allow_admin=is_admin_user(current_user),
    )
    proposal = await book_revision_proposals.load_proposal(
        db,
        project_id=project_id,
        proposal_id=proposal_id,
    )
    try:
        response = await book_revision_proposals.apply_proposal(
            db,
            proposal=proposal,
            preview=preview,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.commit()
    return response

@router.post(
    "/projects/{project_id}/book-review/revision-proposals/{proposal_id}/dismiss",
    response_model=MemoirBookRevisionProposalResponse,
)
async def dismiss_memoir_book_revision_proposal(
    project_id: str,
    proposal_id: str,
    preview: Literal["none", "before", "after", "both"] = "both",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await load_memoir_project(
        db,
        project_id,
        current_user.id,
        allow_admin=is_admin_user(current_user),
    )
    proposal = await book_revision_proposals.load_proposal(
        db,
        project_id=project_id,
        proposal_id=proposal_id,
    )
    try:
        response = await book_revision_proposals.dismiss_proposal(
            db,
            proposal=proposal,
            preview=preview,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.commit()
    return response
