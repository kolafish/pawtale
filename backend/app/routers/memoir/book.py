import asyncio
import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, File, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, is_admin_user
from app.database import get_db
from app.models.users import User
from app.services.memoir.book_output_export_cache import PreparedBookExport, has_cached_export, export_cache_path
from app.services.file_exports import content_disposition
from app.schemas.memoir_book import (
    MemoirBookCustomFontScale,
    MemoirBookCustomFontFamily,
    MemoirBookCustomLineSpacing,
    MemoirBookCustomPageMargin,
    MemoirBookManuscriptTemplate,
    MemoirAudioPlanRequest,
    MemoirBookAudioRequest,
    MemoirBookAudioResponse,
    MemoirBookAudioPlanRequest,
    MemoirBookAudioPlanResponse,
    MemoirBookChapterDraftResponse,
    MemoirBookChapterDraftRevisionRequest,
    MemoirBookOutputChapterEditorialCopyUpdateRequest,
    MemoirBookOutputEditorialCopyRegenerateRequest,
    MemoirBookOutputEditorialCopyGenerationJobListResponse,
    MemoirBookChapterTitleGenerateRequest,
    MemoirBookChapterTitleUpdateRequest,
    MemoirBookOutputExportInfo,
    MemoirBookOutputResponse,
    MemoirBookOutputUpdateRequest,
    MemoirBookOutputVersionResponse,
    MemoirBookShareCreateResponse,
    MemoirBookShareReadRequest,
    MemoirBookShareStatusResponse,
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
from app.services.memoir import book_audio_jobs
from app.services.memoir import book_shares as memoir_book_shares
from app.services.memoir import book_plans as memoir_book_plans
from app.services.memoir import book_plan_write_workflow as memoir_book_plan_write_workflow
from app.services.memoir import image_assets as memoir_image_assets


router = APIRouter()


@router.get("/projects/{project_id}/book-output/share", response_model=MemoirBookShareStatusResponse)
async def get_mobile_memoir_book_share_status(
    project_id: str,
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    chapter_id: str | None = None, section_id: str | None = None,
):
    response.headers["Cache-Control"] = "no-store"
    return await memoir_book_shares.share_status(db, user_id=current_user.id, project_id=project_id, chapter_id=chapter_id, section_id=section_id)


@router.post("/projects/{project_id}/book-output/share", response_model=MemoirBookShareCreateResponse)
async def create_mobile_memoir_book_share(
    project_id: str,
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    chapter_id: str | None = None, section_id: str | None = None,
):
    response.headers["Cache-Control"] = "no-store"
    return await memoir_book_shares.create_share(db, user_id=current_user.id, project_id=project_id, chapter_id=chapter_id, section_id=section_id)


@router.put("/projects/{project_id}/book-output/share", response_model=MemoirBookShareCreateResponse)
async def update_mobile_memoir_book_share(
    project_id: str,
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    chapter_id: str | None = None, section_id: str | None = None,
):
    response.headers["Cache-Control"] = "no-store"
    return await memoir_book_shares.update_share(db, user_id=current_user.id, project_id=project_id, chapter_id=chapter_id, section_id=section_id)


@router.delete("/projects/{project_id}/book-output/share", status_code=204)
async def revoke_mobile_memoir_book_share(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    chapter_id: str | None = None, section_id: str | None = None,
):
    await memoir_book_shares.revoke_share(db, user_id=current_user.id, project_id=project_id, chapter_id=chapter_id, section_id=section_id)
    return Response(status_code=204)


@router.post("/book-shares/read", response_model=MemoirBookOutputResponse)
async def read_mobile_memoir_book_share(
    req: MemoirBookShareReadRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store"
    return await memoir_book_shares.read_share(db, req.token)


@router.get("/projects/{project_id}/book-output/share/preview", response_model=MemoirBookOutputResponse)
async def preview_mobile_memoir_book_share(
    project_id: str, response: Response, chapter_id: str | None = None, section_id: str | None = None,
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user),
):
    response.headers["Cache-Control"] = "no-store"
    return await memoir_book_shares.preview_share(db, user_id=current_user.id, project_id=project_id, chapter_id=chapter_id, section_id=section_id)


@router.get("/book-shares/{token}/images/{asset_id}")
async def get_mobile_memoir_shared_image(token: str, asset_id: str, db: AsyncSession = Depends(get_db)):
    asset, path = await memoir_book_shares.shared_image_file(db, token, asset_id)
    return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or asset.mime_type,
                        headers={"Cache-Control": "no-store"})


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
        allow_admin=is_admin_user(current_user),
    )


@router.post("/projects/{project_id}/book-plan/generate", response_model=MemoirBookPlanResponse)
async def generate_mobile_memoir_book_plan(
    project_id: str,
    req: MemoirBookPlanGenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.domain.memoir.types import MemoirBookPlanStatus
    from app.services.memoir.automatic_book_draft import schedule_book_draft
    result = await memoir_book_plan_write_workflow.generate_book_plan(
        db, user_id=current_user.id, project_id=project_id, req=req,
        status=MemoirBookPlanStatus.CONFIRMED,
    )
    await schedule_book_draft(db, project_id=project_id, source="plan_generated")
    await db.commit()
    return result.plan


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


@router.post("/projects/{project_id}/image-assets", response_model=MemoirImageAssetResponse, status_code=201)
async def upload_mobile_memoir_image_asset(
    project_id: str, file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user),
):
    return await memoir_image_assets.upload_image_asset(db, user_id=current_user.id, project_id=project_id, upload=file)


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
        media_type=mimetypes.guess_type(path.name)[0] or asset.mime_type or "image/jpeg",
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
        allow_admin=is_admin_user(current_user),
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
    return await memoir_book_drafts.synthesize_chapter_draft_audio(
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
        allow_admin=is_admin_user(current_user),
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
        allow_admin=is_admin_user(current_user),
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


@router.get(
    "/projects/{project_id}/book-output/editorial-copy/regenerate/jobs",
    response_model=MemoirBookOutputEditorialCopyGenerationJobListResponse,
)
async def list_mobile_memoir_book_output_editorial_copy_generation_jobs(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_book_generation_jobs.list_book_editorial_copy_generation_jobs(
        db,
        user_id=current_user.id,
        project_id=project_id,
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


async def prepare_mobile_memoir_book_export(
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
    preview_template: MemoirBookManuscriptTemplate | None = None,
    preview_font_scale: MemoirBookCustomFontScale | None = None,
    preview_font_family: MemoirBookCustomFontFamily | None = None,
    preview_line_spacing: MemoirBookCustomLineSpacing | None = None,
    preview_page_margin: MemoirBookCustomPageMargin | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PreparedBookExport:
    return await memoir_book_outputs.prepare_book_output_export(
        db,
        user_id=current_user.id,
        project_id=project_id,
        allow_admin=is_admin_user(current_user),
        export_format=export_format,
        layout=layout,
        version_id=version_id,
        show_source_appendix=show_source_appendix,
        preview_template=preview_template,
        preview_custom_layout_patch={
            key: value
            for key, value in (
                ("font_scale", preview_font_scale),
                ("font_family", preview_font_family),
                ("line_spacing", preview_line_spacing),
                ("page_margin", preview_page_margin),
            )
            if value is not None
        },
    )


@router.get("/projects/{project_id}/book-output/export/info", response_model=MemoirBookOutputExportInfo)
async def get_mobile_memoir_book_export_info(
    prepared: PreparedBookExport = Depends(prepare_mobile_memoir_book_export),
):
    cache_key = await asyncio.to_thread(lambda: prepared.cache_key)
    return MemoirBookOutputExportInfo(
        version_id=prepared.version_id, cache_key=cache_key, filename=prepared.filename,
        ready=await asyncio.to_thread(has_cached_export, export_cache_path(prepared, cache_key), prepared.export_format),
    )


@router.get("/projects/{project_id}/book-output/export")
async def export_mobile_memoir_book_output(
    prepared: PreparedBookExport = Depends(prepare_mobile_memoir_book_export),
    cache_key: str | None = None,
):
    if cache_key is not None and cache_key != await asyncio.to_thread(lambda: prepared.cache_key):
        raise HTTPException(status_code=409, detail="书稿导出内容已变化，请重新打开。")
    export_file = await memoir_book_outputs.export_book_output_file(prepared)
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


@router.post("/projects/{project_id}/book-output/tts/plan", response_model=MemoirBookAudioPlanResponse)
async def prepare_mobile_memoir_book_audio(
    project_id: str,
    req: MemoirBookAudioPlanRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await book_audio_jobs.prepare_book_audio(
        db, user_id=current_user.id, project_id=project_id, req=req,
    )


@router.post("/projects/{project_id}/book-plan/outline/tts/plan", response_model=MemoirBookAudioPlanResponse)
async def prepare_mobile_memoir_outline_audio(
    project_id: str,
    req: MemoirAudioPlanRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_book_plans.prepare_book_outline_audio(
        db, user_id=current_user.id, project_id=project_id, req=req,
    )


@router.post("/projects/{project_id}/book-plan/chapters/{chapter_id}/draft/tts/plan", response_model=MemoirBookAudioPlanResponse)
async def prepare_mobile_memoir_chapter_audio(
    project_id: str,
    chapter_id: str,
    req: MemoirAudioPlanRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_book_drafts.prepare_chapter_draft_audio(
        db, user_id=current_user.id, project_id=project_id, req=req,
        chapter_id=chapter_id,
    )


@router.post("/projects/{project_id}/book-plan/chapters/{chapter_id}/draft/sections/{section_id}/tts/plan", response_model=MemoirBookAudioPlanResponse)
async def prepare_mobile_memoir_section_audio(
    project_id: str,
    chapter_id: str,
    section_id: str,
    req: MemoirAudioPlanRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_book_drafts.prepare_chapter_draft_audio(
        db, user_id=current_user.id, project_id=project_id, req=req,
        chapter_id=chapter_id,
        section_id=section_id,
    )
