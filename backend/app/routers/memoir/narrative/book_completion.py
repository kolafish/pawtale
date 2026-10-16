from typing import Literal

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, is_admin_user
from app.database import get_db
from app.models.users import User
from app.schemas.memoir_narrative import (
    MemoirBookCompletionJobRequest,
    MemoirBookCompletionJobResponse,
    MemoirBookCompletionOverviewResponse,
    MemoirChapterPreparationJobRequest,
    MemoirChapterPreparationJobResponse,
    MemoirChapterSectionRevisionJobRequest,
    MemoirChapterSectionRevisionJobResponse,
    MemoirChapterWorkspaceResponse,
)
from app.schemas.memoir_book_review import MemoirBookRevisionProposalResponse
from app.services.memoir.narrative import book_completion
from app.services.memoir import (
    book_completion_jobs,
    chapter_preparation_jobs,
    completion_read_model,
    section_revision_jobs,
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
    return await book_completion.get_book_completion_overview(
        db,
        project_id=project_id,
    )


@router.post(
    "/projects/{project_id}/book-completion/jobs",
    response_model=MemoirBookCompletionJobResponse,
    status_code=202,
)
async def enqueue_memoir_book_completion_job(
    project_id: str,
    req: MemoirBookCompletionJobRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        return await book_completion_jobs.enqueue_book_completion_job(
            db,
            user_id=current_user.id,
            project_id=project_id,
            req=req,
            allow_admin=is_admin_user(current_user),
        )
    except ValueError as exc:
        raise decision_error(exc) from exc


@router.delete(
    "/projects/{project_id}/book-completion/jobs/{job_id}",
    response_model=MemoirBookCompletionJobResponse,
)
async def cancel_memoir_book_completion_job(
    project_id: str,
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        return await book_completion_jobs.cancel_book_completion_job(
            db,
            user_id=current_user.id,
            project_id=project_id,
            job_id=job_id,
            allow_admin=is_admin_user(current_user),
        )
    except ValueError as exc:
        raise decision_error(exc) from exc


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
        result = await chapter_preparation_jobs.enqueue_chapter_preparation_job(
            db,
            user_id=current_user.id,
            project_id=project_id,
            chapter_id=chapter_id,
            req=req,
        )
        await db.commit()
        return result
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


@router.post(
    "/projects/{project_id}/book-completion/chapters/{chapter_id}/jobs/{job_id}/candidate/accept",
    response_model=MemoirChapterPreparationJobResponse,
)
async def accept_memoir_chapter_candidate(
    project_id: str, chapter_id: str, job_id: str,
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user),
):
    return await chapter_preparation_jobs.accept_chapter_candidate(
        db, user_id=current_user.id, project_id=project_id, chapter_id=chapter_id, job_id=job_id,
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
    "/projects/{project_id}/chapters/{chapter_id}/draft-sections/{section_id}/revision/jobs",
    response_model=MemoirChapterSectionRevisionJobResponse,
    status_code=202,
)
async def enqueue_memoir_chapter_section_revision_job(
    project_id: str,
    chapter_id: str,
    section_id: str,
    req: MemoirChapterSectionRevisionJobRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return await section_revision_jobs.enqueue_section_revision_job(
            db,
            user_id=current_user.id,
            project_id=project_id,
            chapter_id=chapter_id,
            section_id=section_id,
            operation=req.operation,
            proposal_id=req.proposal_id,
            feedback=req.feedback,
            ai_model=req.ai_model,
        )
    except ValueError as exc:
        raise decision_error(exc) from exc


@router.get(
    "/projects/{project_id}/chapters/{chapter_id}/draft-sections/{section_id}/revision/jobs/current",
    response_model=MemoirChapterSectionRevisionJobResponse,
)
async def get_memoir_chapter_section_revision_job(
    project_id: str,
    chapter_id: str,
    section_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await section_revision_jobs.get_section_revision_job(
        db,
        user_id=current_user.id,
        project_id=project_id,
        chapter_id=chapter_id,
        section_id=section_id,
    )


@router.get(
    "/projects/{project_id}/chapters/{chapter_id}/draft-sections/{section_id}/revision/proposals/{proposal_id}",
    response_model=MemoirBookRevisionProposalResponse,
)
async def get_memoir_chapter_section_revision_proposal(
    project_id: str,
    chapter_id: str,
    section_id: str,
    proposal_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        return await section_revision_jobs.section_revision_proposal_response(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
            section_id=section_id,
            proposal_id=proposal_id,
        )
    except ValueError as exc:
        raise decision_error(exc) from exc


@router.post(
    "/projects/{project_id}/chapters/{chapter_id}/draft-sections/{section_id}/revision/proposals/{proposal_id}/{action}",
    response_model=MemoirBookRevisionProposalResponse,
)
async def finalize_memoir_chapter_section_revision(
    project_id: str,
    chapter_id: str,
    section_id: str,
    proposal_id: str,
    action: Literal["apply", "dismiss"],
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return await section_revision_jobs.finalize_section_revision(
            db,
            user_id=current_user.id,
            project_id=project_id,
            chapter_id=chapter_id,
            section_id=section_id,
            proposal_id=proposal_id,
            action=action,
        )
    except ValueError as exc:
        raise decision_error(exc) from exc
