from fastapi import APIRouter, Body, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.users import User
from app.schemas.memoir_narrative import (
    MemoirBookCompletionOverviewResponse,
    MemoirChapterAIRequest,
    MemoirChapterDraftSectionAIRevisionRequest,
    MemoirChapterPreparationJobRequest,
    MemoirChapterPreparationJobResponse,
    MemoirChapterWorkspaceResponse,
)
from app.services.memoir.narrative import book_completion
from app.services.memoir import chapter_preparation_jobs, completion_read_model
from app.services.memoir.project_ai_preferences import select_project_ai_model

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
    "/projects/{project_id}/book-completion/chapters/{chapter_id}/jobs",
    response_model=MemoirChapterPreparationJobResponse,
    status_code=202,
)
async def enqueue_memoir_chapter_preparation_job(
    project_id: str,
    chapter_id: str,
    req: MemoirChapterPreparationJobRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return await chapter_preparation_jobs.enqueue_chapter_preparation_job(
            db,
            user_id=current_user.id,
            project_id=project_id,
            chapter_id=chapter_id,
            req=req,
        )
    except ValueError as exc:
        raise decision_error(exc) from exc


@router.get(
    "/projects/{project_id}/book-completion/chapters/{chapter_id}/jobs/current",
    response_model=MemoirChapterPreparationJobResponse,
)
async def get_memoir_chapter_preparation_job(
    project_id: str,
    chapter_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await chapter_preparation_jobs.get_chapter_preparation_job(
        db,
        user_id=current_user.id,
        project_id=project_id,
        chapter_id=chapter_id,
    )


@router.delete(
    "/projects/{project_id}/book-completion/chapters/{chapter_id}/jobs/{job_id}",
    response_model=MemoirChapterPreparationJobResponse,
)
async def cancel_memoir_chapter_preparation_job(
    project_id: str,
    chapter_id: str,
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return await chapter_preparation_jobs.cancel_chapter_preparation_job(
            db,
            user_id=current_user.id,
            project_id=project_id,
            chapter_id=chapter_id,
            job_id=job_id,
        )
    except ValueError as exc:
        raise decision_error(exc) from exc


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
    selected_ai_model = select_project_ai_model(project, req.ai_model)
    # Persist the model preference before provider calls so this request does
    # not hold SQLite's write lock throughout multi-step chapter preparation.
    await db.commit()
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
    selected_ai_model = select_project_ai_model(project, req.ai_model)
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
    selected_ai_model = select_project_ai_model(project, req.ai_model)
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
