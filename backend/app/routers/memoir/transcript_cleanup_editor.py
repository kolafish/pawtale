from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, is_admin_user
from app.database import get_db
from app.models.users import User
from app.schemas.memoir_interview_editor import (
    MemoirBatchTranscriptCleanupConfirmRequest,
    MemoirBatchTranscriptCleanupConfirmResponse,
    MemoirBatchTranscriptCleanupPreviewRequest,
    MemoirBatchTranscriptCleanupPreviewResponse,
    MemoirDialectGlossaryResponse,
    MemoirDialectGlossaryUpdateRequest,
)
from app.services.memoir import transcript_cleanup_editor


router = APIRouter(tags=["memoir-transcript-cleanup-editor"])


@router.get(
    "/projects/{project_id}/transcript-cleanup/dialect-glossary",
    response_model=MemoirDialectGlossaryResponse,
)
async def get_memoir_dialect_glossary(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await transcript_cleanup_editor.get_dialect_glossary(
        db,
        project_id=project_id,
        user_id=current_user.id,
        allow_admin=is_admin_user(current_user),
    )


@router.put(
    "/projects/{project_id}/transcript-cleanup/dialect-glossary",
    response_model=MemoirDialectGlossaryResponse,
)
async def update_memoir_dialect_glossary(
    project_id: str,
    req: MemoirDialectGlossaryUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await transcript_cleanup_editor.update_dialect_glossary(
        db,
        project_id=project_id,
        user_id=current_user.id,
        req=req,
        allow_admin=is_admin_user(current_user),
    )


@router.post(
    "/projects/{project_id}/transcript-cleanup/preview",
    response_model=MemoirBatchTranscriptCleanupPreviewResponse,
)
async def preview_memoir_batch_transcript_cleanup(
    project_id: str,
    req: MemoirBatchTranscriptCleanupPreviewRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await transcript_cleanup_editor.preview_batch_cleanup(
        db,
        project_id=project_id,
        user_id=current_user.id,
        req=req,
        allow_admin=is_admin_user(current_user),
    )


@router.post(
    "/projects/{project_id}/transcript-cleanup/confirm",
    response_model=MemoirBatchTranscriptCleanupConfirmResponse,
)
async def confirm_memoir_batch_transcript_cleanup(
    project_id: str,
    req: MemoirBatchTranscriptCleanupConfirmRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await transcript_cleanup_editor.confirm_batch_cleanup(
        db,
        project_id=project_id,
        user_id=current_user.id,
        req=req,
        allow_admin=is_admin_user(current_user),
    )
