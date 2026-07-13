from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.domain.memoir.narrative_types import (
    MemoirNarrativeEntityType,
    MemoirNarrativeEntityVerificationStatus,
)
from app.models.users import User
from app.schemas.memoir_narrative import (
    MemoirNarrativeDecisionRequest,
    MemoirNarrativeDecisionResponse,
    MemoirNarrativeEntityMergeRequest,
    MemoirNarrativeEntityResponse,
)
from app.services.memoir.narrative import decisions, queries

from .common import authorize_project, decision_error


router = APIRouter()


@router.get(
    "/projects/{project_id}/entities",
    response_model=list[MemoirNarrativeEntityResponse],
)
async def list_memoir_narrative_entities(
    project_id: str,
    entity_type: MemoirNarrativeEntityType | None = None,
    verification_status: MemoirNarrativeEntityVerificationStatus | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    return await queries.list_entities(
        db,
        project_id=project_id,
        entity_type=entity_type,
        verification_status=verification_status,
        limit=limit,
        offset=offset,
    )


async def _decide_verification(
    *,
    db: AsyncSession,
    project_id: str,
    entity_id: str,
    req: MemoirNarrativeDecisionRequest,
    current_user: User,
    confirmed: bool,
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        decision = await decisions.decide_entity_verification(
            db,
            project_id=project_id,
            entity_id=entity_id,
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
    "/projects/{project_id}/entities/{entity_id}/confirm",
    response_model=MemoirNarrativeDecisionResponse,
)
async def confirm_memoir_narrative_entity(
    project_id: str,
    entity_id: str,
    req: MemoirNarrativeDecisionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await _decide_verification(
        db=db,
        project_id=project_id,
        entity_id=entity_id,
        req=req,
        current_user=current_user,
        confirmed=True,
    )


@router.post(
    "/projects/{project_id}/entities/{entity_id}/reject",
    response_model=MemoirNarrativeDecisionResponse,
)
async def reject_memoir_narrative_entity(
    project_id: str,
    entity_id: str,
    req: MemoirNarrativeDecisionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await _decide_verification(
        db=db,
        project_id=project_id,
        entity_id=entity_id,
        req=req,
        current_user=current_user,
        confirmed=False,
    )


@router.post(
    "/projects/{project_id}/entities/{entity_id}/merge",
    response_model=MemoirNarrativeDecisionResponse,
)
async def merge_memoir_narrative_entity(
    project_id: str,
    entity_id: str,
    req: MemoirNarrativeEntityMergeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        decision = await decisions.merge_entities(
            db,
            project_id=project_id,
            source_entity_id=entity_id,
            retained_entity_id=req.retained_entity_id,
            actor_user_id=current_user.id,
            idempotency_key=req.idempotency_key,
            reason=req.reason,
        )
        await db.commit()
        return decision
    except ValueError as exc:
        await db.rollback()
        raise decision_error(exc) from exc
