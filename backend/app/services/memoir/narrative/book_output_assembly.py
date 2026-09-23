from __future__ import annotations


from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow_naive
from app.domain.memoir.types import (
    MemoirAutomationEventType,
    MemoirBookPlanStatus,
    MemoirCompletionIssueChannel,
    MemoirCompletionIssueStatus,
    MemoirCreatedBy,
    MemoirOutputStatus,
)
from app.models.memoir import (
    MemoirBookOutput,
    MemoirBookOutputVersion,
    MemoirCompletionIssue,
)
from app.models.projects import Project
from app.schemas.memoir_book import MemoirBookAssemblyResponse
from app.services.memoir import (
    automation_events,
    book_editorial_actions,
    book_output_versions,
    book_review_answer_state,
    book_reviews,
)
from app.services.memoir.book_output_manuscript import (
    effective_book_metadata,
    manuscript_from_snapshot,
    stored_manuscript_design,
    stored_cover_style,
)
from app.services.memoir.book_output_snapshots import (
    book_content_from_chapter_snapshots,
    chapter_snapshot,
    load_book_plan_drafts,
    readable_chapter_versions,
)
from app.services.memoir.book_plan_sources import included_book_chapters
from app.services.memoir.book_source_links import plan_source_ref_dicts_by_chapter
from app.services.memoir.book_output_state import invalidate_book_output
from app.services.memoir.image_assets import manuscript_image_snapshots


async def assemble_book_output(
    db: AsyncSession,
    *,
    project_id: str,
    eligible_chapter_ids: set[str] | None = None,
    automatic: bool = False,
) -> MemoirBookAssemblyResponse:
    plan = await load_book_plan_drafts(db, project_id=project_id)
    if plan.status != MemoirBookPlanStatus.CONFIRMED.value:
        raise ValueError("请先确认章节规划")
    chapters = included_book_chapters(plan)
    chapter_versions = readable_chapter_versions(plan, allow_partial=automatic)
    if eligible_chapter_ids is not None:
        chapter_versions = [(chapter, version) for chapter, version in chapter_versions if chapter.id in eligible_chapter_ids]
    current_review = await book_reviews.get_current_book_review(
        db,
        project_id=project_id,
    ) if chapters else None
    decisions = (await db.scalars(select(MemoirCompletionIssue).where(
        MemoirCompletionIssue.project_id == project_id,
        MemoirCompletionIssue.channel == MemoirCompletionIssueChannel.MANUAL_REVIEW.value,
        MemoirCompletionIssue.status != MemoirCompletionIssueStatus.CLOSED.value,
    ))).all()
    if decisions and not automatic:
        raise ValueError("book output requires resolving book review decisions")
    blocked_chapter_ids = {issue.chapter_id for issue in decisions if issue.chapter_id}
    book_decisions = [issue for issue in decisions if issue.scope == "book"]
    for issue in book_decisions:
        blocked_chapter_ids.update(issue.related_chapter_ids_json)
    if any(not issue.related_chapter_ids_json for issue in book_decisions):
        # Missing scope evidence cannot establish which chapters are safe to include.
        blocked_chapter_ids.update(chapter.id for chapter in chapters)
    chapter_versions = [(chapter, version) for chapter, version in chapter_versions if chapter.id not in blocked_chapter_ids]
    omitted = [chapter.id for chapter in chapters if chapter.id not in {item.id for item, _ in chapter_versions}]
    answer_context = await book_review_answer_state.load_answer_context(db, review=current_review) if current_review else None

    project = await db.get(Project, project_id)
    if project is None:
        raise ValueError("project does not exist")
    source_refs_by_chapter = await plan_source_ref_dicts_by_chapter(db, plan)
    chapter_snapshots: list[dict[str, object]] = []
    for index, (chapter, version) in enumerate(chapter_versions, start=1):
        chapter_snapshots.append(
            chapter_snapshot(
                chapter,
                version,
                chapter_index=index,
                source_refs=source_refs_by_chapter.get(chapter.id),
            )
        )
    output = await db.scalar(
        select(MemoirBookOutput).where(MemoirBookOutput.project_id == project_id)
        .with_for_update().execution_options(populate_existing=True)
    )
    title = project.title.strip()
    content = book_content_from_chapter_snapshots(
        title=title,
        snapshots=chapter_snapshots,
    )
    previous_version: MemoirBookOutputVersion | None = None
    if output is None:
        output = MemoirBookOutput(
            project_id=project_id,
            plan_id=plan.id,
            title=title,
            status=MemoirOutputStatus.DRAFT.value,
            metadata_json={"source": "chapter_drafts"},
        )
        db.add(output)
        await db.flush()
    else:
        previous_version = await book_output_versions.load_latest_book_output_version(
            db,
            output_id=output.id,
        )
        protected = bool(previous_version and await book_output_versions.book_body_is_protected(
            db, output=output, version=previous_version,
        ))
        preserves_applied_answers = bool(answer_context and (
            book_review_answer_state.marker_matches_context(output=output, version=previous_version, context=answer_context)
            or book_editorial_actions.marker_matches_review(output=output, version=previous_version,
                review=current_review, answers_signature=answer_context.answers_signature)
        ))
        if (automatic and protected) or preserves_applied_answers:
            return MemoirBookAssemblyResponse(
                project_id=project_id,
                output_id=output.id,
                output_version_id=previous_version.id,
                version=previous_version.version,
                content=previous_version.content,
                chapter_snapshot_json=list(
                    previous_version.chapter_snapshot_json or []
                ),
                preserved_manual=protected,
                omitted_chapter_ids=omitted,
                created_at=previous_version.created_at,
            )
        if (
            previous_version is not None
            and previous_version.content == content
            and previous_version.chapter_snapshot_json == chapter_snapshots
        ):
            return MemoirBookAssemblyResponse(
                project_id=project_id,
                output_id=output.id,
                output_version_id=previous_version.id,
                version=previous_version.version,
                content=previous_version.content,
                chapter_snapshot_json=chapter_snapshots,
                omitted_chapter_ids=omitted,
                created_at=previous_version.created_at,
            )
        output.plan_id = plan.id
        output.title = title
        invalidate_book_output(output)
        output.metadata_json = {
            **(
                output.metadata_json
                if isinstance(output.metadata_json, dict)
                else {}
            ),
            "source": "chapter_drafts",
            "book_tts": None,
        }
        output.updated_at = utcnow_naive()
        db.add(output)
    chapter_illustrations, image_appendix = await manuscript_image_snapshots(
        db,
        project_id=project_id,
    )
    template, custom_layout = stored_manuscript_design(
        previous_version.manuscript_json if previous_version else None
    )
    manuscript = manuscript_from_snapshot(
        title=title,
        content=content,
        snapshot=chapter_snapshots,
        cover_style_value=(
            stored_cover_style(previous_version)
            if previous_version
            else "classic_plain"
        ),
        template=template,
        custom_layout=custom_layout,
        metadata=effective_book_metadata(
            project=project,
            stored=previous_version.manuscript_json if previous_version else None,
        ),
        chapter_illustrations_by_chapter=chapter_illustrations,
        image_appendix=image_appendix,
    )
    version = await book_output_versions.append_book_output_version(
        db,
        output=output,
        content=content,
        chapter_snapshot_json=chapter_snapshots,
        manuscript_json=manuscript.model_dump(mode="json") if manuscript else {},
        created_by=MemoirCreatedBy.SYSTEM,
    )
    if chapter_snapshots:
        await automation_events.record_automation_event(
            db,
            project_id=project_id,
            event_type=MemoirAutomationEventType.BOOK_OUTPUT_GENERATED,
            summary_text=f"书稿已更新，已有 {len(chapter_snapshots)} 章可阅读。",
            metadata_json={"output_version_id": version.id, "chapter_count": len(chapter_snapshots)},
        )
    return MemoirBookAssemblyResponse(
        project_id=project_id,
        output_id=output.id,
        output_version_id=version.id,
        version=version.version,
        content=version.content,
        chapter_snapshot_json=chapter_snapshots,
        omitted_chapter_ids=omitted,
        created_at=version.created_at,
    )
