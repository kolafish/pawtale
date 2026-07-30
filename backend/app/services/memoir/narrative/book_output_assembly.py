from __future__ import annotations


from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.clock import utcnow_naive
from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirBookPlanStatus,
    MemoirCompletionIssueChannel,
    MemoirCompletionIssueScope,
    MemoirCompletionIssueStatus,
    MemoirCreatedBy,
    MemoirOutputStatus,
)
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirBookOutput,
    MemoirBookOutputVersion,
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirCompletionIssue,
)
from app.models.projects import Project
from app.schemas.memoir_book import MemoirBookAssemblyResponse
from app.services.memoir import (
    book_editorial_actions,
    book_output_versions,
    book_review_answer_state,
    book_reviews,
)
from app.services.memoir.book_output_manuscript import (
    effective_book_metadata,
    formal_book_title,
    manuscript_from_snapshot,
    stored_cover_style,
)
from app.services.memoir.book_output_snapshots import (
    book_content_from_chapter_snapshots,
    chapter_snapshot,
    normalized_section_snapshots,
)
from app.services.memoir.book_plan_sources import included_book_chapters
from app.services.memoir.book_source_links import plan_source_ref_dicts_by_chapter
from app.services.memoir.book_output_state import invalidate_book_output
from app.services.memoir.image_assets import manuscript_image_snapshots


def _latest_chapter_version(
    chapter: MemoirBookPlanChapter,
) -> MemoirBookChapterDraftVersion | None:
    if chapter.draft is None or not chapter.draft.versions:
        return None
    return max(
        chapter.draft.versions,
        key=lambda version: (version.version, version.created_at, version.id),
    )


async def assemble_book_output(
    db: AsyncSession,
    *,
    project_id: str,
    allow_unapplied_review_answers: bool = False,
) -> MemoirBookAssemblyResponse:
    plan = await db.scalar(
        select(MemoirBookPlan)
        .where(MemoirBookPlan.project_id == project_id)
        .options(
            selectinload(MemoirBookPlan.chapters)
            .selectinload(MemoirBookPlanChapter.draft)
            .selectinload(MemoirBookChapterDraft.versions)
        )
    )
    if plan is None:
        raise ValueError("book plan does not belong to the project")
    if plan.status != MemoirBookPlanStatus.CONFIRMED.value:
        raise ValueError("confirm the book plan before assembling the book output")
    chapters = included_book_chapters(plan)
    if not chapters:
        raise ValueError("book plan has no chapters")
    chapter_versions: list[
        tuple[MemoirBookPlanChapter, MemoirBookChapterDraftVersion]
    ] = []
    incomplete_titles: list[str] = []
    unstructured_titles: list[str] = []
    for chapter in chapters:
        version = _latest_chapter_version(chapter)
        if (
            chapter.draft is None
            or chapter.draft.status != MemoirBookChapterDraftStatus.CONFIRMED.value
            or version is None
            or not version.content.strip()
        ):
            incomplete_titles.append(chapter.title or "未命名章节")
            continue
        if not normalized_section_snapshots(version.section_snapshot_json):
            unstructured_titles.append(chapter.title or "未命名章节")
            continue
        chapter_versions.append((chapter, version))
    if incomplete_titles:
        raise ValueError(
            "book output requires confirmed chapter drafts: "
            + ", ".join(incomplete_titles)
        )
    if unstructured_titles:
        raise ValueError(
            "book output requires structured chapter drafts: "
            + ", ".join(unstructured_titles)
        )
    current_review = await book_reviews.get_current_book_review(
        db,
        project_id=project_id,
    )
    if current_review is None or current_review.status != "ready":
        raise ValueError("book output requires a current book review")
    unresolved_decision = await db.scalar(
        select(MemoirCompletionIssue.id)
        .where(
            MemoirCompletionIssue.project_id == project_id,
            MemoirCompletionIssue.scope
            == MemoirCompletionIssueScope.BOOK.value,
            MemoirCompletionIssue.channel
            == MemoirCompletionIssueChannel.MANUAL_REVIEW.value,
            MemoirCompletionIssue.status
            != MemoirCompletionIssueStatus.CLOSED.value,
            MemoirCompletionIssue.input_signature
            == current_review.input_signature,
        )
        .limit(1)
    )
    if unresolved_decision:
        raise ValueError("book output requires resolving book review decisions")
    answer_context = await book_review_answer_state.load_answer_context(
        db,
        review=current_review,
    )

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
    )
    title = (
        output.title
        if output is not None
        else formal_book_title(plan.title or "我的回忆录")
    )
    content = book_content_from_chapter_snapshots(
        title=title,
        snapshots=chapter_snapshots,
    )
    previous_version: MemoirBookOutputVersion | None = None
    if output is None:
        if answer_context.answers and not allow_unapplied_review_answers:
            raise ValueError(
                "book output requires applying the saved book review answers"
            )
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
        if book_review_answer_state.marker_matches_context(
            output=output,
            version=previous_version,
            context=answer_context,
        ) or book_editorial_actions.marker_matches_review(
            output=output,
            version=previous_version,
            review=current_review,
            answers_signature=answer_context.answers_signature,
        ):
            return MemoirBookAssemblyResponse(
                project_id=project_id,
                output_id=output.id,
                output_version_id=previous_version.id,
                version=previous_version.version,
                content=previous_version.content,
                chapter_snapshot_json=list(
                    previous_version.chapter_snapshot_json or []
                ),
                created_at=previous_version.created_at,
            )
        if answer_context.answers and not allow_unapplied_review_answers:
            raise ValueError(
                "book output requires applying the saved book review answers"
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
    manuscript = manuscript_from_snapshot(
        title=title,
        content=content,
        snapshot=chapter_snapshots,
        cover_style_value=(
            stored_cover_style(previous_version)
            if previous_version
            else "classic_plain"
        ),
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
    return MemoirBookAssemblyResponse(
        project_id=project_id,
        output_id=output.id,
        output_version_id=version.id,
        version=version.version,
        content=version.content,
        chapter_snapshot_json=chapter_snapshots,
        created_at=version.created_at,
    )
