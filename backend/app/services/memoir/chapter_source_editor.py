from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.memoir_book import (
    MemoirBookPlanResponse,
    MemoirBookSourceAssignmentRequest,
    MemoirBookSourceCoverageItem,
    MemoirBookSourceMoveRequest,
    MemoirBookSourceRemoveRequest,
)
from app.schemas.memoir_chapter_source_editor import (
    MemoirChapterSourceEditorResponse,
    MemoirChapterSourceEditorSourceResponse,
)
from app.services.memoir import book_source_workflow
from app.services.memoir.book_plan_sources import (
    load_plan_with_chapters as _load_plan,
    plan_response as _plan_response,
)


def _source_response(source: MemoirBookSourceCoverageItem) -> MemoirChapterSourceEditorSourceResponse:
    current_chapter = sorted(
        source.included_in_chapters,
        key=lambda chapter: (chapter.sort_order, chapter.chapter_id),
    )[0] if source.included_in_chapters else None
    return MemoirChapterSourceEditorSourceResponse(
        source_index=source.source_index,
        voice_session_id=source.voice_session_id,
        message_id=source.message_id,
        utterance_id=source.utterance_id,
        transcript_version_id=source.transcript_version_id,
        audio_file_id=source.audio_file_id,
        start_ms=source.start_ms,
        end_ms=source.end_ms,
        text=source.text,
        created_at=source.created_at,
        source_material_type=source.source_material_type,
        source_route_action=source.source_route_action,
        source_route_confidence=source.source_route_confidence,
        source_route_reason=source.source_route_reason,
        source_route_updated_at=source.source_route_updated_at,
        is_included=source.is_included,
        included_in_chapters=source.included_in_chapters,
        current_chapter_id=current_chapter.chapter_id if current_chapter else None,
        current_chapter_title=current_chapter.title if current_chapter else None,
        current_chapter_sort_order=current_chapter.sort_order if current_chapter else None,
    )


async def get_chapter_source_editor(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    allow_admin: bool = False,
) -> MemoirChapterSourceEditorResponse:
    coverage = await book_source_workflow.get_book_source_coverage(
        db,
        user_id=user_id,
        project_id=project_id,
        allow_admin=allow_admin,
    )
    plan = await _load_plan(db, project_id)
    plan_data: MemoirBookPlanResponse | None = None
    if plan:
        plan_data = await _plan_response(db, plan)
    sources = [_source_response(source) for source in coverage.sources]
    return MemoirChapterSourceEditorResponse(
        project_id=coverage.project_id,
        plan_id=plan_data.id if plan_data else None,
        plan_title=plan_data.title if plan_data else None,
        plan_status=plan_data.status if plan_data else None,
        total_source_count=coverage.total_source_count,
        covered_source_count=coverage.covered_source_count,
        uncovered_source_count=coverage.uncovered_source_count,
        chapters=plan_data.chapters if plan_data else [],
        sources=sources,
        uncovered_sources=[source for source in sources if not source.is_included],
    )


async def assign_chapter_sources(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookSourceAssignmentRequest,
    allow_admin: bool = False,
) -> MemoirChapterSourceEditorResponse:
    await book_source_workflow.assign_book_sources_to_chapters(
        db,
        user_id=user_id,
        project_id=project_id,
        req=req,
        allow_admin=allow_admin,
    )
    return await get_chapter_source_editor(
        db,
        user_id=user_id,
        project_id=project_id,
        allow_admin=allow_admin,
    )


async def move_chapter_source(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookSourceMoveRequest,
    allow_admin: bool = False,
) -> MemoirChapterSourceEditorResponse:
    await book_source_workflow.move_book_source_between_chapters(
        db,
        user_id=user_id,
        project_id=project_id,
        req=req,
        allow_admin=allow_admin,
    )
    return await get_chapter_source_editor(
        db,
        user_id=user_id,
        project_id=project_id,
        allow_admin=allow_admin,
    )


async def remove_chapter_sources(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookSourceRemoveRequest,
    allow_admin: bool = False,
) -> MemoirChapterSourceEditorResponse:
    await book_source_workflow.remove_book_sources_from_chapter(
        db,
        user_id=user_id,
        project_id=project_id,
        req=req,
        allow_admin=allow_admin,
    )
    return await get_chapter_source_editor(
        db,
        user_id=user_id,
        project_id=project_id,
        allow_admin=allow_admin,
    )
