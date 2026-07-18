from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, is_admin_user
from app.database import get_db
from app.models.users import User
from app.schemas.memoir_sections import (
    MemoirSectionCollectionResponse,
    MemoirSectionCreateRequest,
    MemoirSectionMaterialAssignmentRequest,
    MemoirSectionMaterialMoveRequest,
    MemoirSectionMergeRequest,
    MemoirSectionReorderRequest,
    MemoirSectionSplitRequest,
    MemoirSectionUpdateRequest,
)
from app.services.memoir import sections as memoir_sections


router = APIRouter(tags=["memoir-sections"])


@router.get(
    "/projects/{project_id}/sections",
    response_model=MemoirSectionCollectionResponse,
)
async def list_memoir_sections(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_sections.list_sections(
        db,
        user_id=current_user.id,
        project_id=project_id,
        allow_admin=is_admin_user(current_user),
    )


@router.post(
    "/projects/{project_id}/sections/bootstrap",
    response_model=MemoirSectionCollectionResponse,
)
async def bootstrap_memoir_sections(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_sections.bootstrap_sections(
        db,
        user_id=current_user.id,
        project_id=project_id,
        allow_admin=is_admin_user(current_user),
    )


@router.post(
    "/projects/{project_id}/chapters/{chapter_id}/sections",
    response_model=MemoirSectionCollectionResponse,
)
async def create_memoir_section(
    project_id: str,
    chapter_id: str,
    req: MemoirSectionCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_sections.create_section(
        db,
        user_id=current_user.id,
        project_id=project_id,
        chapter_id=chapter_id,
        req=req,
        allow_admin=is_admin_user(current_user),
    )


@router.patch(
    "/projects/{project_id}/sections/{section_id}",
    response_model=MemoirSectionCollectionResponse,
)
async def update_memoir_section(
    project_id: str,
    section_id: str,
    req: MemoirSectionUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_sections.update_section(
        db,
        user_id=current_user.id,
        project_id=project_id,
        section_id=section_id,
        req=req,
        allow_admin=is_admin_user(current_user),
    )


@router.put(
    "/projects/{project_id}/chapters/{chapter_id}/sections/order",
    response_model=MemoirSectionCollectionResponse,
)
async def reorder_memoir_sections(
    project_id: str,
    chapter_id: str,
    req: MemoirSectionReorderRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_sections.reorder_sections(
        db,
        user_id=current_user.id,
        project_id=project_id,
        chapter_id=chapter_id,
        req=req,
        allow_admin=is_admin_user(current_user),
    )


@router.post(
    "/projects/{project_id}/sections/{section_id}/materials",
    response_model=MemoirSectionCollectionResponse,
)
async def assign_memoir_section_materials(
    project_id: str,
    section_id: str,
    req: MemoirSectionMaterialAssignmentRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_sections.assign_materials(
        db,
        user_id=current_user.id,
        project_id=project_id,
        section_id=section_id,
        req=req,
        allow_admin=is_admin_user(current_user),
    )


@router.post(
    "/projects/{project_id}/section-materials/{utterance_id}/move",
    response_model=MemoirSectionCollectionResponse,
)
async def move_memoir_section_material(
    project_id: str,
    utterance_id: str,
    req: MemoirSectionMaterialMoveRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_sections.move_material(
        db,
        user_id=current_user.id,
        project_id=project_id,
        utterance_id=utterance_id,
        req=req,
        allow_admin=is_admin_user(current_user),
    )


@router.post(
    "/projects/{project_id}/sections/{target_section_id}/merge",
    response_model=MemoirSectionCollectionResponse,
)
async def merge_memoir_sections(
    project_id: str,
    target_section_id: str,
    req: MemoirSectionMergeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_sections.merge_sections(
        db,
        user_id=current_user.id,
        project_id=project_id,
        target_section_id=target_section_id,
        req=req,
        allow_admin=is_admin_user(current_user),
    )


@router.post(
    "/projects/{project_id}/sections/{source_section_id}/split",
    response_model=MemoirSectionCollectionResponse,
)
async def split_memoir_section(
    project_id: str,
    source_section_id: str,
    req: MemoirSectionSplitRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_sections.split_section(
        db,
        user_id=current_user.id,
        project_id=project_id,
        source_section_id=source_section_id,
        req=req,
        allow_admin=is_admin_user(current_user),
    )
