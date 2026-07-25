from fastapi import APIRouter, Body, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.users import User
from app.schemas.memoir_narrative import (
    MemoirBookCompletionOverviewResponse,
    MemoirChapterAIRequest,
    MemoirChapterDraftSectionAIRevisionRequest,
    MemoirChapterWorkspaceResponse,
)
from app.services.memoir.narrative import book_completion
from app.services.memoir import completion_read_model
from app.services.memoir.project_ai_preferences import (
    project_ai_model,
    remember_project_ai_model,
)

from .common import authorize_project, decision_error


router = APIRouter()


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
        return await completion_read_model.chapter_workspace(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
        )
    except ValueError as exc:
        raise decision_error(exc) from exc


@router.get(
    "/projects/{project_id}/book-completion/overview",
    response_model=MemoirBookCompletionOverviewResponse,
)
async def get_memoir_book_completion_overview(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    return await book_completion.get_book_completion_overview(db, project_id=project_id)


@router.post(
    "/projects/{project_id}/book-completion/chapters/{chapter_id}/prepare",
    response_model=MemoirChapterWorkspaceResponse,
)
async def prepare_memoir_book_completion_chapter(
    project_id: str,
    chapter_id: str,
    req: MemoirChapterAIRequest = Body(
        default=MemoirChapterAIRequest(),
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = await authorize_project(
        db,
        project_id=project_id,
        current_user=current_user,
    )
    requested_ai_model = (req.ai_model or "").strip()
    selected_ai_model = requested_ai_model or project_ai_model(project)
    remember_project_ai_model(project, requested_ai_model)
    try:
        workspace = await book_completion.prepare_chapter(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
            user_id=current_user.id,
            ai_model=selected_ai_model,
        )
        await db.commit()
        return workspace
    except ValueError as exc:
        await db.rollback()
        raise decision_error(exc) from exc


@router.post(
    "/projects/{project_id}/book-completion/chapters/{chapter_id}/rewrite",
    response_model=MemoirChapterWorkspaceResponse,
)
async def rewrite_memoir_book_completion_chapter(
    project_id: str,
    chapter_id: str,
    req: MemoirChapterAIRequest = Body(
        default=MemoirChapterAIRequest(),
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = await authorize_project(
        db,
        project_id=project_id,
        current_user=current_user,
    )
    requested_ai_model = (req.ai_model or "").strip()
    selected_ai_model = requested_ai_model or project_ai_model(project)
    remember_project_ai_model(project, requested_ai_model)
    try:
        workspace = await book_completion.rewrite_chapter(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
            user_id=current_user.id,
            ai_model=selected_ai_model,
        )
        await db.commit()
        return workspace
    except ValueError as exc:
        await db.rollback()
        raise decision_error(exc) from exc


@router.post(
    "/projects/{project_id}/chapters/{chapter_id}/draft-sections/{section_id}/ai-revise",
    response_model=MemoirChapterWorkspaceResponse,
)
async def revise_memoir_chapter_draft_section_with_ai(
    project_id: str,
    chapter_id: str,
    section_id: str,
    req: MemoirChapterDraftSectionAIRevisionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = await authorize_project(
        db,
        project_id=project_id,
        current_user=current_user,
    )
    requested_ai_model = (req.ai_model or "").strip()
    selected_ai_model = requested_ai_model or project_ai_model(project)
    remember_project_ai_model(project, requested_ai_model)
    try:
        workspace = await book_completion.revise_chapter_section_with_ai(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
            section_id=section_id,
            expected_version_id=req.expected_version_id,
            instruction_text=req.instruction_text,
            user_id=current_user.id,
            ai_model=selected_ai_model,
        )
        await db.commit()
        return workspace
    except ValueError as exc:
        await db.rollback()
        raise decision_error(exc) from exc
