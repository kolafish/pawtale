from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.domain.memoir.narrative_types import (
    MemoirClaimPublicationStatus,
    MemoirClaimVerificationStatus,
)
from app.models.users import User
from app.schemas.memoir_narrative import (
    MemoirClaimResponse,
    MemoirClaimSummaryResponse,
    MemoirNarrativeDecisionRequest,
    MemoirNarrativeDecisionResponse,
)
from app.services.memoir.narrative import decisions, queries

from .common import authorize_project, decision_error


router = APIRouter()


@router.get(
    "/projects/{project_id}/claims",
    response_model=list[MemoirClaimSummaryResponse],
)
async def list_memoir_claims(
    project_id: str,
    verification_status: MemoirClaimVerificationStatus | None = None,
    publication_status: MemoirClaimPublicationStatus | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    return await queries.list_claims(
        db,
        project_id=project_id,
        verification_status=verification_status,
        publication_status=publication_status,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/projects/{project_id}/claims/{claim_id}",
    response_model=MemoirClaimResponse,
)
async def get_memoir_claim(
    project_id: str,
    claim_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    claim = await queries.get_claim(db, project_id=project_id, claim_id=claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail="claim does not belong to the project")
    return claim


async def _decide_verification(
    *,
    db: AsyncSession,
    project_id: str,
    claim_id: str,
    req: MemoirNarrativeDecisionRequest,
    current_user: User,
    confirmed: bool,
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        decision = await decisions.decide_claim_verification(
            db,
            project_id=project_id,
            claim_id=claim_id,
            confirmed=confirmed,
            actor_user_id=current_user.id,
            idempotency_key=req.idempotency_key,
            reason=req.reason,
        )
        await db.commit()
        return decision
    except ValueError as exc:
        await db.rollback()
        raise decision_error(exc) from exc


@router.post(
    "/projects/{project_id}/claims/{claim_id}/confirm",
    response_model=MemoirNarrativeDecisionResponse,
)
async def confirm_memoir_claim(
    project_id: str,
    claim_id: str,
    req: MemoirNarrativeDecisionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await _decide_verification(
        db=db,
        project_id=project_id,
        claim_id=claim_id,
        req=req,
        current_user=current_user,
        confirmed=True,
    )


@router.post(
    "/projects/{project_id}/claims/{claim_id}/reject",
    response_model=MemoirNarrativeDecisionResponse,
)
async def reject_memoir_claim(
    project_id: str,
    claim_id: str,
    req: MemoirNarrativeDecisionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await _decide_verification(
        db=db,
        project_id=project_id,
        claim_id=claim_id,
        req=req,
        current_user=current_user,
        confirmed=False,
    )


async def _decide_publication(
    *,
    db: AsyncSession,
    project_id: str,
    claim_id: str,
    req: MemoirNarrativeDecisionRequest,
    current_user: User,
    publication_status: MemoirClaimPublicationStatus,
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        decision = await decisions.decide_claim_publication(
            db,
            project_id=project_id,
            claim_id=claim_id,
            publication_status=publication_status,
            actor_user_id=current_user.id,
            idempotency_key=req.idempotency_key,
            reason=req.reason,
        )
        await db.commit()
        return decision
    except ValueError as exc:
        await db.rollback()
        raise decision_error(exc) from exc


@router.post(
    "/projects/{project_id}/claims/{claim_id}/mark-private",
    response_model=MemoirNarrativeDecisionResponse,
)
async def mark_memoir_claim_private(
    project_id: str,
    claim_id: str,
    req: MemoirNarrativeDecisionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await _decide_publication(
        db=db,
        project_id=project_id,
        claim_id=claim_id,
        req=req,
        current_user=current_user,
        publication_status=MemoirClaimPublicationStatus.PRIVATE,
    )


@router.post(
    "/projects/{project_id}/claims/{claim_id}/exclude",
    response_model=MemoirNarrativeDecisionResponse,
)
async def exclude_memoir_claim(
    project_id: str,
    claim_id: str,
    req: MemoirNarrativeDecisionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await _decide_publication(
        db=db,
        project_id=project_id,
        claim_id=claim_id,
        req=req,
        current_user=current_user,
        publication_status=MemoirClaimPublicationStatus.EXCLUDED,
    )
