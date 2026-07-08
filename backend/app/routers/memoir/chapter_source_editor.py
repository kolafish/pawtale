from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.users import User
from app.schemas.memoir_chapter_source_editor import (
    MemoirChapterSourceEditorAssignmentRequest,
    MemoirChapterSourceEditorMoveRequest,
    MemoirChapterSourceEditorRemoveRequest,
    MemoirChapterSourceEditorResponse,
)
from app.services.memoir import chapter_source_editor as memoir_chapter_source_editor


router = APIRouter(tags=["memoir-chapter-source-editor"])


@router.get(
    "/projects/{project_id}/chapter-source-editor",
    response_model=MemoirChapterSourceEditorResponse,
)
async def get_memoir_chapter_source_editor(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_chapter_source_editor.get_chapter_source_editor(
        db,
        user_id=current_user.id,
        project_id=project_id,
    )


@router.post(
    "/projects/{project_id}/chapter-source-editor/assignments",
    response_model=MemoirChapterSourceEditorResponse,
)
async def assign_memoir_chapter_sources(
    project_id: str,
    req: MemoirChapterSourceEditorAssignmentRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_chapter_source_editor.assign_chapter_sources(
        db,
        user_id=current_user.id,
        project_id=project_id,
        req=req,
    )


@router.post(
    "/projects/{project_id}/chapter-source-editor/moves",
    response_model=MemoirChapterSourceEditorResponse,
)
async def move_memoir_chapter_source(
    project_id: str,
    req: MemoirChapterSourceEditorMoveRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_chapter_source_editor.move_chapter_source(
        db,
        user_id=current_user.id,
        project_id=project_id,
        req=req,
    )


@router.post(
    "/projects/{project_id}/chapter-source-editor/removals",
    response_model=MemoirChapterSourceEditorResponse,
)
async def remove_memoir_chapter_sources(
    project_id: str,
    req: MemoirChapterSourceEditorRemoveRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_chapter_source_editor.remove_chapter_sources(
        db,
        user_id=current_user.id,
        project_id=project_id,
        req=req,
    )
