from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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
)
from app.schemas.memoir_sections import MemoirChapterSectionSummary
from app.services.memoir import sections as memoir_sections
from app.services.memoir.access import load_memoir_project
from app.services.memoir.book_source_links import (
    ChapterSourceChangeState,
    chapter_source_change_states_by_chapter,
    plan_source_ref_dicts_by_chapter,
)
from app.services.memoir.transcript_projection import current_utterance_text


PENDING_SOURCE_UTTERANCE_IDS_KEY = "pending_source_utterance_ids"
SOURCE_REFS_UPDATED_AT_KEY = "source_refs_updated_at"
REMOVED_SOURCE_UTTERANCE_IDS_KEY = "removed_source_utterance_ids"
SOURCE_REFS_REMOVED_AT_KEY = "source_refs_removed_at"
DRAFT_STATUS_REASON_KEY = "status_reason"

NEW_SOURCES_ASSIGNED_STATUS_REASON = "new_sources_assigned"
SOURCES_CHANGED_STATUS_REASON = "sources_changed"
SOURCES_DELETED_STATUS_REASON = "sources_deleted"

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
    parent_message_id: str | None = None
    interviewer_line: str = ""

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


def safe_quote(source_text: str, quote: str = "") -> str:
    cleaned = text_or_empty(quote)
    if cleaned and cleaned in source_text:
        return cleaned[:180]
    return source_text[:180]


def as_dict(ref: MemoirBookChapterSourceRef) -> dict[str, object]:
    return ref.model_dump(mode="json")


def sorted_chapters(plan: MemoirBookPlan) -> list[MemoirBookPlanChapter]:
    return sorted(plan.chapters, key=lambda chapter: chapter.sort_order)


def chapter_title_owner(chapter: MemoirBookPlanChapter, *, plan_created_by: str) -> MemoirCreatedBy | None:
    metadata = chapter.metadata_json or {}
    owner = metadata.get("title_updated_by")
    # Legacy activation stamped every unmarked title in a user-owned plan.
    # An AI-created chapter without evidence of a later title edit is still AI-owned.
    if (metadata.get("created_from_material_assignment_by") == "ai"
            and not metadata.get("title_updated_at") and "previous_title" not in metadata):
        return MemoirCreatedBy.AI
    if owner in {"user", "ai"}:
        return MemoirCreatedBy(owner)
    creator = metadata.get("created_from_material_assignment_by")
    if creator in {"user", "ai"}:
        return MemoirCreatedBy(creator)
    return MemoirCreatedBy.USER if plan_created_by == "user" else None


def included_book_chapters(plan: MemoirBookPlan) -> list[MemoirBookPlanChapter]:
    return [chapter for chapter in sorted_chapters(plan) if chapter.include_in_book]


def find_chapter_by_id(
    chapters: list[MemoirBookPlanChapter],
    chapter_id: str | None,
) -> MemoirBookPlanChapter | None:
    if not chapter_id:
        return None
    return next((chapter for chapter in chapters if chapter.id == chapter_id), None)


def renumber_chapters(chapters: list[MemoirBookPlanChapter]) -> None:
    for index, chapter in enumerate(chapters):
        chapter.sort_order = index


def set_temporary_chapter_orders(chapters: list[MemoirBookPlanChapter]) -> None:
    for index, chapter in enumerate(chapters):
        chapter.sort_order = 10000 + index


def unique_source_ref_dicts(refs: list[dict]) -> list[dict]:
    unique: list[dict] = []
    seen: set[str] = set()
    for ref in refs:
        utterance_id = source_ref_utterance_id(ref)
        if not utterance_id or utterance_id in seen:
            continue
        unique.append(dict(ref))
        seen.add(utterance_id)
    return unique


def merge_metadata(metadata_json: dict | None, patch: dict[str, object]) -> dict[str, object]:
    return {**(metadata_json or {}), **patch}


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


async def load_plan_with_chapters(db: AsyncSession, project_id: str) -> MemoirBookPlan | None:
    return await db.scalar(
        select(MemoirBookPlan)
        .where(MemoirBookPlan.project_id == project_id)
        .options(selectinload(MemoirBookPlan.chapters))
        .execution_options(populate_existing=True)
    )


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


def chapter_response(
    chapter: MemoirBookPlanChapter,
    *,
    created_at_by_utterance: dict[str, datetime] | None = None,
    source_refs: list[dict[str, object]] | None = None,
    source_change_state: ChapterSourceChangeState | None = None,
    sections: list[MemoirChapterSectionSummary] | None = None,
) -> MemoirBookPlanChapterResponse:
    source_created_at = created_at_by_utterance or {}
    if source_change_state:
        pending_source_count = source_change_state.pending_source_count
        pending_completion_source_count = (
            source_change_state.pending_completion_source_count
        )
        pending_new_source_count = source_change_state.pending_new_source_count
        removed_source_count = source_change_state.removed_source_count
    else:
        pending_source_count, removed_source_count = source_change_counts(chapter.metadata_json)
        pending_completion_source_count = 0
        pending_new_source_count = pending_source_count
    refs = source_refs or []
    return MemoirBookPlanChapterResponse(
        id=chapter.id,
        plan_id=chapter.plan_id,
        project_id=chapter.project_id,
        title=chapter.title,
        sort_order=chapter.sort_order,
        include_in_book=chapter.include_in_book,
        source_refs=[
            source_ref_response(ref, source_created_at)
            for ref in refs
            if isinstance(ref, dict)
        ],
        sections=sections or [],
        metadata_json=chapter.metadata_json or {},
        pending_source_count=pending_source_count,
        pending_completion_source_count=pending_completion_source_count,
        pending_new_source_count=pending_new_source_count,
        removed_source_count=removed_source_count,
        created_at=chapter.created_at,
        updated_at=chapter.updated_at,
    )


async def plan_response(db: AsyncSession, plan: MemoirBookPlan) -> MemoirBookPlanResponse:
    refs_by_chapter = await plan_source_ref_dicts_by_chapter(db, plan)
    source_change_states = await chapter_source_change_states_by_chapter(db, plan)
    sections_by_chapter = await memoir_sections.section_summaries_by_chapter(
        db,
        project_id=plan.project_id,
    )
    return MemoirBookPlanResponse(
        id=plan.id,
        project_id=plan.project_id,
        title=plan.title,
        status=MemoirBookPlanStatus(plan.status),
        created_by=MemoirCreatedBy(plan.created_by),
        model=plan.model,
        metadata_json=plan.metadata_json or {},
        chapters=[
            chapter_response(
                chapter,
                source_refs=refs_by_chapter.get(chapter.id, []),
                source_change_state=source_change_states.get(chapter.id),
                sections=sections_by_chapter.get(chapter.id, []),
            )
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
    allow_admin: bool = False,
) -> list[SourceSegment]:
    await load_memoir_project(db, project_id, user_id, allow_admin=allow_admin)
    result = await db.execute(
        select(MemoirVoiceUtterance, MemoirChatMessage)
        .join(MemoirVoiceSession, MemoirVoiceUtterance.voice_session_id == MemoirVoiceSession.id)
        .join(MemoirChatMessage, MemoirVoiceUtterance.chat_message_id == MemoirChatMessage.id)
        .where(
            MemoirVoiceSession.project_id == project_id,
            MemoirChatMessage.role == "user",
            MemoirChatMessage.content_status.in_(("final", "corrected")),
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
        text = current_utterance_text(utterance)
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
                parent_message_id=message.parent_message_id,
            )
        )
    return segments
