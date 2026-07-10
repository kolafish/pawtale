from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, is_admin_user
from app.database import get_db
from app.models.users import User
from app.schemas.memoir_material_editor import (
    MemoirMaterialItemResponse,
    MemoirMaterialListResponse,
)
from app.schemas.memoir_voice import MemoirVoiceTranscriptUpdateRequest
from app.services.memoir import material_editor


router = APIRouter(tags=["memoir-material-editor"])


@router.get(
    "/projects/{project_id}/materials",
    response_model=MemoirMaterialListResponse,
)
async def list_memoir_materials(
    project_id: str,
    q: str | None = Query(None, max_length=120),
    correction_status: Literal["all", "corrected", "uncorrected"] = "all",
    voice_session_id: str | None = Query(None, max_length=36),
    sort: Literal["chronological", "recent"] = "chronological",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await material_editor.list_materials(
        db,
        user_id=current_user.id,
        project_id=project_id,
        search_query=q,
        correction_status=correction_status,
        voice_session_id=voice_session_id,
        sort=sort,
        limit=limit,
        offset=offset,
        allow_admin=is_admin_user(current_user),
    )


@router.get(
    "/projects/{project_id}/materials/{utterance_id}",
    response_model=MemoirMaterialItemResponse,
)
async def get_memoir_material(
    project_id: str,
    utterance_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await material_editor.get_material(
        db,
        user_id=current_user.id,
        project_id=project_id,
        utterance_id=utterance_id,
        allow_admin=is_admin_user(current_user),
    )


@router.patch(
    "/projects/{project_id}/materials/{utterance_id}/transcript",
    response_model=MemoirMaterialItemResponse,
)
async def update_memoir_material_transcript(
    project_id: str,
    utterance_id: str,
    req: MemoirVoiceTranscriptUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await material_editor.update_material_transcript(
        db,
        user_id=current_user.id,
        project_id=project_id,
        utterance_id=utterance_id,
        req=req,
        allow_admin=is_admin_user(current_user),
    )
