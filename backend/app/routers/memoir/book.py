from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.users import User
from app.services.file_exports import content_disposition
from app.schemas.memoir_book import (
    MemoirBookAudioRequest,
    MemoirBookAudioResponse,
    MemoirBookChapterDraftResponse,
    MemoirBookChapterDraftRevisionRequest,
    MemoirBookOutputChapterEditorialCopyUpdateRequest,
    MemoirBookOutputEditorialCopyRegenerateRequest,
    MemoirBookChapterTitleGenerateRequest,
    MemoirBookChapterTitleUpdateRequest,
    MemoirBookOutputResponse,
    MemoirBookOutputUpdateRequest,
    MemoirBookOutputVersionResponse,
    MemoirBookPlanConfirmRequest,
    MemoirBookPlanGenerateRequest,
    MemoirBookPlanResponse,
    MemoirBookPlanStructureCommandRequest,
    MemoirBookPlanUpdateRequest,
    MemoirBookTitleSuggestionRequest,
    MemoirBookTitleSuggestionResponse,
    MemoirAutomationEventResponse,
)
from app.schemas.memoir_images import (
    MemoirBookImagePlacementCreateRequest,
    MemoirBookImagePlacementUpdateRequest,
    MemoirImageAssetListResponse,
    MemoirImageAssetResponse,
    MemoirImageAssetUpdateRequest,
)
from app.services.memoir import book_drafts as memoir_book_drafts
from app.services.memoir import book_generation_jobs as memoir_book_generation_jobs
from app.services.memoir import book_outputs as memoir_book_outputs
from app.services.memoir import book_plans as memoir_book_plans
from app.services.memoir import book_plan_write_workflow as memoir_book_plan_write_workflow
from app.services.memoir import image_assets as memoir_image_assets


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
    return await memoir_book_plan_write_workflow.generate_book_plan(
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
    return await memoir_book_plan_write_workflow.confirm_book_plan(
        db,
        user_id=current_user.id,
        project_id=project_id,
        req=req,
    )


@router.patch("/projects/{project_id}/book-plan", response_model=MemoirBookPlanResponse)
async def update_mobile_memoir_book_plan(
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
    )


@router.post("/projects/{project_id}/book-plan/structure-commands", response_model=MemoirBookPlanResponse)
async def apply_mobile_memoir_book_plan_structure_command(
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
    )


@router.post("/projects/{project_id}/book-plan/outline/tts", response_model=MemoirBookAudioResponse)
async def create_mobile_memoir_book_plan_outline_tts(
    project_id: str,
    req: MemoirBookAudioRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_book_plans.synthesize_book_outline_audio(
        db,
        user_id=current_user.id,
        project_id=project_id,
        req=req,
    )


@router.get("/projects/{project_id}/image-assets", response_model=MemoirImageAssetListResponse)
async def list_mobile_memoir_image_assets(
    project_id: str,
    include_deleted: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_image_assets.list_image_assets(
        db,
        user_id=current_user.id,
        project_id=project_id,
        include_deleted=include_deleted,
    )


@router.patch("/projects/{project_id}/image-assets/{asset_id}", response_model=MemoirImageAssetResponse)
async def update_mobile_memoir_image_asset(
    project_id: str,
    asset_id: str,
    req: MemoirImageAssetUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_image_assets.update_image_asset(
        db,
        user_id=current_user.id,
        project_id=project_id,
        asset_id=asset_id,
        req=req,
    )


@router.delete("/projects/{project_id}/image-assets/{asset_id}", status_code=204)
async def delete_mobile_memoir_image_asset(
    project_id: str,
    asset_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await memoir_image_assets.delete_image_asset(
        db,
        user_id=current_user.id,
        project_id=project_id,
        asset_id=asset_id,
    )
    return Response(status_code=204)


@router.get("/projects/{project_id}/image-assets/{asset_id}/file")
async def get_mobile_memoir_image_asset_file(
    project_id: str,
    asset_id: str,
    variant: str = "",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    asset, path = await memoir_image_assets.load_image_asset_file(
        db,
        user_id=current_user.id,
        project_id=project_id,
        asset_id=asset_id,
        thumbnail=variant == "thumbnail",
        branded=variant != "thumbnail",
    )
    return FileResponse(
        path,
        media_type=asset.mime_type or "image/jpeg",
        filename=f"{asset.id}{Path(path).suffix}",
    )


@router.post(
    "/projects/{project_id}/image-assets/{asset_id}/placements",
    response_model=MemoirImageAssetResponse,
    status_code=201,
)
async def create_mobile_memoir_image_placement(
    project_id: str,
    asset_id: str,
    req: MemoirBookImagePlacementCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_image_assets.create_image_placement(
        db,
        user_id=current_user.id,
        project_id=project_id,
        asset_id=asset_id,
        req=req,
    )


@router.patch(
    "/projects/{project_id}/image-assets/{asset_id}/placements/{placement_id}",
    response_model=MemoirImageAssetResponse,
)
async def update_mobile_memoir_image_placement(
    project_id: str,
    asset_id: str,
    placement_id: str,
    req: MemoirBookImagePlacementUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_image_assets.update_image_placement(
        db,
        user_id=current_user.id,
        project_id=project_id,
        asset_id=asset_id,
        placement_id=placement_id,
        req=req,
    )


@router.delete(
    "/projects/{project_id}/image-assets/{asset_id}/placements/{placement_id}",
    response_model=MemoirImageAssetResponse,
)
async def delete_mobile_memoir_image_placement(
    project_id: str,
    asset_id: str,
    placement_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_image_assets.delete_image_placement(
        db,
        user_id=current_user.id,
        project_id=project_id,
        asset_id=asset_id,
        placement_id=placement_id,
    )


@router.patch(
    "/projects/{project_id}/book-plan/chapters/{chapter_id}/title",
    response_model=MemoirBookPlanResponse,
)
async def update_mobile_memoir_book_chapter_title(
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
    )


@router.post(
    "/projects/{project_id}/book-plan/chapters/{chapter_id}/title/generate",
    response_model=MemoirBookPlanResponse,
)
async def generate_mobile_memoir_book_chapter_title(
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
    "/projects/{project_id}/book-plan/chapters/{chapter_id}/draft/revise",
    response_model=MemoirBookChapterDraftResponse,
)
async def revise_mobile_memoir_book_chapter_draft(
    project_id: str,
    chapter_id: str,
    req: MemoirBookChapterDraftRevisionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_book_drafts.revise_chapter_draft(
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


@router.post(
    "/projects/{project_id}/book-plan/chapters/{chapter_id}/draft/tts",
    response_model=MemoirBookAudioResponse,
)
async def create_mobile_memoir_book_chapter_tts(
    project_id: str,
    chapter_id: str,
    req: MemoirBookAudioRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_book_drafts.synthesize_chapter_draft_audio(
        db,
        user_id=current_user.id,
        project_id=project_id,
        chapter_id=chapter_id,
        req=req,
    )


@router.post(
    "/projects/{project_id}/book-plan/chapters/{chapter_id}/draft/sections/{section_id}/tts",
    response_model=MemoirBookAudioResponse,
)
async def create_mobile_memoir_book_chapter_section_tts(
    project_id: str,
    chapter_id: str,
    section_id: str,
    req: MemoirBookAudioRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_book_drafts.synthesize_chapter_draft_section_audio(
        db,
        user_id=current_user.id,
        project_id=project_id,
        chapter_id=chapter_id,
        section_id=section_id,
        req=req,
    )


@router.get("/projects/{project_id}/book-output", response_model=MemoirBookOutputResponse)
async def get_mobile_memoir_book_output(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_book_outputs.get_book_output(
        db,
        user_id=current_user.id,
        project_id=project_id,
    )


@router.get(
    "/projects/{project_id}/book-output/versions/{version_id}",
    response_model=MemoirBookOutputVersionResponse,
)
async def get_mobile_memoir_book_output_version(
    project_id: str,
    version_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_book_outputs.get_book_output_version(
        db,
        user_id=current_user.id,
        project_id=project_id,
        version_id=version_id,
    )


@router.patch("/projects/{project_id}/book-output", response_model=MemoirBookOutputResponse)
async def update_mobile_memoir_book_output(
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
    )


@router.post(
    "/projects/{project_id}/book-output/editorial-copy/regenerate",
    response_model=MemoirBookOutputResponse,
)
async def regenerate_mobile_memoir_book_output_editorial_copy(
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
    )


@router.post(
    "/projects/{project_id}/book-output/editorial-copy/regenerate/jobs",
    response_model=MemoirAutomationEventResponse,
)
async def enqueue_mobile_memoir_book_output_editorial_copy_generation(
    project_id: str,
    req: MemoirBookOutputEditorialCopyRegenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_book_generation_jobs.enqueue_book_editorial_copy_generation_job(
        db,
        user_id=current_user.id,
        project_id=project_id,
        req=req,
    )


@router.patch(
    "/projects/{project_id}/book-output/chapters/{chapter_id}/editorial-copy",
    response_model=MemoirBookOutputResponse,
)
async def update_mobile_memoir_book_output_chapter_editorial_copy(
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
    )


@router.post(
    "/projects/{project_id}/book-output/title-suggestions",
    response_model=MemoirBookTitleSuggestionResponse,
)
async def suggest_mobile_memoir_book_output_titles(
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
    )


@router.get("/projects/{project_id}/book-output/export")
async def export_mobile_memoir_book_output(
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
    )
    return Response(
        content=export_file.content,
        media_type=export_file.media_type,
        headers={"Content-Disposition": content_disposition(export_file.filename)},
    )


@router.post("/projects/{project_id}/book-output/tts", response_model=MemoirBookAudioResponse)
async def create_mobile_memoir_book_output_tts(
    project_id: str,
    req: MemoirBookAudioRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_book_outputs.synthesize_book_output_audio(
        db,
        user_id=current_user.id,
        project_id=project_id,
        req=req,
    )
