from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.users import User
from app.schemas.memoir_narrative import (
    MemoirBookDraftBlockAssemblyResponse,
    MemoirChapterDraftBlockAssemblyResponse,
    MemoirDraftBlockGenerationRequest,
    MemoirDraftBlockJobResponse,
    MemoirDraftBlockResponse,
    MemoirDraftBlockRevisionApplyRequest,
    MemoirDraftBlockRevisionJobRequest,
)
from app.services.memoir.narrative import (
    draft_assembly,
    draft_block_queries,
    draft_generation_jobs,
)

from .common import authorize_project, decision_error


router = APIRouter()


@router.post(
    "/projects/{project_id}/chapters/{chapter_id}/draft-blocks/generation-jobs",
    response_model=MemoirDraftBlockJobResponse,
)
async def enqueue_memoir_chapter_draft_block_generation(
    project_id: str,
    chapter_id: str,
    req: MemoirDraftBlockGenerationRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        job = await draft_generation_jobs.enqueue_chapter_block_generation(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
            request=req,
        )
        await db.commit()
        await db.refresh(job)
        return draft_generation_jobs.job_response(job)
    except ValueError as exc:
        await db.rollback()
        raise decision_error(exc) from exc


@router.get(
    "/projects/{project_id}/draft-block-generation-jobs/{job_id}",
    response_model=MemoirDraftBlockJobResponse,
)
async def get_memoir_draft_block_generation_job(
    project_id: str,
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        job = await draft_generation_jobs.load_draft_generation_job(
            db,
            project_id=project_id,
            job_id=job_id,
        )
        return draft_generation_jobs.job_response(job)
    except ValueError as exc:
        raise decision_error(exc) from exc


@router.post(
    "/projects/{project_id}/draft-blocks/{block_id}/revision-jobs",
    response_model=MemoirDraftBlockJobResponse,
)
async def enqueue_memoir_draft_block_revision(
    project_id: str,
    block_id: str,
    req: MemoirDraftBlockRevisionJobRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        job = await draft_generation_jobs.enqueue_block_revision(
            db,
            project_id=project_id,
            block_id=block_id,
            request=req,
        )
        await db.commit()
        await db.refresh(job)
        return draft_generation_jobs.job_response(job)
    except ValueError as exc:
        await db.rollback()
        raise decision_error(exc) from exc


@router.post(
    "/projects/{project_id}/draft-blocks/{block_id}/apply-revision",
    response_model=MemoirDraftBlockResponse,
)
async def apply_memoir_draft_block_revision(
    project_id: str,
    block_id: str,
    req: MemoirDraftBlockRevisionApplyRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        await draft_generation_jobs.apply_generated_revision(
            db,
            project_id=project_id,
            block_id=block_id,
            request=req,
        )
        await db.commit()
        return await draft_block_queries.block_response(
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


@router.post(
    "/projects/{project_id}/chapters/{chapter_id}/draft-blocks/assemble",
    response_model=MemoirChapterDraftBlockAssemblyResponse,
)
async def assemble_memoir_chapter_draft_blocks(
    project_id: str,
    chapter_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        response = await draft_assembly.assemble_chapter_draft(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
        )
        await db.commit()
        return response
    except ValueError as exc:
        await db.rollback()
        raise decision_error(exc) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="chapter draft changed concurrently") from exc


@router.post(
    "/projects/{project_id}/book-output/assemble-from-draft-blocks",
    response_model=MemoirBookDraftBlockAssemblyResponse,
)
async def assemble_memoir_book_output_from_draft_blocks(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        response = await draft_assembly.assemble_book_output(
            db,
            project_id=project_id,
        )
        await db.commit()
        return response
    except ValueError as exc:
        await db.rollback()
        raise decision_error(exc) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="book output changed concurrently") from exc
