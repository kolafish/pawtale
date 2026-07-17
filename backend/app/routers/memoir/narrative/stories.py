from uuid import NAMESPACE_URL, uuid5

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.domain.memoir.narrative_types import (
    MemoirNarrativeActorType,
    MemoirStoryLifecycleStatus,
    MemoirStoryPublicationStatus,
    MemoirStoryType,
    MemoirStoryVerificationStatus,
)
from app.models.users import User
from app.repositories.memoir_narrative import MemoirStoryRepository
from app.schemas.memoir_narrative import (
    MemoirNarrativeDecisionRequest,
    MemoirNarrativeDecisionResponse,
    MemoirStoryClaimsAttachRequest,
    MemoirStoryCreate,
    MemoirStoryCreateRequest,
    MemoirStoryMergeRequest,
    MemoirStoryResponse,
    MemoirStorySummaryResponse,
)
from app.services.memoir.narrative import stories, story_decisions, story_merging

from .common import authorize_project, decision_error


router = APIRouter()


@router.get("/projects/{project_id}/stories", response_model=list[MemoirStorySummaryResponse])
async def list_memoir_stories(
    project_id: str,
    story_type: MemoirStoryType | None = None,
    lifecycle_status: MemoirStoryLifecycleStatus | None = None,
    verification_status: MemoirStoryVerificationStatus | None = None,
    publication_status: MemoirStoryPublicationStatus | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    return await stories.list_story_responses(
        db,
        project_id=project_id,
        story_type=story_type,
        lifecycle_status=lifecycle_status,
        verification_status=verification_status,
        publication_status=publication_status,
        limit=limit,
        offset=offset,
    )


@router.post("/projects/{project_id}/stories", response_model=MemoirStoryResponse)
async def create_memoir_story(
    project_id: str,
    req: MemoirStoryCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    story_id = str(
        uuid5(
            NAMESPACE_URL,
            f"pawtale:user-story:{project_id}:{current_user.id}:{req.idempotency_key}",
        )
    )
    existing = await MemoirStoryRepository(db).get(project_id=project_id, story_id=story_id)
    if existing is not None:
        return await stories.story_response(db, story=existing)
    try:
        async with db.begin_nested():
            story = await stories.create_story(
                db,
                story_id=story_id,
                data=MemoirStoryCreate(
                    project_id=project_id,
                    title=req.title,
                    story_type=req.story_type,
                    summary=req.summary,
                    time_start=req.time_start,
                    time_end=req.time_end,
                    time_precision=req.time_precision,
                    primary_place_entity_id=req.primary_place_entity_id,
                    created_by=MemoirNarrativeActorType.USER,
                    created_by_user_id=current_user.id,
                ),
                reason=req.reason,
            )
        await db.commit()
        return await stories.story_response(db, story=story)
    except IntegrityError as exc:
        await db.rollback()
        existing = await MemoirStoryRepository(db).get(
            project_id=project_id,
            story_id=story_id,
        )
        if existing is None:
            raise HTTPException(status_code=409, detail="story could not be created") from exc
        return await stories.story_response(db, story=existing)
    except ValueError as exc:
        await db.rollback()
        raise decision_error(exc) from exc


@router.get("/projects/{project_id}/stories/{story_id}", response_model=MemoirStoryResponse)
async def get_memoir_story(
    project_id: str,
    story_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    response = await stories.get_story_response(db, project_id=project_id, story_id=story_id)
    if response is None:
        raise HTTPException(status_code=404, detail="story not found")
    return response


@router.post(
    "/projects/{project_id}/stories/{story_id}/claims",
    response_model=MemoirStoryResponse,
)
async def attach_memoir_story_claims(
    project_id: str,
    story_id: str,
    req: MemoirStoryClaimsAttachRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    story = await MemoirStoryRepository(db).get(project_id=project_id, story_id=story_id)
    if story is None:
        raise HTTPException(status_code=404, detail="story not found")
    try:
        await stories.attach_claims_to_story(
            db,
            story=story,
            claims=req.claims,
            actor_type=MemoirNarrativeActorType.USER,
            actor_user_id=current_user.id,
            reason=req.reason,
        )
        await db.commit()
        return await stories.story_response(db, story=story)
    except ValueError as exc:
        await db.rollback()
        raise decision_error(exc) from exc


async def _decide_story_verification(
    *,
    db: AsyncSession,
    project_id: str,
    story_id: str,
    req: MemoirNarrativeDecisionRequest,
    current_user: User,
    confirmed: bool,
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        decision = await story_decisions.decide_story_verification(
            db,
            project_id=project_id,
            story_id=story_id,
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
    "/projects/{project_id}/stories/{story_id}/confirm",
    response_model=MemoirNarrativeDecisionResponse,
)
async def confirm_memoir_story(
    project_id: str,
    story_id: str,
    req: MemoirNarrativeDecisionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await _decide_story_verification(
        db=db,
        project_id=project_id,
        story_id=story_id,
        req=req,
        current_user=current_user,
        confirmed=True,
    )


@router.post(
    "/projects/{project_id}/stories/{story_id}/reject",
    response_model=MemoirNarrativeDecisionResponse,
)
async def reject_memoir_story(
    project_id: str,
    story_id: str,
    req: MemoirNarrativeDecisionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await _decide_story_verification(
        db=db,
        project_id=project_id,
        story_id=story_id,
        req=req,
        current_user=current_user,
        confirmed=False,
    )


@router.post(
    "/projects/{project_id}/stories/{story_id}/merge",
    response_model=MemoirNarrativeDecisionResponse,
)
async def merge_memoir_story(
    project_id: str,
    story_id: str,
    req: MemoirStoryMergeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        decision = await story_merging.merge_stories(
            db,
            project_id=project_id,
            source_story_id=story_id,
            retained_story_id=req.retained_story_id,
            actor_user_id=current_user.id,
            idempotency_key=req.idempotency_key,
            reason=req.reason,
        )
        await db.commit()
        return decision
    except ValueError as exc:
        await db.rollback()
        raise decision_error(exc) from exc


async def _decide_story_publication(
    *,
    db: AsyncSession,
    project_id: str,
    story_id: str,
    req: MemoirNarrativeDecisionRequest,
    current_user: User,
    status: MemoirStoryPublicationStatus,
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        decision = await story_decisions.decide_story_publication(
            db,
            project_id=project_id,
            story_id=story_id,
            publication_status=status,
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
    "/projects/{project_id}/stories/{story_id}/mark-private",
    response_model=MemoirNarrativeDecisionResponse,
)
async def mark_memoir_story_private(
    project_id: str,
    story_id: str,
    req: MemoirNarrativeDecisionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await _decide_story_publication(
        db=db,
        project_id=project_id,
        story_id=story_id,
        req=req,
        current_user=current_user,
        status=MemoirStoryPublicationStatus.PRIVATE,
    )


@router.post(
    "/projects/{project_id}/stories/{story_id}/exclude",
    response_model=MemoirNarrativeDecisionResponse,
)
async def exclude_memoir_story(
    project_id: str,
    story_id: str,
    req: MemoirNarrativeDecisionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await _decide_story_publication(
        db=db,
        project_id=project_id,
        story_id=story_id,
        req=req,
        current_user=current_user,
        status=MemoirStoryPublicationStatus.EXCLUDED,
    )
