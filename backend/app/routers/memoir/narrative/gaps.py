from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.domain.memoir.narrative_types import MemoirNarrativeGapStatus
from app.models.users import User
from app.schemas.memoir_narrative import (
    MemoirChapterGapAuditJobResponse,
    MemoirChapterGapAuditRequest,
    MemoirChapterWorkspaceResponse,
    MemoirNarrativeGapDismissRequest,
    MemoirNarrativeGapListResponse,
    MemoirNarrativeGapResolutionRequest,
    MemoirNarrativeGapResponse,
)
from app.services.memoir.narrative import chapter_workspace, gap_audit, gaps

from .common import authorize_project, decision_error


router = APIRouter()


async def _raise_mutation_error(
    db: AsyncSession,
    exc: ValueError | IntegrityError,
) -> NoReturn:
    await db.rollback()
    if isinstance(exc, ValueError):
        raise decision_error(exc) from exc
    raise HTTPException(
        status_code=409,
        detail="gap state changed concurrently; reload and retry",
    ) from exc


@router.get(
    "/projects/{project_id}/chapters/{chapter_id}/workspace",
    response_model=MemoirChapterWorkspaceResponse,
)
async def get_memoir_chapter_workspace(
    project_id: str,
    chapter_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        return await chapter_workspace.get_chapter_workspace(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
        )
    except ValueError as exc:
        raise decision_error(exc) from exc


@router.post(
    "/projects/{project_id}/chapters/{chapter_id}/workspace/audits",
    response_model=MemoirChapterGapAuditJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def audit_memoir_chapter_workspace(
    project_id: str,
    chapter_id: str,
    req: MemoirChapterGapAuditRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        job = await gap_audit.enqueue_chapter_gap_audit(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
            include_semantic_audit=req.include_semantic_audit,
            ai_model=req.ai_model,
        )
        await db.commit()
        await db.refresh(job)
        return MemoirChapterGapAuditJobResponse.model_validate(job)
    except (ValueError, IntegrityError) as exc:
        await _raise_mutation_error(db, exc)


@router.get(
    "/projects/{project_id}/gaps",
    response_model=MemoirNarrativeGapListResponse,
)
async def list_memoir_narrative_gaps(
    project_id: str,
    chapter_id: str | None = None,
    gap_status: MemoirNarrativeGapStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    items, total = await gaps.list_gaps(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        status=gap_status,
        limit=limit,
        offset=offset,
    )
    return MemoirNarrativeGapListResponse(
        items=[await gaps.gap_response(db, gap=item) for item in items],
        total=total,
    )


async def _resolve(
    *,
    db: AsyncSession,
    project_id: str,
    gap_id: str,
    resolution_status: MemoirNarrativeGapStatus,
    req: MemoirNarrativeGapResolutionRequest,
    current_user: User,
) -> MemoirNarrativeGapResponse:
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        gap = await gaps.resolve_gap(
            db,
            project_id=project_id,
            gap_id=gap_id,
            status=resolution_status,
            actor_user_id=current_user.id,
            idempotency_key=req.idempotency_key,
            note=req.note,
        )
        await db.commit()
        gap = await gaps.get_gap(db, project_id=project_id, gap_id=gap.id)
        if gap is None:
            raise ValueError("gap does not belong to the project")
        return await gaps.gap_response(db, gap=gap)
    except (ValueError, IntegrityError) as exc:
        await _raise_mutation_error(db, exc)


@router.post(
    "/projects/{project_id}/gaps/{gap_id}/resolve",
    response_model=MemoirNarrativeGapResponse,
)
async def resolve_memoir_narrative_gap(
    project_id: str,
    gap_id: str,
    req: MemoirNarrativeGapResolutionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await _resolve(
        db=db,
        project_id=project_id,
        gap_id=gap_id,
        resolution_status=MemoirNarrativeGapStatus.RESOLVED,
        req=req,
        current_user=current_user,
    )


@router.post(
    "/projects/{project_id}/gaps/{gap_id}/accept-incomplete",
    response_model=MemoirNarrativeGapResponse,
)
async def accept_incomplete_memoir_narrative_gap(
    project_id: str,
    gap_id: str,
    req: MemoirNarrativeGapResolutionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await _resolve(
        db=db,
        project_id=project_id,
        gap_id=gap_id,
        resolution_status=MemoirNarrativeGapStatus.ACCEPTED_INCOMPLETE,
        req=req,
        current_user=current_user,
    )


@router.post(
    "/projects/{project_id}/gaps/{gap_id}/not-applicable",
    response_model=MemoirNarrativeGapResponse,
)
async def mark_memoir_narrative_gap_not_applicable(
    project_id: str,
    gap_id: str,
    req: MemoirNarrativeGapResolutionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await _resolve(
        db=db,
        project_id=project_id,
        gap_id=gap_id,
        resolution_status=MemoirNarrativeGapStatus.NOT_APPLICABLE,
        req=req,
        current_user=current_user,
    )


@router.post(
    "/projects/{project_id}/gaps/{gap_id}/dismiss",
    response_model=MemoirNarrativeGapResponse,
)
async def dismiss_memoir_narrative_gap(
    project_id: str,
    gap_id: str,
    req: MemoirNarrativeGapDismissRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await _resolve(
        db=db,
        project_id=project_id,
        gap_id=gap_id,
        resolution_status=MemoirNarrativeGapStatus.DISMISSED,
        req=req,
        current_user=current_user,
    )
