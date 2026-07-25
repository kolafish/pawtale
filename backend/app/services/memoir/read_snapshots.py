from sqlalchemy.ext.asyncio import AsyncSession

from app.models.projects import Project
from app.models.users import User
from app.schemas.memoir_images import MemoirImageAssetListResponse
from app.schemas.memoir_snapshots import (
    MemoirBookSnapshotResponse,
    MemoirHomeSnapshotResponse,
)
from app.schemas.memoir_voice import MemoirAutomationEventListResponse
from app.services.memoir import automation_events
from app.services.memoir import book_drafts
from app.services.memoir import book_outputs
from app.services.memoir import book_plan_sources
from app.services.memoir import image_assets
from app.services.memoir import projects
from app.services.memoir import voice_transcripts
from app.services.memoir.access import load_memoir_project


def _empty_automation_events() -> MemoirAutomationEventListResponse:
    return MemoirAutomationEventListResponse(items=[], total=0, limit=8)


def _empty_image_assets() -> MemoirImageAssetListResponse:
    return MemoirImageAssetListResponse(
        items=[],
        total=0,
        ready_count=0,
        included_count=0,
        unused_count=0,
    )


async def home_snapshot(
    db: AsyncSession,
    user: User,
) -> MemoirHomeSnapshotResponse:
    identity = await projects.memoir_identity_response(db, user)
    if not identity.active_project_id:
        return MemoirHomeSnapshotResponse(
            server_revision=0,
            identity=identity,
        )
    project = await load_memoir_project(
        db,
        identity.active_project_id,
        user.id,
    )
    transcripts = await voice_transcripts.list_voice_transcripts(
        db,
        user.id,
        project_id=project.id,
        limit=1,
    )
    return MemoirHomeSnapshotResponse(
        server_revision=project.memoir_data_revision,
        identity=identity,
        latest_transcript=transcripts.items[0] if transcripts.items else None,
    )


async def book_snapshot(
    db: AsyncSession,
    user: User,
) -> MemoirBookSnapshotResponse:
    identity = await projects.memoir_identity_response(db, user)
    if not identity.active_project_id:
        return MemoirBookSnapshotResponse(
            server_revision=0,
            identity=identity,
            automation_events=_empty_automation_events(),
            image_assets=_empty_image_assets(),
        )

    project: Project = await load_memoir_project(
        db,
        identity.active_project_id,
        user.id,
    )
    plan_model = await book_plan_sources.load_plan_with_chapters(db, project.id)
    plan = (
        await book_plan_sources.plan_response(db, plan_model)
        if plan_model is not None
        else None
    )
    chapter_ids = [chapter.id for chapter in plan_model.chapters] if plan_model else []
    drafts_by_chapter_id = await book_drafts.chapter_draft_responses_by_chapter(
        db,
        project_id=project.id,
        chapter_ids=chapter_ids,
    )
    output = await book_outputs.book_output_response_or_none(
        db,
        user_id=user.id,
        project_id=project.id,
    )
    recent_events = await automation_events.list_automation_events(
        db,
        user_id=user.id,
        project_id=project.id,
        limit=8,
    )
    assets = await image_assets.list_image_assets(
        db,
        user_id=user.id,
        project_id=project.id,
    )
    return MemoirBookSnapshotResponse(
        server_revision=project.memoir_data_revision,
        identity=identity,
        plan=plan,
        drafts_by_chapter_id=drafts_by_chapter_id,
        output=output,
        automation_events=recent_events,
        image_assets=assets,
    )
