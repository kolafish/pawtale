from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, is_admin_user
from app.database import get_db
from app.models.users import User
from app.schemas.memoir_section_digests import (
    MemoirSectionDigestGenerateRequest,
    MemoirSectionDigestResponse,
)
from app.services.memoir.access import load_memoir_project
from app.services.memoir import section_digests


router = APIRouter(tags=["memoir-section-digests"])


@router.get(
    "/projects/{project_id}/sections/{section_id}/digest",
    response_model=MemoirSectionDigestResponse,
)
async def get_memoir_section_digest(
    project_id: str,
    section_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await load_memoir_project(
        db,
        project_id,
        current_user.id,
        allow_admin=is_admin_user(current_user),
    )
    digest = await section_digests.get_current_section_digest(
        db,
        project_id=project_id,
        section_id=section_id,
    )
    if digest is None:
        raise HTTPException(status_code=404, detail="小章节摘要尚未生成或已经过期")
    return section_digests.digest_response(digest)


@router.post(
    "/projects/{project_id}/sections/{section_id}/digest/generate",
    response_model=MemoirSectionDigestResponse,
)
async def generate_memoir_section_digest(
    project_id: str,
    section_id: str,
    req: MemoirSectionDigestGenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await load_memoir_project(
        db,
        project_id,
        current_user.id,
        allow_admin=is_admin_user(current_user),
    )
    digest = await section_digests.generate_or_reuse_section_digest(
        db,
        project_id=project_id,
        section_id=section_id,
        ai_model=req.ai_model,
        force_regenerate=req.force_regenerate,
    )
    await db.commit()
    return section_digests.digest_response(digest)
