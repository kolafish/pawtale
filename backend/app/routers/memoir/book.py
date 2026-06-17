from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.users import User
from app.schemas.memoir import (
    MemoirBookChapterDraftGenerateRequest,
    MemoirBookChapterDraftResponse,
    MemoirBookChapterDraftUpdateRequest,
    MemoirBookPlanConfirmRequest,
    MemoirBookPlanGenerateRequest,
    MemoirBookPlanResponse,
)
from app.services.memoir import book_drafts as memoir_book_drafts
from app.services.memoir import book_plans as memoir_book_plans


router = APIRouter()


@router.get("/projects/{project_id}/book-plan", response_model=MemoirBookPlanResponse)
async def get_mobile_memoir_book_plan(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_book_plans.get_book_plan(
        db,
        user_id=current_user.id,
        project_id=project_id,
    )


@router.post("/projects/{project_id}/book-plan/generate", response_model=MemoirBookPlanResponse)
async def generate_mobile_memoir_book_plan(
    project_id: str,
    req: MemoirBookPlanGenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_book_plans.generate_book_plan(
        db,
        user_id=current_user.id,
        project_id=project_id,
        req=req,
    )


@router.put("/projects/{project_id}/book-plan", response_model=MemoirBookPlanResponse)
async def confirm_mobile_memoir_book_plan(
    project_id: str,
    req: MemoirBookPlanConfirmRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_book_plans.confirm_book_plan(
        db,
        user_id=current_user.id,
        project_id=project_id,
        req=req,
    )


@router.get(
    "/projects/{project_id}/book-plan/chapters/{chapter_id}/draft",
    response_model=MemoirBookChapterDraftResponse,
)
async def get_mobile_memoir_book_chapter_draft(
    project_id: str,
    chapter_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_book_drafts.get_chapter_draft(
        db,
        user_id=current_user.id,
        project_id=project_id,
        chapter_id=chapter_id,
    )


@router.post(
    "/projects/{project_id}/book-plan/chapters/{chapter_id}/draft",
    response_model=MemoirBookChapterDraftResponse,
)
async def generate_mobile_memoir_book_chapter_draft(
    project_id: str,
    chapter_id: str,
    req: MemoirBookChapterDraftGenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_book_drafts.generate_chapter_draft(
        db,
        user_id=current_user.id,
        project_id=project_id,
        chapter_id=chapter_id,
        req=req,
    )


@router.patch(
    "/projects/{project_id}/book-plan/chapters/{chapter_id}/draft",
    response_model=MemoirBookChapterDraftResponse,
)
async def update_mobile_memoir_book_chapter_draft(
    project_id: str,
    chapter_id: str,
    req: MemoirBookChapterDraftUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_book_drafts.update_chapter_draft(
        db,
        user_id=current_user.id,
        project_id=project_id,
        chapter_id=chapter_id,
        req=req,
    )


@router.post(
    "/projects/{project_id}/book-plan/chapters/{chapter_id}/draft/confirm",
    response_model=MemoirBookChapterDraftResponse,
)
async def confirm_mobile_memoir_book_chapter_draft(
    project_id: str,
    chapter_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_book_drafts.confirm_chapter_draft(
        db,
        user_id=current_user.id,
        project_id=project_id,
        chapter_id=chapter_id,
    )
