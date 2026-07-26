from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, is_admin_user
from app.database import get_db
from app.models.projects import Project
from app.models.users import User
from app.schemas.memoir_chapter_pipeline import (
    MemoirChapterDigestGenerateRequest,
    MemoirChapterDigestResponse,
    MemoirLayeredChapterDraftGenerateRequest,
    MemoirLayeredChapterDraftResponse,
)
from app.services.memoir.access import load_memoir_project
from app.services.memoir import chapter_digests, chapter_generation
from app.services.memoir.project_ai_preferences import select_project_ai_model


router = APIRouter(tags=["memoir-chapter-pipeline"])


async def _authorize(
    db: AsyncSession,
    *,
    project_id: str,
    current_user: User,
) -> Project:
    return await load_memoir_project(
        db,
        project_id,
        current_user.id,
        allow_admin=is_admin_user(current_user),
    )


@router.post(
    "/projects/{project_id}/chapters/{chapter_id}/digest/generate",
    response_model=MemoirChapterDigestResponse,
)
async def generate_memoir_chapter_digest(
    project_id: str,
    chapter_id: str,
    req: MemoirChapterDigestGenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = await _authorize(db, project_id=project_id, current_user=current_user)
    digest = await chapter_digests.generate_or_reuse_chapter_digest(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        ai_model=select_project_ai_model(project, req.ai_model),
        force_regenerate=req.force_regenerate,
    )
    await db.commit()
    return MemoirChapterDigestResponse(
        id=digest.id,
        project_id=digest.project_id,
        chapter_id=digest.chapter_id,
        input_signature=digest.input_signature,
        engine=digest.engine,
        status=digest.status,
        digest=digest.digest_json,
        error_message=digest.error_message,
        created_at=digest.created_at,
        updated_at=digest.updated_at,
    )


@router.get(
    "/projects/{project_id}/chapters/{chapter_id}/layered-draft",
    response_model=MemoirLayeredChapterDraftResponse,
)
async def get_memoir_layered_chapter_draft(
    project_id: str,
    chapter_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _authorize(db, project_id=project_id, current_user=current_user)
    draft, version = await chapter_generation.get_chapter_draft(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    return chapter_generation.draft_response(draft, version)


@router.post(
    "/projects/{project_id}/chapters/{chapter_id}/layered-draft/generate",
    response_model=MemoirLayeredChapterDraftResponse,
)
async def generate_memoir_layered_chapter_draft(
    project_id: str,
    chapter_id: str,
    req: MemoirLayeredChapterDraftGenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = await _authorize(db, project_id=project_id, current_user=current_user)
    draft, version = await chapter_generation.generate_and_save_chapter_draft(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        editorial_guidance=req.editorial_guidance,
        ai_model=select_project_ai_model(project, req.ai_model),
    )
    await db.commit()
    return chapter_generation.draft_response(draft, version)


@router.post(
    "/projects/{project_id}/chapters/{chapter_id}/layered-draft/confirm",
    response_model=MemoirLayeredChapterDraftResponse,
)
async def confirm_memoir_layered_chapter_draft(
    project_id: str,
    chapter_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _authorize(db, project_id=project_id, current_user=current_user)
    draft, version = await chapter_generation.confirm_chapter_draft(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    await db.commit()
    return chapter_generation.draft_response(draft, version)
