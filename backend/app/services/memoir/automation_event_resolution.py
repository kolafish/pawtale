from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import MemoirAutomationEventStatus, MemoirAutomationEventType
from app.models.memoir import MemoirAutomationEvent
from app.services.memoir.automation_event_metadata import (
    CHAPTER_DRAFT_ASSEMBLED_REASON,
    DELETED_UTTERANCE_IDS_KEY,
    RESOLVED_AT_KEY,
    RESOLVED_CHAPTER_ID_KEY,
    RESOLVED_CHAPTER_TITLE_KEY,
    RESOLVED_RESULT_VERSION_ID_KEY,
    RESOLVED_UTTERANCE_IDS_KEY,
    RESOLUTION_KEY,
    RESOLUTION_REASON_KEY,
    SOURCE_ASSIGNED_REASON,
    SOURCE_DELETED_AT_KEY,
    SOURCE_DELETED_REASON,
    _clean_unknown_utterance_ids,
    _clean_utterance_ids,
    _event_is_visible,
    _event_utterance_ids,
    _metadata_utterance_ids,
    _metadata_without_deleted_utterances,
    _without_deleted_ids,
)
from app.services.memoir.automation_event_sources import (
    AssignedSourceChapter,
    assigned_source_chapters,
)


@dataclass(frozen=True)
class AutomationEventSourceCleanupResult:
    updated_event_count: int = 0
    deleted_utterance_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AutomationEventSourceReviewResolutionResult:
    updated_event_count: int = 0
    resolved_utterance_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AutomationEventChapterAuditResolutionResult:
    updated_event_count: int = 0
    resolved_chapter_id: str = ""


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def _quoted_title(text: str) -> str:
    match = re.search(r"「([^」]+)」", text)
    return match.group(1).strip() if match else ""


def _summary_after_source_cleanup(event: MemoirAutomationEvent, remaining_count: int) -> str:
    if remaining_count <= 0:
        return "关联讲述已删除，自动处理记录已隐藏。"
    title = _quoted_title(event.summary_text)
    if event.event_type == MemoirAutomationEventType.SOURCE_ASSIGNED.value and title:
        return f"{remaining_count} 条讲述已自动整理进「{title}」。"
    if event.event_type == MemoirAutomationEventType.CHAPTER_AUTO_REVISED.value and title:
        return f"「{title}」已自动补写 {remaining_count} 条新增讲述。"
    return event.summary_text


def _apply_deleted_utterance_cleanup(
    event: MemoirAutomationEvent,
    *,
    deleted_ids: set[str],
    now_iso: str,
) -> bool:
    event_ids = _event_utterance_ids(event)
    deleted_refs = [utterance_id for utterance_id in event_ids if utterance_id in deleted_ids]
    if not deleted_refs:
        return False

    metadata = _metadata_without_deleted_utterances(event.metadata_json or {}, deleted_ids)
    metadata[DELETED_UTTERANCE_IDS_KEY] = _clean_utterance_ids(
        _clean_unknown_utterance_ids(metadata.get(DELETED_UTTERANCE_IDS_KEY)) + deleted_refs
    )
    metadata[SOURCE_DELETED_AT_KEY] = now_iso

    remaining_direct_ids = _without_deleted_ids(event.utterance_ids_json, deleted_ids)
    remaining_ids = _clean_utterance_ids(remaining_direct_ids + _metadata_utterance_ids(metadata))
    should_hide = not remaining_ids or event.status in {
        MemoirAutomationEventStatus.PENDING.value,
        MemoirAutomationEventStatus.NEEDS_REVIEW.value,
    }

    event.utterance_ids_json = remaining_direct_ids
    event.summary_text = _summary_after_source_cleanup(event, len(remaining_ids))
    if should_hide:
        event.status = MemoirAutomationEventStatus.COMPLETED.value
        if not metadata.get(RESOLUTION_KEY):
            metadata[RESOLUTION_KEY] = "ignored"
            metadata[RESOLUTION_REASON_KEY] = SOURCE_DELETED_REASON
        metadata.setdefault(RESOLVED_AT_KEY, now_iso)
    event.metadata_json = metadata
    return True


def _apply_assigned_source_review_resolution(
    event: MemoirAutomationEvent,
    *,
    assigned_sources: dict[str, AssignedSourceChapter],
    now_iso: str,
    reason: str,
) -> bool:
    if event.event_type != MemoirAutomationEventType.SOURCE_HELD.value:
        return False
    if event.status != MemoirAutomationEventStatus.NEEDS_REVIEW.value:
        return False

    resolved_ids = [utterance_id for utterance_id in _event_utterance_ids(event) if utterance_id in assigned_sources]
    if not resolved_ids:
        return False

    resolved_source = assigned_sources[resolved_ids[0]]
    event.chapter_id = resolved_source.chapter_id
    event.status = MemoirAutomationEventStatus.COMPLETED.value
    event.summary_text = f"讲述已整理进「{resolved_source.chapter_title}」，自动处理记录已隐藏。"
    event.metadata_json = {
        **(event.metadata_json or {}),
        RESOLUTION_KEY: "confirmed",
        RESOLUTION_REASON_KEY: reason,
        RESOLVED_AT_KEY: now_iso,
        RESOLVED_CHAPTER_ID_KEY: resolved_source.chapter_id,
        RESOLVED_CHAPTER_TITLE_KEY: resolved_source.chapter_title,
        RESOLVED_UTTERANCE_IDS_KEY: resolved_ids,
    }
    return True


def _metadata_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _event_followup_task_id(event: MemoirAutomationEvent) -> str:
    metadata = _metadata_dict(event.metadata_json)
    route = _metadata_dict(metadata.get("route")) or metadata
    followup_answer = _metadata_dict(route.get("followup_answer"))
    task_id = followup_answer.get("task_id")
    return task_id.strip() if isinstance(task_id, str) else ""


def _metadata_chapter_id(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _audit_event_matches_assembled_chapter(
    event: MemoirAutomationEvent,
    *,
    chapter_id: str,
) -> bool:
    if event.event_type != MemoirAutomationEventType.AUDIT_COMPLETED.value:
        return False
    if not _event_is_visible(event):
        return False

    metadata = _metadata_dict(event.metadata_json)
    if metadata.get("source") != "book_quality_audit":
        return False

    primary_action = _metadata_dict(metadata.get("primary_action"))
    action_chapter_id = _metadata_chapter_id(primary_action.get("chapter_id"))
    if (
        primary_action.get("type") == "open_chapter_workspace"
        and action_chapter_id == chapter_id
    ):
        return True

    primary_chapter = _metadata_dict(metadata.get("primary_chapter"))
    return (
        _metadata_chapter_id(primary_chapter.get("chapter_id")) == chapter_id
        and primary_chapter.get("status")
        in {"needs_update", "needs_draft", "thin_draft"}
    )


def _apply_assembled_chapter_audit_resolution(
    event: MemoirAutomationEvent,
    *,
    chapter_id: str,
    now_iso: str,
    reason: str,
    result_version_id: str | None,
) -> bool:
    if not _audit_event_matches_assembled_chapter(event, chapter_id=chapter_id):
        return False

    metadata = dict(event.metadata_json or {})
    metadata[RESOLUTION_KEY] = "confirmed"
    metadata[RESOLUTION_REASON_KEY] = reason
    metadata[RESOLVED_AT_KEY] = now_iso
    metadata[RESOLVED_CHAPTER_ID_KEY] = chapter_id
    if result_version_id:
        metadata[RESOLVED_RESULT_VERSION_ID_KEY] = result_version_id
    event.metadata_json = metadata
    return True


async def cleanup_automation_events_for_deleted_utterances(
    db: AsyncSession,
    *,
    project_id: str,
    utterance_ids: list[str],
) -> AutomationEventSourceCleanupResult:
    deleted_ids = set(_clean_utterance_ids(utterance_ids))
    if not deleted_ids:
        return AutomationEventSourceCleanupResult()

    result = await db.execute(
        select(MemoirAutomationEvent).where(MemoirAutomationEvent.project_id == project_id)
    )
    now_iso = _utcnow_iso()
    updated_count = 0
    for event in result.scalars().all():
        if _apply_deleted_utterance_cleanup(event, deleted_ids=deleted_ids, now_iso=now_iso):
            db.add(event)
            updated_count += 1
    if updated_count:
        await db.flush()
    return AutomationEventSourceCleanupResult(
        updated_event_count=updated_count,
        deleted_utterance_ids=sorted(deleted_ids),
    )


async def resolve_assigned_source_review_events(
    db: AsyncSession,
    *,
    project_id: str,
    utterance_ids: list[str] | None = None,
    reason: str = SOURCE_ASSIGNED_REASON,
    confirmed_plan_only: bool = False,
) -> AutomationEventSourceReviewResolutionResult:
    clean_ids = set(_clean_utterance_ids(utterance_ids)) if utterance_ids is not None else None
    assigned_sources = await assigned_source_chapters(
        db,
        project_id=project_id,
        utterance_ids=clean_ids,
        confirmed_plan_only=confirmed_plan_only,
    )
    if not assigned_sources:
        return AutomationEventSourceReviewResolutionResult()

    result = await db.execute(
        select(MemoirAutomationEvent).where(
            MemoirAutomationEvent.project_id == project_id,
            MemoirAutomationEvent.event_type == MemoirAutomationEventType.SOURCE_HELD.value,
            MemoirAutomationEvent.status == MemoirAutomationEventStatus.NEEDS_REVIEW.value,
        )
    )
    now_iso = _utcnow_iso()
    updated_count = 0
    resolved_ids: list[str] = []
    for event in result.scalars().all():
        event_ids = [utterance_id for utterance_id in _event_utterance_ids(event) if utterance_id in assigned_sources]
        if not event_ids:
            continue
        if _apply_assigned_source_review_resolution(
            event,
            assigned_sources=assigned_sources,
            now_iso=now_iso,
            reason=reason,
        ):
            db.add(event)
            updated_count += 1
            resolved_ids.extend(event_ids)
    if updated_count:
        await db.flush()
    return AutomationEventSourceReviewResolutionResult(
        updated_event_count=updated_count,
        resolved_utterance_ids=sorted(set(resolved_ids)),
    )


async def resolve_followup_source_review_events(
    db: AsyncSession,
    *,
    project_id: str,
    task_id: str,
    resolved_by_utterance_id: str,
) -> int:
    result = await db.execute(
        select(MemoirAutomationEvent).where(
            MemoirAutomationEvent.project_id == project_id,
            MemoirAutomationEvent.event_type == MemoirAutomationEventType.SOURCE_HELD.value,
            MemoirAutomationEvent.status == MemoirAutomationEventStatus.NEEDS_REVIEW.value,
        )
    )
    now_iso = _utcnow_iso()
    updated_count = 0
    for event in result.scalars().all():
        if _event_followup_task_id(event) != task_id:
            continue
        metadata = dict(event.metadata_json or {})
        metadata[RESOLUTION_KEY] = "confirmed"
        metadata[RESOLUTION_REASON_KEY] = "followup_answer_resolved"
        metadata[RESOLVED_AT_KEY] = now_iso
        metadata[RESOLVED_UTTERANCE_IDS_KEY] = [resolved_by_utterance_id]
        event.status = MemoirAutomationEventStatus.COMPLETED.value
        event.summary_text = "需要确认的内容已通过后续回答处理。"
        event.metadata_json = metadata
        db.add(event)
        updated_count += 1
    if updated_count:
        await db.flush()
    return updated_count


async def resolve_assembled_chapter_audit_events(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    result_version_id: str | None = None,
    reason: str = CHAPTER_DRAFT_ASSEMBLED_REASON,
) -> AutomationEventChapterAuditResolutionResult:
    if not chapter_id:
        return AutomationEventChapterAuditResolutionResult()

    result = await db.execute(
        select(MemoirAutomationEvent).where(
            MemoirAutomationEvent.project_id == project_id,
            MemoirAutomationEvent.event_type == MemoirAutomationEventType.AUDIT_COMPLETED.value,
        )
    )
    now_iso = _utcnow_iso()
    updated_count = 0
    for event in result.scalars().all():
        if _apply_assembled_chapter_audit_resolution(
            event,
            chapter_id=chapter_id,
            now_iso=now_iso,
            reason=reason,
            result_version_id=result_version_id,
        ):
            db.add(event)
            updated_count += 1
    if updated_count:
        await db.flush()
    return AutomationEventChapterAuditResolutionResult(
        updated_event_count=updated_count,
        resolved_chapter_id=chapter_id if updated_count else "",
    )
