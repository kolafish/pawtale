from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import MemoirChapterSourceAssignedBy, MemoirChapterSourceLinkStatus
from app.models.memoir import (
    MemoirBookChapterDraftVersion,
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirChapterSourceLink,
)


PENDING_SOURCE_UTTERANCE_IDS_KEY = "pending_source_utterance_ids"


@dataclass(frozen=True)
class ChapterSourceAssignmentMetadata:
    assigned_by: MemoirChapterSourceAssignedBy | str = MemoirChapterSourceAssignedBy.SYSTEM
    confidence: float | None = None
    reason: str = ""


@dataclass(frozen=True)
class ChapterSourceLinkSyncResult:
    created_count: int = 0
    activated_count: int = 0
    updated_count: int = 0
    removed_count: int = 0


def _utcnow() -> datetime:
    return datetime.utcnow()


def _assigned_by_value(value: MemoirChapterSourceAssignedBy | str) -> str:
    return value.value if isinstance(value, MemoirChapterSourceAssignedBy) else value


def _string_ref_value(ref: dict, key: str) -> str:
    value = ref.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _optional_string_ref_value(ref: dict, key: str) -> str | None:
    value = ref.get(key)
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _optional_int_ref_value(ref: dict, key: str) -> int | None:
    value = ref.get(key)
    return value if isinstance(value, int) else None


def _source_refs(chapter: MemoirBookPlanChapter) -> list[dict]:
    return [dict(ref) for ref in (chapter.source_refs_json or []) if isinstance(ref, dict)]


def _pending_source_ids(chapter: MemoirBookPlanChapter) -> set[str]:
    value = (chapter.metadata_json or {}).get(PENDING_SOURCE_UTTERANCE_IDS_KEY, [])
    if not isinstance(value, list):
        return set()
    return {item.strip() for item in value if isinstance(item, str) and item.strip()}


def _link_key(chapter_id: str, utterance_id: str) -> tuple[str, str]:
    return chapter_id, utterance_id


def _metadata_for_utterance(
    utterance_id: str,
    *,
    default_metadata: ChapterSourceAssignmentMetadata,
    assignment_metadata_by_utterance: dict[str, ChapterSourceAssignmentMetadata] | None,
) -> ChapterSourceAssignmentMetadata:
    if assignment_metadata_by_utterance is None:
        return default_metadata
    return assignment_metadata_by_utterance.get(
        utterance_id,
        ChapterSourceAssignmentMetadata(
            assigned_by=MemoirChapterSourceAssignedBy.SYSTEM,
            reason="sync_existing_source_ref",
        ),
    )


def _apply_ref_fields(link: MemoirChapterSourceLink, ref: dict) -> bool:
    values = {
        "transcript_version_id": _optional_string_ref_value(ref, "transcript_version_id"),
        "audio_file_id": _optional_string_ref_value(ref, "audio_file_id"),
        "start_ms": _optional_int_ref_value(ref, "start_ms"),
        "end_ms": _optional_int_ref_value(ref, "end_ms"),
        "quote": _string_ref_value(ref, "quote"),
    }
    changed = False
    for field, value in values.items():
        if getattr(link, field) != value:
            setattr(link, field, value)
            changed = True
    return changed


def _apply_assignment_metadata(
    link: MemoirChapterSourceLink,
    metadata: ChapterSourceAssignmentMetadata,
) -> bool:
    values = {
        "assigned_by": _assigned_by_value(metadata.assigned_by),
        "assignment_confidence": metadata.confidence,
        "assignment_reason": metadata.reason.strip(),
    }
    changed = False
    for field, value in values.items():
        if getattr(link, field) != value:
            setattr(link, field, value)
            changed = True
    return changed


async def _load_project_plan(db: AsyncSession, *, project_id: str) -> MemoirBookPlan | None:
    return await db.scalar(
        select(MemoirBookPlan)
        .where(MemoirBookPlan.project_id == project_id)
        .options(selectinload(MemoirBookPlan.chapters))
        .execution_options(populate_existing=True)
    )


async def _latest_versions_by_chapter(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_ids: set[str],
) -> dict[str, MemoirBookChapterDraftVersion]:
    if not chapter_ids:
        return {}
    rows = (
        await db.execute(
            select(MemoirBookChapterDraftVersion)
            .where(
                MemoirBookChapterDraftVersion.project_id == project_id,
                MemoirBookChapterDraftVersion.chapter_id.in_(chapter_ids),
            )
            .order_by(
                MemoirBookChapterDraftVersion.chapter_id,
                MemoirBookChapterDraftVersion.version,
                MemoirBookChapterDraftVersion.created_at,
                MemoirBookChapterDraftVersion.id,
            )
        )
    ).scalars().all()
    latest_by_chapter: dict[str, MemoirBookChapterDraftVersion] = {}
    for version in rows:
        latest_by_chapter[version.chapter_id] = version
    return latest_by_chapter


async def sync_project_chapter_source_links(
    db: AsyncSession,
    *,
    project_id: str,
    assigned_by: MemoirChapterSourceAssignedBy | str = MemoirChapterSourceAssignedBy.SYSTEM,
    assignment_reason: str = "",
    assignment_metadata_by_utterance: dict[str, ChapterSourceAssignmentMetadata] | None = None,
    mark_missing_removed: bool = True,
) -> ChapterSourceLinkSyncResult:
    plan = await _load_project_plan(db, project_id=project_id)
    if not plan:
        return ChapterSourceLinkSyncResult()
    return await sync_plan_chapter_source_links(
        db,
        plan=plan,
        assigned_by=assigned_by,
        assignment_reason=assignment_reason,
        assignment_metadata_by_utterance=assignment_metadata_by_utterance,
        mark_missing_removed=mark_missing_removed,
    )


async def sync_plan_chapter_source_links(
    db: AsyncSession,
    *,
    plan: MemoirBookPlan,
    assigned_by: MemoirChapterSourceAssignedBy | str = MemoirChapterSourceAssignedBy.SYSTEM,
    assignment_reason: str = "",
    assignment_metadata_by_utterance: dict[str, ChapterSourceAssignmentMetadata] | None = None,
    mark_missing_removed: bool = True,
) -> ChapterSourceLinkSyncResult:
    existing_links = (
        await db.execute(
            select(MemoirChapterSourceLink).where(
                MemoirChapterSourceLink.project_id == plan.project_id,
                MemoirChapterSourceLink.plan_id == plan.id,
            )
        )
    ).scalars().all()
    existing_by_key = {
        _link_key(link.chapter_id, link.utterance_id): link
        for link in existing_links
    }
    latest_versions_by_chapter = await _latest_versions_by_chapter(
        db,
        project_id=plan.project_id,
        chapter_ids={chapter.id for chapter in plan.chapters},
    )
    has_merge_state_by_chapter: dict[str, bool] = {}
    for link in existing_links:
        if link.merged_draft_version_id:
            has_merge_state_by_chapter[link.chapter_id] = True

    default_metadata = ChapterSourceAssignmentMetadata(
        assigned_by=assigned_by,
        reason=assignment_reason,
    )
    current_keys: set[tuple[str, str]] = set()
    created_count = 0
    activated_count = 0
    updated_count = 0
    removed_count = 0

    for chapter in sorted(plan.chapters, key=lambda item: item.sort_order):
        pending_ids = _pending_source_ids(chapter)
        latest_version = latest_versions_by_chapter.get(chapter.id)
        should_bootstrap_merge_state = bool(latest_version) and not has_merge_state_by_chapter.get(chapter.id, False)
        for ref in _source_refs(chapter):
            utterance_id = _string_ref_value(ref, "utterance_id")
            if not utterance_id:
                continue
            key = _link_key(chapter.id, utterance_id)
            current_keys.add(key)
            metadata = _metadata_for_utterance(
                utterance_id,
                default_metadata=default_metadata,
                assignment_metadata_by_utterance=assignment_metadata_by_utterance,
            )
            link = existing_by_key.get(key)
            if link is None:
                link = MemoirChapterSourceLink(
                    project_id=plan.project_id,
                    plan_id=plan.id,
                    chapter_id=chapter.id,
                    utterance_id=utterance_id,
                    status=MemoirChapterSourceLinkStatus.ACTIVE.value,
                )
                _apply_ref_fields(link, ref)
                _apply_assignment_metadata(link, metadata)
                if (
                    latest_version
                    and should_bootstrap_merge_state
                    and utterance_id not in pending_ids
                ):
                    link.merged_draft_version_id = latest_version.id
                db.add(link)
                created_count += 1
                continue

            changed = False
            if link.status != MemoirChapterSourceLinkStatus.ACTIVE.value:
                link.status = MemoirChapterSourceLinkStatus.ACTIVE.value
                link.removed_at = None
                link.merged_draft_version_id = None
                changed = True
                activated_count += 1
                changed = _apply_assignment_metadata(link, metadata) or changed
            changed = _apply_ref_fields(link, ref) or changed
            if utterance_id in pending_ids and link.merged_draft_version_id is not None:
                link.merged_draft_version_id = None
                changed = True
            elif (
                latest_version
                and should_bootstrap_merge_state
                and utterance_id not in pending_ids
                and link.merged_draft_version_id is None
            ):
                link.merged_draft_version_id = latest_version.id
                changed = True
            if changed:
                link.updated_at = _utcnow()
                db.add(link)
                updated_count += 1

    if mark_missing_removed:
        removed_at = _utcnow()
        for key, link in existing_by_key.items():
            if key in current_keys or link.status == MemoirChapterSourceLinkStatus.REMOVED.value:
                continue
            link.status = MemoirChapterSourceLinkStatus.REMOVED.value
            link.removed_at = removed_at
            link.updated_at = removed_at
            db.add(link)
            removed_count += 1

    if created_count or activated_count or updated_count or removed_count:
        await db.flush()
    return ChapterSourceLinkSyncResult(
        created_count=created_count,
        activated_count=activated_count,
        updated_count=updated_count,
        removed_count=removed_count,
    )


async def backfill_project_chapter_source_links(
    db: AsyncSession,
    *,
    project_id: str,
) -> ChapterSourceLinkSyncResult:
    return await sync_project_chapter_source_links(
        db,
        project_id=project_id,
        assigned_by=MemoirChapterSourceAssignedBy.SYSTEM,
        assignment_reason="backfill_source_refs_json",
        mark_missing_removed=False,
    )


async def mark_utterance_links_removed(
    db: AsyncSession,
    *,
    project_id: str,
    utterance_id: str,
    removed_at: datetime | None = None,
) -> int:
    removed_at = removed_at or _utcnow()
    links = (
        await db.execute(
            select(MemoirChapterSourceLink).where(
                MemoirChapterSourceLink.project_id == project_id,
                MemoirChapterSourceLink.utterance_id == utterance_id,
                MemoirChapterSourceLink.status == MemoirChapterSourceLinkStatus.ACTIVE.value,
            )
        )
    ).scalars().all()
    for link in links:
        link.status = MemoirChapterSourceLinkStatus.REMOVED.value
        link.removed_at = removed_at
        link.updated_at = removed_at
        db.add(link)
    if links:
        await db.flush()
    return len(links)


async def update_utterance_link_ref_fields(
    db: AsyncSession,
    *,
    project_id: str,
    utterance_id: str,
    transcript_version_id: str | None,
    quote: str,
) -> int:
    links = (
        await db.execute(
            select(MemoirChapterSourceLink).where(
                MemoirChapterSourceLink.project_id == project_id,
                MemoirChapterSourceLink.utterance_id == utterance_id,
                MemoirChapterSourceLink.status == MemoirChapterSourceLinkStatus.ACTIVE.value,
            )
        )
    ).scalars().all()
    updated_count = 0
    now = _utcnow()
    for link in links:
        changed = False
        if link.transcript_version_id != transcript_version_id:
            link.transcript_version_id = transcript_version_id
            changed = True
        cleaned_quote = quote.strip()[:180]
        if link.quote != cleaned_quote:
            link.quote = cleaned_quote
            changed = True
        if changed:
            link.merged_draft_version_id = None
            link.updated_at = now
            db.add(link)
            updated_count += 1
    if updated_count:
        await db.flush()
    return updated_count
