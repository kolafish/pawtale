from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.memoir import MemoirEditingJob
from app.domain.memoir.types import MemoirEditingJobType
from app.models.users import User
from app.schemas.memoir_narrative import (
    MemoirBookConsistencyAuditJobResponse,
    MemoirBookConsistencyAuditRequest,
    MemoirBookFormalizeRequest,
    MemoirBookFormalizeResponse,
    MemoirBookReadinessResponse,
)
from app.services.memoir.narrative import book_consistency_audit, book_readiness

from .common import authorize_project, decision_error


router = APIRouter()


@router.post(
    "/projects/{project_id}/book-consistency-audits",
    response_model=MemoirBookConsistencyAuditJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def audit_memoir_book_consistency(
    project_id: str,
    req: MemoirBookConsistencyAuditRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        job = await book_consistency_audit.enqueue_book_consistency_audit(
            db,
            project_id=project_id,
            include_semantic_audit=req.include_semantic_audit,
            ai_model=req.ai_model,
        )
        await db.commit()
        await db.refresh(job)
        return MemoirBookConsistencyAuditJobResponse.model_validate(job)
    except (ValueError, IntegrityError) as exc:
        await db.rollback()
        raise decision_error(exc) from exc


@router.get(
    "/projects/{project_id}/book-consistency-audits/{job_id}",
    response_model=MemoirBookConsistencyAuditJobResponse,
)
async def get_memoir_book_consistency_audit(
    project_id: str,
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    job = await db.scalar(
        select(MemoirEditingJob).where(
            MemoirEditingJob.project_id == project_id,
            MemoirEditingJob.id == job_id,
            MemoirEditingJob.job_type == MemoirEditingJobType.AUDIT_BOOK_CONSISTENCY.value,
        )
    )
    if job is None:
        raise HTTPException(status_code=404, detail="book consistency audit not found")
    return MemoirBookConsistencyAuditJobResponse.model_validate(job)


@router.get(
    "/projects/{project_id}/book-readiness",
    response_model=MemoirBookReadinessResponse,
)
async def get_memoir_book_readiness(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    return await book_readiness.get_book_readiness(db, project_id=project_id)


@router.post(
    "/projects/{project_id}/book-output/formalize",
    response_model=MemoirBookFormalizeResponse,
)
async def formalize_memoir_book_output(
    project_id: str,
    req: MemoirBookFormalizeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        response = await book_readiness.formalize_book_output(
            db,
            project_id=project_id,
            actor_user_id=current_user.id,
            audit_job_id=req.audit_job_id,
            output_version_id=req.output_version_id,
        )
        await db.commit()
        return response
    except (ValueError, IntegrityError) as exc:
        await db.rollback()
        raise decision_error(exc) from exc
