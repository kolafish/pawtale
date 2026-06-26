from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.users import User
from app.schemas.memoir_book import (
    MemoirBookAudioRequest,
    MemoirBookAudioResponse,
    MemoirBookChapterDraftGenerateRequest,
    MemoirBookChapterDraftResponse,
    MemoirBookChapterDraftUpdateRequest,
    MemoirBookChapterTitleGenerateRequest,
    MemoirBookChapterTitleUpdateRequest,
    MemoirBookOutputGenerateRequest,
    MemoirBookOutputResponse,
    MemoirBookPlanConfirmRequest,
    MemoirBookPlanGenerateRequest,
    MemoirBookPlanResponse,
    MemoirBookPlanStructureCommandRequest,
    MemoirBookPlanUpdateRequest,
    MemoirBookQualityAuditRunRequest,
    MemoirBookQualityAuditRunResponse,
    MemoirBookSourceAssignmentRequest,
    MemoirBookSourceAutoAssignmentRunRequest,
    MemoirBookSourceAutoAssignmentRunResponse,
    MemoirBookSourceCoverageResponse,
    MemoirBookSourceMoveRequest,
    MemoirBookSourceReviewConfirmRequest,
    MemoirBookSourceReviewConfirmResponse,
    MemoirBookSourceReviewCreateRequest,
    MemoirBookSourceReviewItemResponse,
    MemoirBookSourceReviewListResponse,
    MemoirBookSourceRouteRequest,
    MemoirBookSourceRouteResponse,
    MemoirBookVoiceAssistantTTSRequest,
    MemoirBookVoiceCommandExecuteRequest,
    MemoirBookVoiceCommandExecuteResponse,
    MemoirBookVoiceCommandParseRequest,
    MemoirBookVoiceCommandParseResponse,
)
from app.services.memoir import book_drafts as memoir_book_drafts
from app.services.memoir import book_outputs as memoir_book_outputs
from app.services.memoir import book_plans as memoir_book_plans
from app.services.memoir import book_quality_audit as memoir_book_quality_audit
from app.services.memoir import source_auto_assignment as memoir_source_auto_assignment
from app.services.memoir import source_review_queue as memoir_source_review_queue
from app.services.memoir import source_router as memoir_source_router
from app.services.memoir import book_voice_commands as memoir_book_voice_commands


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


@router.patch("/projects/{project_id}/book-plan", response_model=MemoirBookPlanResponse)
async def update_mobile_memoir_book_plan(
    project_id: str,
    req: MemoirBookPlanUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_book_plans.update_book_plan(
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
    return await memoir_book_plans.apply_book_plan_structure_command(
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


@router.post(
    "/projects/{project_id}/book-voice-command/parse",
    response_model=MemoirBookVoiceCommandParseResponse,
)
async def parse_mobile_memoir_book_voice_command(
    project_id: str,
    req: MemoirBookVoiceCommandParseRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_book_voice_commands.parse_book_voice_command(
        db,
        user_id=current_user.id,
        project_id=project_id,
        req=req,
    )


@router.post(
    "/projects/{project_id}/book-voice-command/execute",
    response_model=MemoirBookVoiceCommandExecuteResponse,
)
async def execute_mobile_memoir_book_voice_command(
    project_id: str,
    req: MemoirBookVoiceCommandExecuteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_book_voice_commands.execute_book_voice_command(
        db,
        user_id=current_user.id,
        project_id=project_id,
        req=req,
    )


@router.post(
    "/projects/{project_id}/book-voice-assistant/tts",
    response_model=MemoirBookAudioResponse,
)
async def create_mobile_memoir_book_voice_assistant_tts(
    project_id: str,
    req: MemoirBookVoiceAssistantTTSRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_book_voice_commands.synthesize_book_voice_assistant_audio(
        db,
        user_id=current_user.id,
        project_id=project_id,
        req=req,
    )


@router.get("/projects/{project_id}/book-source-coverage", response_model=MemoirBookSourceCoverageResponse)
async def get_mobile_memoir_book_source_coverage(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_book_plans.get_book_source_coverage(
        db,
        user_id=current_user.id,
        project_id=project_id,
    )


@router.post("/projects/{project_id}/book-source-routing/dry-run", response_model=MemoirBookSourceRouteResponse)
async def dry_run_mobile_memoir_book_source_routing(
    project_id: str,
    req: MemoirBookSourceRouteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_source_router.route_book_source(
        db,
        user_id=current_user.id,
        project_id=project_id,
        utterance_id=req.utterance_id,
    )


@router.get("/projects/{project_id}/book-source-review-items", response_model=MemoirBookSourceReviewListResponse)
async def list_mobile_memoir_book_source_review_items(
    project_id: str,
    include_resolved: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_source_review_queue.list_source_review_items(
        db,
        user_id=current_user.id,
        project_id=project_id,
        include_resolved=include_resolved,
    )


@router.post(
    "/projects/{project_id}/book-source-review-items",
    response_model=MemoirBookSourceReviewItemResponse,
    status_code=201,
)
async def create_mobile_memoir_book_source_review_item(
    project_id: str,
    req: MemoirBookSourceReviewCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_source_review_queue.create_source_review_item(
        db,
        user_id=current_user.id,
        project_id=project_id,
        utterance_id=req.utterance_id,
    )


@router.post(
    "/projects/{project_id}/book-source-review-items/{event_id}/confirm",
    response_model=MemoirBookSourceReviewConfirmResponse,
)
async def confirm_mobile_memoir_book_source_review_item(
    project_id: str,
    event_id: str,
    req: MemoirBookSourceReviewConfirmRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_source_review_queue.confirm_source_review_item(
        db,
        user_id=current_user.id,
        project_id=project_id,
        event_id=event_id,
        req=req,
    )


@router.post(
    "/projects/{project_id}/book-source-review-items/{event_id}/ignore",
    response_model=MemoirBookSourceReviewItemResponse,
)
async def ignore_mobile_memoir_book_source_review_item(
    project_id: str,
    event_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_source_review_queue.ignore_source_review_item(
        db,
        user_id=current_user.id,
        project_id=project_id,
        event_id=event_id,
    )


@router.post("/projects/{project_id}/book-source-assignments", response_model=MemoirBookPlanResponse)
async def assign_mobile_memoir_book_sources(
    project_id: str,
    req: MemoirBookSourceAssignmentRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_book_plans.assign_book_sources_to_chapters(
        db,
        user_id=current_user.id,
        project_id=project_id,
        req=req,
    )


@router.post(
    "/projects/{project_id}/book-source-auto-assignments/run",
    response_model=MemoirBookSourceAutoAssignmentRunResponse,
)
async def run_mobile_memoir_book_source_auto_assignments(
    project_id: str,
    req: MemoirBookSourceAutoAssignmentRunRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_source_auto_assignment.run_source_auto_assignment(
        db,
        user_id=current_user.id,
        project_id=project_id,
        req=req,
    )


@router.post(
    "/projects/{project_id}/book-quality-audit/run",
    response_model=MemoirBookQualityAuditRunResponse,
)
async def run_mobile_memoir_book_quality_audit(
    project_id: str,
    req: MemoirBookQualityAuditRunRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_book_quality_audit.run_book_quality_audit(
        db,
        user_id=current_user.id,
        project_id=project_id,
        req=req,
    )


@router.post("/projects/{project_id}/book-source-moves", response_model=MemoirBookPlanResponse)
async def move_mobile_memoir_book_source(
    project_id: str,
    req: MemoirBookSourceMoveRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_book_plans.move_book_source_between_chapters(
        db,
        user_id=current_user.id,
        project_id=project_id,
        req=req,
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
    return await memoir_book_plans.update_book_chapter_title(
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
    return await memoir_book_plans.generate_book_chapter_title(
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


@router.post("/projects/{project_id}/book-output/generate", response_model=MemoirBookOutputResponse)
async def generate_mobile_memoir_book_output(
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
