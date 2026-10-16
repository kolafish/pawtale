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
from app.schemas.memoir_book import MemoirBookAssemblyResponse, MemoirBookOutputEditorialCopyRegenerateRequest
from app.services.memoir import (
    automation_events,
    book_editorial_actions,
    book_output_versions,
    book_review_answer_state,
    book_reviews,
    completion_issues,
    chapter_generation,
)
from app.services.memoir.book_output_manuscript import (
    effective_book_metadata,
    manuscript_from_snapshot,
    stored_manuscript_design,
    stored_cover_style,
    fallback_chapter_quotes,
)
from app.services.memoir.book_output_snapshots import (
    book_content_from_chapter_snapshots,
    chapter_snapshot,
    load_book_plan_drafts,
    readable_chapter_versions,
)
from app.services.memoir.project_ai_preferences import project_transcript_polish_level
from app.services.memoir.book_plan_sources import included_book_chapters
from app.services.memoir.book_source_links import plan_source_ref_dicts_by_chapter
from app.services.memoir.sections import lock_project_chapters
from app.services.memoir.book_output_state import invalidate_book_output
from app.services.memoir.image_assets import (
    auto_place_share_video_backgrounds,
    manuscript_image_snapshots,
    sync_latest_book_output_image_snapshots,
)


async def assemble_book_output(
    db: AsyncSession,
    *,
    project_id: str,
    eligible_chapter_ids: set[str] | None = None,
    automatic: bool = False,
) -> MemoirBookAssemblyResponse:
    await lock_project_chapters(db, project_id=project_id)
    plan = await load_book_plan_drafts(db, project_id=project_id)
    if plan.status != MemoirBookPlanStatus.CONFIRMED.value:
        raise ValueError("请先确认章节规划")
    chapters = included_book_chapters(plan)
    chapter_versions = readable_chapter_versions(plan, allow_partial=True)
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
    blocking_decisions = [
        issue for issue in decisions
        if issue.issue_type in completion_issues.BLOCKING_BOOK_ISSUE_TYPES
    ]
    blocked_chapter_ids = completion_issues.blocking_book_chapter_ids(
        blocking_decisions, (chapter.id for chapter in chapters),
    )
    chapter_versions = [(chapter, version) for chapter, version in chapter_versions if chapter.id not in blocked_chapter_ids]
    pending_facts = await chapter_generation.pending_fact_chapter_ids(
        db, project_id=project_id, chapter_versions=chapter_versions,
    )
    chapter_versions = [(chapter, version) for chapter, version in chapter_versions if chapter.id not in pending_facts]
    if not chapter_versions and not automatic:
        raise ValueError("还没有可收录的章节正文，请先处理相关章节的待确认事项")
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
    auto_images_changed = await auto_place_share_video_backgrounds(
        db,
        project_id=project_id,
        chapter_ids={chapter.id for chapter, _ in chapter_versions},
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
        versions = list((await db.scalars(
            select(MemoirBookOutputVersion)
            .where(MemoirBookOutputVersion.output_id == output.id)
            .order_by(MemoirBookOutputVersion.version)
        )).all()) if previous_version else []
        copy_state = book_output_versions.automatic_editorial_copy_state(output, versions)
        manual_copy_fields = copy_state.manual_chapter_fields
        previous_snapshots = {
            item["chapter_id"]: item
            for item in (previous_version.chapter_snapshot_json if previous_version else [])
            if isinstance(item, dict) and isinstance(item.get("chapter_id"), str)
        }
        previous_contains_blocked_chapter = bool(
            blocked_chapter_ids.intersection(previous_snapshots)
        )
        next_snapshots = {
            item["chapter_id"]: item for item in chapter_snapshots
        }
        if (
            not previous_contains_blocked_chapter
            and (
                preserves_applied_answers
                or (automatic and protected)
            )
        ):
            if auto_images_changed:
                await sync_latest_book_output_image_snapshots(db, project_id=project_id)
                previous_version = await book_output_versions.load_latest_book_output_version(
                    db, output_id=output.id,
                )
                assert previous_version is not None
            previous_version = await book_output_versions.repair_automatic_chapter_quotes(
                db, output=output, version=previous_version, state=copy_state, polish_level=project_transcript_polish_level(project),
            )
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
            if auto_images_changed:
                await sync_latest_book_output_image_snapshots(db, project_id=project_id)
                previous_version = await book_output_versions.load_latest_book_output_version(
                    db, output_id=output.id,
                )
                assert previous_version is not None
            previous_version = await book_output_versions.repair_automatic_chapter_quotes(
                db, output=output, version=previous_version, state=copy_state, polish_level=project_transcript_polish_level(project),
            )
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
        }
        output.updated_at = utcnow_naive()
        db.add(output)
    chapter_illustrations, image_appendix, cover_image = await manuscript_image_snapshots(
        db,
        project_id=project_id,
    )
    template, custom_layout = stored_manuscript_design(
        previous_version.manuscript_json if previous_version else None,
    )
    metadata = effective_book_metadata(
        project=project, stored=previous_version.manuscript_json if previous_version else None,
    )
    if previous_version:
        stored_metadata = book_output_versions.stored_manuscript_metadata(previous_version.manuscript_json)
        metadata.update({field: stored_metadata[field] for field in copy_state.manual_targets})
    if previous_version and (
        book_output_versions.book_editorial_copy_signature(title, previous_version.chapter_snapshot_json)
        != book_output_versions.book_editorial_copy_signature(title, chapter_snapshots)
    ):
        # A new body must never be published with obsolete automatic front matter,
        # even while the next copy job is still running or has failed.
        for field in ("preface", "afterword", "back_cover_quote"):
            if field not in copy_state.manual_targets:
                metadata.pop(field, None)
    manuscript = manuscript_from_snapshot(
        polish_level=project_transcript_polish_level(project),
        title=title,
        content=content,
        snapshot=chapter_snapshots,
        cover_style_value=stored_cover_style(previous_version),
        template=template,
        custom_layout=custom_layout,
        cover_image=cover_image,
        metadata=metadata,
        chapter_illustrations_by_chapter=chapter_illustrations,
        image_appendix=image_appendix,
    )
    if manuscript and previous_version:
        old_chapters = book_output_versions.manuscript_chapters(previous_version)
        for chapter in manuscript.chapters:
            old = old_chapters.get(chapter.chapter_id)
            previous_snapshot = previous_snapshots.get(chapter.chapter_id)
            next_snapshot = next_snapshots.get(chapter.chapter_id)
            if not (old and previous_snapshot and next_snapshot):
                continue
            fields = set(manual_copy_fields.get(chapter.chapter_id, []))
            if (book_output_versions.chapter_editorial_copy_signature(previous_snapshot)
                    == book_output_versions.chapter_editorial_copy_signature(next_snapshot)):
                fields.update({"intro", "pull_quotes"})
            # An authored intro has no source binding. Deleting an unrelated
            # narration must never turn it into a protected template sentence.
            if "intro" in fields:
                chapter.intro = str(old.get("intro") or "")
            preserve_quotes = "pull_quotes" in manual_copy_fields.get(chapter.chapter_id, [])
            chapter.pull_quotes = book_output_versions.retained_chapter_quotes(
                old, next_snapshot, preserve_manual=preserve_quotes, polish_level=project_transcript_polish_level(project),
            )
            if not chapter.pull_quotes and not preserve_quotes:
                chapter.pull_quotes = fallback_chapter_quotes(chapter.content, next_snapshot, polish_level=project_transcript_polish_level(project))
    version = await book_output_versions.append_book_output_version(
        db,
        output=output,
        content=content,
        chapter_snapshot_json=chapter_snapshots,
        manuscript_json=manuscript.model_dump(mode="json") if manuscript else {},
        created_by=MemoirCreatedBy.SYSTEM,
    )
    if chapter_snapshots and not automatic:
        from app.services.memoir.book_generation_jobs import schedule_book_editorial_copy_generation
        await schedule_book_editorial_copy_generation(db, project_id=project_id, user_id=project.user_id,
            req=MemoirBookOutputEditorialCopyRegenerateRequest(), automatic=True)
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
