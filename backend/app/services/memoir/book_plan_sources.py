from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import MemoirBookPlanStatus, MemoirCreatedBy
from app.models.memoir import (
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirChatMessage,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.schemas.memoir_book import (
    MemoirBookChapterSourceRef,
    MemoirBookPlanChapterResponse,
    MemoirBookPlanResponse,
    MemoirBookSourceCoverageItem,
    MemoirBookSourceIncludedChapter,
)
from app.services.memoir.access import load_memoir_project


PENDING_SOURCE_UTTERANCE_IDS_KEY = "pending_source_utterance_ids"
SOURCE_ASSIGNMENT_UPDATED_AT_KEY = "source_assignment_updated_at"
SOURCE_REFS_UPDATED_AT_KEY = "source_refs_updated_at"
REMOVED_SOURCE_UTTERANCE_IDS_KEY = "removed_source_utterance_ids"
SOURCE_REFS_REMOVED_AT_KEY = "source_refs_removed_at"
DRAFT_STATUS_REASON_KEY = "status_reason"

NEW_SOURCES_ASSIGNED_STATUS_REASON = "new_sources_assigned"
SOURCES_CHANGED_STATUS_REASON = "sources_changed"
SOURCES_DELETED_STATUS_REASON = "sources_deleted"

SOURCE_CHANGE_METADATA_KEYS = {
    PENDING_SOURCE_UTTERANCE_IDS_KEY,
    SOURCE_ASSIGNMENT_UPDATED_AT_KEY,
    SOURCE_REFS_UPDATED_AT_KEY,
    REMOVED_SOURCE_UTTERANCE_IDS_KEY,
    SOURCE_REFS_REMOVED_AT_KEY,
}


@dataclass(frozen=True)
class SourceSegment:
    source_index: int
    voice_session_id: str
    message_id: str
    utterance_id: str
    transcript_version_id: str | None
    audio_file_id: str | None
    start_ms: int | None
    end_ms: int | None
    text: str
    created_at: datetime

    def ref(self, quote: str = "") -> MemoirBookChapterSourceRef:
        return MemoirBookChapterSourceRef(
            voice_session_id=self.voice_session_id,
            message_id=self.message_id,
            utterance_id=self.utterance_id,
            transcript_version_id=self.transcript_version_id,
            audio_file_id=self.audio_file_id,
            start_ms=self.start_ms,
            end_ms=self.end_ms,
            created_at=self.created_at,
            quote=safe_quote(self.text, quote),
        )


def text_or_empty(value: str | None) -> str:
    return (value or "").strip()


def display_text(utterance: MemoirVoiceUtterance) -> str:
    return text_or_empty(utterance.transcript_edited) or text_or_empty(utterance.transcript_raw)


def safe_quote(source_text: str, quote: str = "") -> str:
    cleaned = text_or_empty(quote)
    if cleaned and cleaned in source_text:
        return cleaned[:180]
    return source_text[:180]


def as_dict(ref: MemoirBookChapterSourceRef) -> dict[str, object]:
    return ref.model_dump(mode="json")


def merge_metadata(metadata_json: dict | None, patch: dict[str, object]) -> dict[str, object]:
    return {**(metadata_json or {}), **patch}


def metadata_without_keys(metadata_json: dict | None, keys: set[str]) -> dict[str, object]:
    metadata = dict(metadata_json or {})
    for key in keys:
        metadata.pop(key, None)
    return metadata


def source_change_metadata_keys() -> set[str]:
    return set(SOURCE_CHANGE_METADATA_KEYS)


def draft_status_metadata(metadata_json: dict | None, reason: str) -> dict[str, object]:
    return merge_metadata(metadata_json, {DRAFT_STATUS_REASON_KEY: reason})


def source_ref_utterance_ids(refs: list[dict] | list[MemoirBookChapterSourceRef]) -> list[str]:
    ids: list[str] = []
    for ref in refs:
        if isinstance(ref, MemoirBookChapterSourceRef):
            ids.append(ref.utterance_id)
        elif isinstance(ref, dict) and isinstance(ref.get("utterance_id"), str):
            ids.append(ref["utterance_id"])
    return ids


def source_ref_utterance_id(ref: object) -> str:
    if isinstance(ref, MemoirBookChapterSourceRef):
        return ref.utterance_id
    if isinstance(ref, dict) and isinstance(ref.get("utterance_id"), str):
        return ref["utterance_id"]
    return ""


def source_ids_from_metadata(metadata_json: dict | None, key: str) -> set[str]:
    value = (metadata_json or {}).get(key, [])
    if not isinstance(value, list):
        return set()
    return {item.strip() for item in value if isinstance(item, str) and item.strip()}


def pending_source_ids(metadata_json: dict | None) -> set[str]:
    return source_ids_from_metadata(metadata_json, PENDING_SOURCE_UTTERANCE_IDS_KEY)


def removed_source_ids(metadata_json: dict | None) -> set[str]:
    return source_ids_from_metadata(metadata_json, REMOVED_SOURCE_UTTERANCE_IDS_KEY)


def source_change_counts(metadata_json: dict | None) -> tuple[int, int]:
    return len(pending_source_ids(metadata_json)), len(removed_source_ids(metadata_json))


def set_source_assignment_metadata(metadata_json: dict | None, pending_ids: set[str]) -> dict[str, object]:
    metadata = dict(metadata_json or {})
    if pending_ids:
        metadata[PENDING_SOURCE_UTTERANCE_IDS_KEY] = sorted(pending_ids)
        metadata[SOURCE_ASSIGNMENT_UPDATED_AT_KEY] = datetime.now(UTC).isoformat()
    else:
        metadata.pop(PENDING_SOURCE_UTTERANCE_IDS_KEY, None)
        metadata.pop(SOURCE_ASSIGNMENT_UPDATED_AT_KEY, None)
    return metadata


def set_source_change_metadata(
    metadata_json: dict | None,
    *,
    added_ids: set[str],
    removed_ids: set[str],
) -> dict[str, object]:
    metadata = dict(metadata_json or {})
    if added_ids:
        metadata[PENDING_SOURCE_UTTERANCE_IDS_KEY] = sorted(added_ids)
    else:
        metadata.pop(PENDING_SOURCE_UTTERANCE_IDS_KEY, None)
    if removed_ids:
        metadata[REMOVED_SOURCE_UTTERANCE_IDS_KEY] = sorted(removed_ids)
    else:
        metadata.pop(REMOVED_SOURCE_UTTERANCE_IDS_KEY, None)
    metadata[SOURCE_REFS_UPDATED_AT_KEY] = datetime.now(UTC).isoformat()
    return metadata


def set_deleted_source_metadata(
    metadata_json: dict | None,
    *,
    pending_ids: set[str],
    removed_ids: set[str],
    removed_at: str,
) -> dict[str, object]:
    metadata = dict(metadata_json or {})
    if pending_ids:
        metadata[PENDING_SOURCE_UTTERANCE_IDS_KEY] = sorted(pending_ids)
    else:
        metadata.pop(PENDING_SOURCE_UTTERANCE_IDS_KEY, None)
    if removed_ids:
        metadata[REMOVED_SOURCE_UTTERANCE_IDS_KEY] = sorted(removed_ids)
        metadata[SOURCE_REFS_REMOVED_AT_KEY] = removed_at
    else:
        metadata.pop(REMOVED_SOURCE_UTTERANCE_IDS_KEY, None)
        metadata.pop(SOURCE_REFS_REMOVED_AT_KEY, None)
    return metadata


def source_ref_response(
    ref: dict[str, object],
    created_at_by_utterance: dict[str, datetime],
) -> MemoirBookChapterSourceRef:
    data = dict(ref)
    utterance_id = data.get("utterance_id")
    if not data.get("created_at") and isinstance(utterance_id, str):
        created_at = created_at_by_utterance.get(utterance_id)
        if created_at:
            data["created_at"] = created_at
    return MemoirBookChapterSourceRef.model_validate(data)


async def source_ref_created_at_by_utterance(
    db: AsyncSession,
    plan: MemoirBookPlan,
) -> dict[str, datetime]:
    utterance_ids = {
        ref["utterance_id"]
        for chapter in plan.chapters
        for ref in (chapter.source_refs_json or [])
        if isinstance(ref, dict) and isinstance(ref.get("utterance_id"), str)
    }
    if not utterance_ids:
        return {}
    result = await db.execute(
        select(MemoirVoiceUtterance.id, MemoirVoiceUtterance.created_at).where(
            MemoirVoiceUtterance.id.in_(utterance_ids)
        )
    )
    return {utterance_id: created_at for utterance_id, created_at in result.all()}


def chapter_response(
    chapter: MemoirBookPlanChapter,
    *,
    created_at_by_utterance: dict[str, datetime] | None = None,
) -> MemoirBookPlanChapterResponse:
    source_created_at = created_at_by_utterance or {}
    pending_source_count, removed_source_count = source_change_counts(chapter.metadata_json)
    return MemoirBookPlanChapterResponse(
        id=chapter.id,
        plan_id=chapter.plan_id,
        project_id=chapter.project_id,
        title=chapter.title,
        sort_order=chapter.sort_order,
        source_refs=[
            source_ref_response(ref, source_created_at)
            for ref in (chapter.source_refs_json or [])
            if isinstance(ref, dict)
        ],
        metadata_json=chapter.metadata_json or {},
        pending_source_count=pending_source_count,
        removed_source_count=removed_source_count,
        created_at=chapter.created_at,
        updated_at=chapter.updated_at,
    )


async def plan_response(db: AsyncSession, plan: MemoirBookPlan) -> MemoirBookPlanResponse:
    created_at_by_utterance = await source_ref_created_at_by_utterance(db, plan)
    return MemoirBookPlanResponse(
        id=plan.id,
        project_id=plan.project_id,
        title=plan.title,
        status=MemoirBookPlanStatus(plan.status),
        created_by=MemoirCreatedBy(plan.created_by),
        model=plan.model,
        metadata_json=plan.metadata_json or {},
        chapters=[
            chapter_response(chapter, created_at_by_utterance=created_at_by_utterance)
            for chapter in plan.chapters
        ],
        created_at=plan.created_at,
        updated_at=plan.updated_at,
    )


async def source_segments(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
) -> list[SourceSegment]:
    await load_memoir_project(db, project_id, user_id)
    result = await db.execute(
        select(MemoirVoiceUtterance, MemoirChatMessage)
        .join(MemoirVoiceSession, MemoirVoiceUtterance.voice_session_id == MemoirVoiceSession.id)
        .join(MemoirChatMessage, MemoirVoiceUtterance.chat_message_id == MemoirChatMessage.id)
        .where(
            MemoirVoiceSession.project_id == project_id,
            or_(
                MemoirVoiceUtterance.transcript_raw != "",
                MemoirVoiceUtterance.transcript_edited != "",
            ),
        )
        .order_by(
            MemoirVoiceSession.created_at,
            MemoirVoiceUtterance.created_at,
            MemoirVoiceUtterance.id,
        )
    )
    segments: list[SourceSegment] = []
    for utterance, message in result.all():
        text = display_text(utterance)
        if not text:
            continue
        segments.append(
            SourceSegment(
                source_index=len(segments) + 1,
                voice_session_id=utterance.voice_session_id,
                message_id=message.id,
                utterance_id=utterance.id,
                transcript_version_id=message.transcript_version_id,
                audio_file_id=utterance.audio_file_id or message.audio_file_id,
                start_ms=utterance.start_ms,
                end_ms=utterance.end_ms,
                text=text,
                created_at=utterance.created_at,
            )
        )
    return segments


def covered_utterance_ids(plan: MemoirBookPlan | None) -> set[str]:
    if not plan:
        return set()
    covered: set[str] = set()
    for chapter in plan.chapters:
        for ref in chapter.source_refs_json or []:
            if isinstance(ref, dict) and isinstance(ref.get("utterance_id"), str):
                covered.add(ref["utterance_id"])
    return covered


def included_chapters_by_utterance(
    plan: MemoirBookPlan | None,
) -> dict[str, list[MemoirBookSourceIncludedChapter]]:
    if not plan:
        return {}
    included: dict[str, list[MemoirBookSourceIncludedChapter]] = {}
    for chapter in sorted(plan.chapters, key=lambda item: item.sort_order):
        chapter_item = MemoirBookSourceIncludedChapter(
            chapter_id=chapter.id,
            title=chapter.title,
            sort_order=chapter.sort_order,
        )
        for ref in chapter.source_refs_json or []:
            if not isinstance(ref, dict) or not isinstance(ref.get("utterance_id"), str):
                continue
            included.setdefault(ref["utterance_id"], []).append(chapter_item)
    return included


def source_coverage_item(
    source: SourceSegment,
    included_chapters: list[MemoirBookSourceIncludedChapter] | None = None,
) -> MemoirBookSourceCoverageItem:
    included = included_chapters or []
    return MemoirBookSourceCoverageItem(
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
        is_included=bool(included),
        included_in_chapters=included,
    )
