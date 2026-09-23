from __future__ import annotations

from copy import deepcopy

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
from app.schemas.memoir_book import MemoirBookAssemblyResponse, MemoirBookManuscriptQuote
from app.services.memoir import (
    automation_events,
    book_editorial_actions,
    book_output_versions,
    book_review_answer_state,
    book_reviews,
    completion_issues,
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


def _manuscript_chapters(version: MemoirBookOutputVersion) -> dict[str, dict[str, object]]:
    manuscript = version.manuscript_json if isinstance(version.manuscript_json, dict) else {}
    chapters = manuscript.get("chapters")
    return {
        chapter["chapter_id"]: chapter
        for chapter in (chapters if isinstance(chapters, list) else [])
        if isinstance(chapter, dict) and isinstance(chapter.get("chapter_id"), str)
    }


def _local_copy_changes(
    before: MemoirBookOutputVersion,
    after: MemoirBookOutputVersion,
) -> dict[str, set[str]] | None:
    """Recognize a user edit confined to chapter introductions and quotations."""
    if before.chapter_snapshot_json != after.chapter_snapshot_json:
        return None
    for version in (before, after):
        if version.content != book_content_from_chapter_snapshots(
            title=book_output_versions.book_output_version_title(version, fallback=""),
            snapshots=version.chapter_snapshot_json,
        ):
            return None
    old_chapters = _manuscript_chapters(before)
    new_chapters = _manuscript_chapters(after)
    if not old_chapters or old_chapters.keys() != new_chapters.keys():
        return None
    changes: dict[str, set[str]] = {}
    for chapter_id, old in old_chapters.items():
        new = new_chapters[chapter_id]
        # Image placement is independent of the chapter's editorial text.
        old_body = {key: value for key, value in old.items() if key not in {"intro", "pull_quotes", "illustrations"}}
        new_body = {key: value for key, value in new.items() if key not in {"intro", "pull_quotes", "illustrations"}}
        if old_body != new_body:
            return None
        changed = {
            field for field in ("intro", "pull_quotes")
            if old.get(field) != new.get(field)
        }
        if changed:
            changes[chapter_id] = changed
    return changes or None


def _manual_copy_fields(
    versions: list[MemoirBookOutputVersion],
) -> dict[str, set[str]]:
    latest_chapters = _manuscript_chapters(versions[-1])
    edits: dict[tuple[str, str], object] = {}
    for before, after in zip(versions, versions[1:]):
        if after.created_by != MemoirCreatedBy.USER.value:
            continue
        changes = _local_copy_changes(before, after)
        if changes is None:
            continue
        chapters = _manuscript_chapters(after)
        for chapter_id, fields in changes.items():
            for field in fields:
                edits[(chapter_id, field)] = chapters[chapter_id].get(field)
    manual: dict[str, set[str]] = {}
    for (chapter_id, field), value in edits.items():
        if latest_chapters.get(chapter_id, {}).get(field) == value:
            manual.setdefault(chapter_id, set()).add(field)
    return manual


def _only_local_copy_is_protected(
    output: MemoirBookOutput,
    versions: list[MemoirBookOutputVersion],
) -> bool:
    metadata = output.metadata_json if isinstance(output.metadata_json, dict) else {}
    applied_ids = {
        marker.get("output_version_id")
        for marker in (
            metadata.get(book_review_answer_state.BOOK_REVIEW_ANSWER_APPLICATION_KEY),
            metadata.get(book_editorial_actions.BOOK_EDITORIAL_REVISION_APPLICATION_KEY),
        )
        if isinstance(marker, dict)
    }
    for index in range(len(versions) - 1, 0, -1):
        current = versions[index]
        if current.id in applied_ids:
            return False
        previous = versions[index - 1]
        if book_output_versions._same_book_body(current, previous):
            continue
        return (
            current.created_by == MemoirCreatedBy.USER.value
            and _local_copy_changes(previous, current) is not None
        )
    return False


def _same_chapter_copy_context(left: dict[str, object], right: dict[str, object]) -> bool:
    def sections(snapshot: dict[str, object]) -> list[tuple[object, ...]]:
        value = snapshot.get("sections")
        return [
            (
                section.get("section_id"),
                section.get("title"),
                section.get("section_type"),
                section.get("used_material_ids"),
            )
            for section in (value if isinstance(value, list) else [])
            if isinstance(section, dict)
        ]

    return (
        left.get("chapter_title") == right.get("chapter_title")
        and sections(left) == sections(right)
        and _chapter_source_refs(left) == _chapter_source_refs(right)
    )


def _chapter_source_refs(snapshot: dict[str, object]) -> dict[str, tuple[object, object]]:
    refs = snapshot.get("source_refs")
    return {
        ref["utterance_id"]: (ref.get("transcript_version_id"), ref.get("quote"))
        for ref in (refs if isinstance(refs, list) else [])
        if isinstance(ref, dict) and isinstance(ref.get("utterance_id"), str)
    }


def _chapter_lost_sources(left: dict[str, object], right: dict[str, object]) -> bool:
    def source_ids(snapshot: dict[str, object]) -> set[str]:
        sections = snapshot.get("sections")
        materials = {
            material_id
            for section in (sections if isinstance(sections, list) else [])
            if isinstance(section, dict)
            for material_id in section.get("used_material_ids", [])
            if isinstance(material_id, str)
        }
        if materials:
            return materials
        return set(_chapter_source_refs(snapshot))

    old_refs = _chapter_source_refs(left)
    new_refs = _chapter_source_refs(right)
    return bool(source_ids(left) - source_ids(right)) or any(
        new_refs.get(source_id) != source for source_id, source in old_refs.items()
    )


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
    blocking_decisions = [
        issue for issue in decisions
        if issue.issue_type in completion_issues.BLOCKING_BOOK_ISSUE_TYPES
    ]
    if blocking_decisions and not automatic:
        raise ValueError("book output requires resolving book review decisions")
    blocked_chapter_ids = completion_issues.blocking_book_chapter_ids(
        blocking_decisions, (chapter.id for chapter in chapters),
    )
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
        versions = list((await db.scalars(
            select(MemoirBookOutputVersion)
            .where(MemoirBookOutputVersion.output_id == output.id)
            .order_by(MemoirBookOutputVersion.version)
        )).all()) if previous_version else []
        manual_copy_fields = _manual_copy_fields(versions) if versions else {}
        previous_snapshots = {
            item["chapter_id"]: item
            for item in (previous_version.chapter_snapshot_json if previous_version else [])
            if isinstance(item, dict) and isinstance(item.get("chapter_id"), str)
        }
        next_snapshots = {
            item["chapter_id"]: item for item in chapter_snapshots
        }
        changed_manual_copy = any(
            chapter_id in next_snapshots
            and not _chapter_lost_sources(previous_snapshots.get(chapter_id, {}), next_snapshots[chapter_id])
            and not _same_chapter_copy_context(previous_snapshots.get(chapter_id, {}), next_snapshots[chapter_id])
            for chapter_id in manual_copy_fields
        )
        if (
            preserves_applied_answers
            or (automatic and changed_manual_copy)
            or (automatic and protected and not _only_local_copy_is_protected(output, versions))
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
                preserved_manual=protected or changed_manual_copy,
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
    if manuscript and previous_version:
        old_chapters = _manuscript_chapters(previous_version)
        for chapter in manuscript.chapters:
            old = old_chapters.get(chapter.chapter_id)
            previous_snapshot = previous_snapshots.get(chapter.chapter_id)
            next_snapshot = next_snapshots.get(chapter.chapter_id)
            if old and previous_snapshot and next_snapshot and _same_chapter_copy_context(previous_snapshot, next_snapshot):
                chapter.intro = str(old.get("intro") or "")
                chapter.pull_quotes = [
                    MemoirBookManuscriptQuote.model_validate(value)
                    for value in deepcopy(old.get("pull_quotes") or [])
                    if isinstance(value, dict)
                ]
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
