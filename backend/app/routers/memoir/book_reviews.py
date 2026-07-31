import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, WebSocket
from sqlalchemy.ext.asyncio import AsyncSession

from app.application_errors import ApplicationError
from app.auth import get_current_user, is_admin_user
from app.database import get_db
from app.domain.memoir.types import MemoirBookRevisionProposalStatus
from app.models.memoir import MemoirBookReview
from app.models.users import User
from app.routers.websocket_auth import authenticate_websocket, reject_websocket
from app.schemas.memoir_book_review import (
    MemoirBookEditorialRevisionJobResponse,
    MemoirBookEditorialRevisionRequest,
    MemoirBookReviewAnswerApplicationJobResponse,
    MemoirBookReviewAnswerApplyRequest,
    MemoirBookReviewGenerateRequest,
    MemoirBookReviewJobResponse,
    MemoirBookReviewResponse,
    MemoirBookRevisionFeedbackJobResponse,
    MemoirBookRevisionDraftJobResponse,
    MemoirBookRevisionDraftRequest,
    MemoirBookRevisionFeedbackRequest,
    MemoirBookRevisionProposalResponse,
)
from app.services.asr.capacity import (
    VoiceGatewayCapacityError,
    voice_gateway_capacity_manager,
)
from app.services.asr.factory import create_realtime_volc_streaming_asr_client
from app.services.asr.gateway import VoiceGatewaySession
from app.services.memoir.access import load_memoir_project
from app.services.memoir import (
    book_review_jobs,
    book_revision_jobs,
    book_revision_proposals,
    book_reviews,
)
from app.services.memoir.asr_text_normalization import normalize_memoir_asr_text
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
        return await book_revision_jobs.enqueue_book_revision_job(
            db,
            user_id=current_user.id,
            project_id=project_id,
            source="review_answers",
            ai_model=req.ai_model,
            workflow_mode=req.workflow_mode,
            allow_admin=is_admin_user(current_user),
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
    return await book_revision_jobs.get_book_revision_job(
        db,
        user_id=current_user.id,
        project_id=project_id,
        source="review_answers",
        allow_admin=is_admin_user(current_user),
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
        return await book_revision_jobs.cancel_book_revision_job(
            db,
            user_id=current_user.id,
            project_id=project_id,
            source="review_answers",
            job_id=job_id,
            allow_admin=is_admin_user(current_user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/projects/{project_id}/book-review/editorial-revision/jobs",
    response_model=MemoirBookEditorialRevisionJobResponse,
    status_code=202,
)
async def enqueue_memoir_book_editorial_revision_job(
    project_id: str,
    req: MemoirBookEditorialRevisionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return await book_revision_jobs.enqueue_book_revision_job(
            db,
            user_id=current_user.id,
            project_id=project_id,
            source="editorial_actions",
            ai_model=req.ai_model,
            action_ids=req.action_ids,
            workflow_mode=req.workflow_mode,
            allow_admin=is_admin_user(current_user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/projects/{project_id}/book-review/editorial-revision/jobs/current",
    response_model=MemoirBookEditorialRevisionJobResponse,
)
async def get_current_memoir_book_editorial_revision_job(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await book_revision_jobs.get_book_revision_job(
        db,
        user_id=current_user.id,
        project_id=project_id,
        source="editorial_actions",
        allow_admin=is_admin_user(current_user),
    )


@router.delete(
    "/projects/{project_id}/book-review/editorial-revision/jobs/{job_id}",
    response_model=MemoirBookEditorialRevisionJobResponse,
)
async def cancel_memoir_book_editorial_revision_job(
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
            source="editorial_actions",
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


@router.websocket("/projects/{project_id}/book-review/revision-proposals/{proposal_id}/dictation")
async def stream_memoir_book_revision_dictation(
    websocket: WebSocket,
    project_id: str,
    proposal_id: str,
    db: AsyncSession = Depends(get_db),
):
    current_user = await authenticate_websocket(websocket, db)
    if current_user is None:
        await reject_websocket(websocket, "voice_gateway_auth_failed")
        return
    if not await _book_revision_dictation_is_available(
        db,
        project_id=project_id,
        proposal_id=proposal_id,
        current_user=current_user,
    ):
        await reject_websocket(websocket, "book_revision_dictation_unavailable")
        return

    connection_id = str(uuid.uuid4())
    capacity_session_id = f"book-revision-dictation:{connection_id}"
    try:
        capacity_lease = await voice_gateway_capacity_manager.acquire(
            current_user.id,
            capacity_session_id,
        )
    except VoiceGatewayCapacityError as exc:
        await reject_websocket(websocket, exc.code)
        return

    try:
        await websocket.accept()
        session = VoiceGatewaySession(
            client=websocket,
            asr_client_factory=lambda: create_realtime_volc_streaming_asr_client(
                current_user.id,
                capacity_session_id,
            ),
            text_normalizer=normalize_memoir_asr_text,
            connection_id=connection_id,
            diagnostics_context={
                "gateway": "book_revision_dictation",
                "project_id": project_id,
                "proposal_id": proposal_id,
            },
        )
        await session.run()
    finally:
        await capacity_lease.release()


async def _book_revision_dictation_is_available(
    db: AsyncSession,
    *,
    project_id: str,
    proposal_id: str,
    current_user: User,
) -> bool:
    try:
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
        response = await book_revision_proposals.proposal_response(
            db,
            proposal=proposal,
            preview="none",
        )
    except (ApplicationError, HTTPException):
        return False
    return response.status == MemoirBookRevisionProposalStatus.READY and response.can_dismiss
