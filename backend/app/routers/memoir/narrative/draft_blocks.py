from typing import Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.users import User
from app.schemas.memoir_narrative import (
    MemoirDraftBlockListResponse,
    MemoirDraftBlockManualRevisionRequest,
    MemoirDraftBlockResponse,
    MemoirDraftBlockUnlockRequest,
)
from app.services.memoir.narrative import (
    draft_block_queries,
    draft_blocks,
)

from .common import authorize_project, decision_error


router = APIRouter()


async def _commit_block_response(
    db: AsyncSession,
    *,
    project_id: str,
    block_id: str,
) -> MemoirDraftBlockResponse:
    await db.commit()
    return await draft_block_queries.block_response(
        db,
        project_id=project_id,
        block_id=block_id,
    )


async def _handle_mutation(
    db: AsyncSession,
    *,
    project_id: str,
    block_id: str,
    operation: Callable[[], Awaitable[object]],
) -> MemoirDraftBlockResponse:
    try:
        await operation()
        return await _commit_block_response(
            db,
            project_id=project_id,
            block_id=block_id,
        )
    except ValueError as exc:
        await db.rollback()
        raise decision_error(exc) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="draft block changed concurrently; reload and retry",
        ) from exc


@router.get(
    "/projects/{project_id}/chapters/{chapter_id}/draft-blocks",
    response_model=MemoirDraftBlockListResponse,
)
async def get_memoir_chapter_draft_blocks(
    project_id: str,
    chapter_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        return await draft_block_queries.list_chapter_blocks(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
        )
    except ValueError as exc:
        raise decision_error(exc) from exc


@router.post(
    "/projects/{project_id}/draft-blocks/{block_id}/manual-revisions",
    response_model=MemoirDraftBlockResponse,
)
async def manually_edit_memoir_draft_block(
    project_id: str,
    block_id: str,
    req: MemoirDraftBlockManualRevisionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    return await _handle_mutation(
        db,
        project_id=project_id,
        block_id=block_id,
        operation=lambda: draft_blocks.manually_edit_block(
            db,
            project_id=project_id,
            block_id=block_id,
            expected_current_version_id=req.expected_current_version_id,
            content=req.content,
            feedback=req.feedback,
        ),
    )


@router.post(
    "/projects/{project_id}/draft-blocks/{block_id}/unlock",
    response_model=MemoirDraftBlockResponse,
)
async def unlock_memoir_draft_block(
    project_id: str,
    block_id: str,
    req: MemoirDraftBlockUnlockRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    return await _handle_mutation(
        db,
        project_id=project_id,
        block_id=block_id,
        operation=lambda: draft_blocks.unlock_block(
            db,
            project_id=project_id,
            block_id=block_id,
            expected_current_version_id=req.expected_current_version_id,
            actor_user_id=current_user.id,
            idempotency_key=req.idempotency_key,
            reason=req.reason,
        ),
    )
