from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, is_admin_user
from app.database import get_db
from app.models.users import User
from app.schemas.memoir_book import (
    MemoirBookChapterDraftGenerateRequest,
    MemoirBookChapterDraftResponse,
    MemoirBookChapterDraftUpdateRequest,
    MemoirBookChapterTitleGenerateRequest,
    MemoirBookChapterTitleUpdateRequest,
    MemoirBookOutputChapterEditorialCopyUpdateRequest,
    MemoirBookOutputEditorialCopyRegenerateRequest,
    MemoirBookOutputGenerateRequest,
    MemoirBookOutputResponse,
    MemoirBookOutputUpdateRequest,
    MemoirBookPlanConfirmRequest,
    MemoirBookPlanGenerateRequest,
    MemoirBookPlanResponse,
    MemoirBookPlanStructureCommandRequest,
    MemoirBookPlanUpdateRequest,
    MemoirBookTitleSuggestionRequest,
    MemoirBookTitleSuggestionResponse,
)
from app.schemas.memoir_book_editor import MemoirBookEditorResponse
from app.services.file_exports import content_disposition
from app.services.memoir import book_drafts as memoir_book_drafts
from app.services.memoir import book_editor as memoir_book_editor
from app.services.memoir import book_outputs as memoir_book_outputs
from app.services.memoir import book_plan_write_workflow as memoir_book_plan_write_workflow


router = APIRouter(tags=["memoir-book-editor"])


@router.get("/projects/{project_id}/book-editor", response_model=MemoirBookEditorResponse)
async def get_memoir_book_editor(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_book_editor.get_book_editor(
        db,
        user_id=current_user.id,
        project_id=project_id,
        allow_admin=is_admin_user(current_user),
    )


@router.post("/projects/{project_id}/book-editor/plan/generate", response_model=MemoirBookPlanResponse)
async def generate_memoir_book_editor_plan(
    project_id: str,
    req: MemoirBookPlanGenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_book_plan_write_workflow.generate_book_plan(
        db,
        user_id=current_user.id,
        project_id=project_id,
        req=req,
        allow_admin=is_admin_user(current_user),
    )


@router.put("/projects/{project_id}/book-editor/plan", response_model=MemoirBookPlanResponse)
async def confirm_memoir_book_editor_plan(
    project_id: str,
    req: MemoirBookPlanConfirmRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_book_plan_write_workflow.confirm_book_plan(
        db,
        user_id=current_user.id,
        project_id=project_id,
        req=req,
        allow_admin=is_admin_user(current_user),
    )


@router.patch("/projects/{project_id}/book-editor/plan", response_model=MemoirBookPlanResponse)
async def update_memoir_book_editor_plan(
    project_id: str,
    req: MemoirBookPlanUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_book_plan_write_workflow.update_book_plan(
        db,
        user_id=current_user.id,
        project_id=project_id,
        req=req,
        allow_admin=is_admin_user(current_user),
    )


@router.post(
    "/projects/{project_id}/book-editor/plan/structure-commands",
    response_model=MemoirBookPlanResponse,
)
async def apply_memoir_book_editor_plan_structure_command(
    project_id: str,
    req: MemoirBookPlanStructureCommandRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_book_plan_write_workflow.apply_book_plan_structure_command(
        db,
        user_id=current_user.id,
        project_id=project_id,
        req=req,
        allow_admin=is_admin_user(current_user),
    )


@router.patch(
    "/projects/{project_id}/book-editor/chapters/{chapter_id}/title",
    response_model=MemoirBookPlanResponse,
)
async def update_memoir_book_editor_chapter_title(
    project_id: str,
    chapter_id: str,
    req: MemoirBookChapterTitleUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_book_plan_write_workflow.update_book_chapter_title(
        db,
        user_id=current_user.id,
        project_id=project_id,
        chapter_id=chapter_id,
        req=req,
        allow_admin=is_admin_user(current_user),
    )


@router.post(
    "/projects/{project_id}/book-editor/chapters/{chapter_id}/title/generate",
    response_model=MemoirBookPlanResponse,
)
async def generate_memoir_book_editor_chapter_title(
    project_id: str,
    chapter_id: str,
    req: MemoirBookChapterTitleGenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_book_plan_write_workflow.generate_book_chapter_title(
        db,
        user_id=current_user.id,
        project_id=project_id,
        chapter_id=chapter_id,
        req=req,
        allow_admin=is_admin_user(current_user),
    )


@router.get(
    "/projects/{project_id}/book-editor/chapters/{chapter_id}/draft",
    response_model=MemoirBookChapterDraftResponse,
)
async def get_memoir_book_editor_chapter_draft(
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
        allow_admin=is_admin_user(current_user),
    )


@router.post(
    "/projects/{project_id}/book-editor/chapters/{chapter_id}/draft/generate",
    response_model=MemoirBookChapterDraftResponse,
)
async def generate_memoir_book_editor_chapter_draft(
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
        allow_admin=is_admin_user(current_user),
    )


@router.patch(
    "/projects/{project_id}/book-editor/chapters/{chapter_id}/draft",
    response_model=MemoirBookChapterDraftResponse,
)
async def update_memoir_book_editor_chapter_draft(
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
        allow_admin=is_admin_user(current_user),
    )


@router.post(
    "/projects/{project_id}/book-editor/chapters/{chapter_id}/draft/confirm",
    response_model=MemoirBookChapterDraftResponse,
)
async def confirm_memoir_book_editor_chapter_draft(
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
        allow_admin=is_admin_user(current_user),
    )


@router.post("/projects/{project_id}/book-editor/output/generate", response_model=MemoirBookOutputResponse)
async def generate_memoir_book_editor_output(
    project_id: str,
    req: MemoirBookOutputGenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_book_outputs.generate_book_output(
        db,
        user_id=current_user.id,
        project_id=project_id,
        req=req,
        allow_admin=is_admin_user(current_user),
    )


@router.patch("/projects/{project_id}/book-editor/output", response_model=MemoirBookOutputResponse)
async def update_memoir_book_editor_output(
    project_id: str,
    req: MemoirBookOutputUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_book_outputs.update_book_output(
        db,
        user_id=current_user.id,
        project_id=project_id,
        req=req,
        allow_admin=is_admin_user(current_user),
    )


@router.post(
    "/projects/{project_id}/book-editor/output/editorial-copy/regenerate",
    response_model=MemoirBookOutputResponse,
)
async def regenerate_memoir_book_editor_output_copy(
    project_id: str,
    req: MemoirBookOutputEditorialCopyRegenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_book_outputs.regenerate_book_output_editorial_copy(
        db,
        user_id=current_user.id,
        project_id=project_id,
        req=req,
        allow_admin=is_admin_user(current_user),
    )


@router.patch(
    "/projects/{project_id}/book-editor/output/chapters/{chapter_id}/editorial-copy",
    response_model=MemoirBookOutputResponse,
)
async def update_memoir_book_editor_output_chapter_copy(
    project_id: str,
    chapter_id: str,
    req: MemoirBookOutputChapterEditorialCopyUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_book_outputs.update_book_output_chapter_editorial_copy(
        db,
        user_id=current_user.id,
        project_id=project_id,
        chapter_id=chapter_id,
        req=req,
        allow_admin=is_admin_user(current_user),
    )


@router.post(
    "/projects/{project_id}/book-editor/output/title-suggestions",
    response_model=MemoirBookTitleSuggestionResponse,
)
async def suggest_memoir_book_editor_output_titles(
    project_id: str,
    req: MemoirBookTitleSuggestionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_book_outputs.suggest_book_output_titles(
        db,
        user_id=current_user.id,
        project_id=project_id,
        req=req,
        allow_admin=is_admin_user(current_user),
    )


@router.get("/projects/{project_id}/book-editor/output/export")
async def export_memoir_book_editor_output(
    project_id: str,
    export_format: memoir_book_outputs.MemoirBookOutputExportFormat = Query(
        memoir_book_outputs.MemoirBookOutputExportFormat.PDF,
        alias="format",
    ),
    layout: memoir_book_outputs.MemoirBookOutputExportLayout = Query(
        memoir_book_outputs.MemoirBookOutputExportLayout.STANDARD
    ),
    version_id: str | None = None,
    show_source_appendix: bool | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    export_file = await memoir_book_outputs.export_book_output_file(
        db,
        user_id=current_user.id,
        project_id=project_id,
        export_format=export_format,
        layout=layout,
        version_id=version_id,
        show_source_appendix=show_source_appendix,
        allow_admin=is_admin_user(current_user),
    )
    return Response(
        content=export_file.content,
        media_type=export_file.media_type,
        headers={"Content-Disposition": content_disposition(export_file.filename)},
    )
