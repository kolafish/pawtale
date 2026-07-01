from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import MemoirChapterSourceLinkStatus
from app.models.memoir import (
    MemoirBookChapterDraftVersion,
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirChapterSourceLink,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.schemas.memoir_book import MemoirBookSourceIncludedChapter


SOURCE_REF_KEYS = {
    "voice_session_id",
    "message_id",
    "utterance_id",
    "transcript_version_id",
    "audio_file_id",
    "start_ms",
    "end_ms",
    "created_at",
    "quote",
}
PENDING_SOURCE_UTTERANCE_IDS_KEY = "pending_source_utterance_ids"
REMOVED_SOURCE_UTTERANCE_IDS_KEY = "removed_source_utterance_ids"


@dataclass(frozen=True)
class ChapterSourceChangeState:
    pending_source_ids: set[str]
    removed_source_ids: set[str]

    @property
    def pending_source_count(self) -> int:
        return len(self.pending_source_ids)

    @property
    def removed_source_count(self) -> int:
        return len(self.removed_source_ids)


def _sorted_chapters(plan: MemoirBookPlan) -> list[MemoirBookPlanChapter]:
    return sorted(plan.chapters, key=lambda chapter: chapter.sort_order)


def source_ref_utterance_id(ref: object) -> str:
    if isinstance(ref, dict):
        value = ref.get("utterance_id")
        return value.strip() if isinstance(value, str) else ""
    value = getattr(ref, "utterance_id", "")
    return value.strip() if isinstance(value, str) else ""


def source_ref_utterance_ids(refs: list[dict[str, object]]) -> list[str]:
    return [
        utterance_id
        for utterance_id in (source_ref_utterance_id(ref) for ref in refs)
        if utterance_id
    ]


def _source_ids_from_metadata(metadata_json: dict | None, key: str) -> set[str]:
    value = (metadata_json or {}).get(key, [])
    if not isinstance(value, list):
        return set()
    return {item.strip() for item in value if isinstance(item, str) and item.strip()}


def _legacy_change_state(
    chapter: MemoirBookPlanChapter,
    *,
    valid_utterance_ids: set[str] | None = None,
) -> ChapterSourceChangeState:
    pending_source_ids = _source_ids_from_metadata(chapter.metadata_json, PENDING_SOURCE_UTTERANCE_IDS_KEY)
    if valid_utterance_ids is not None:
        pending_source_ids &= valid_utterance_ids
    return ChapterSourceChangeState(
        pending_source_ids=pending_source_ids,
        removed_source_ids=_source_ids_from_metadata(chapter.metadata_json, REMOVED_SOURCE_UTTERANCE_IDS_KEY),
    )


def _legacy_source_refs(chapter: MemoirBookPlanChapter) -> list[dict[str, object]]:
    refs: list[dict[str, object]] = []
    seen: set[str] = set()
    for ref in chapter.source_refs_json or []:
        if not isinstance(ref, dict):
            continue
        utterance_id = source_ref_utterance_id(ref)
        if not utterance_id or utterance_id in seen:
            continue
        refs.append(dict(ref))
        seen.add(utterance_id)
    return refs


async def _links_by_chapter(
    db: AsyncSession,
    *,
    plan: MemoirBookPlan,
) -> dict[str, list[MemoirChapterSourceLink]]:
    rows = (
        await db.execute(
            select(MemoirChapterSourceLink)
            .where(
                MemoirChapterSourceLink.project_id == plan.project_id,
                MemoirChapterSourceLink.plan_id == plan.id,
            )
            .order_by(
                MemoirChapterSourceLink.chapter_id,
                MemoirChapterSourceLink.created_at,
                MemoirChapterSourceLink.id,
            )
        )
    ).scalars().all()
    grouped: dict[str, list[MemoirChapterSourceLink]] = {}
    for link in rows:
        grouped.setdefault(link.chapter_id, []).append(link)
    return grouped


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


async def _utterances_by_id(
    db: AsyncSession,
    *,
    project_id: str,
    utterance_ids: set[str],
) -> dict[str, MemoirVoiceUtterance]:
    if not utterance_ids:
        return {}
    rows = (
        await db.execute(
            select(MemoirVoiceUtterance)
            .join(MemoirVoiceSession, MemoirVoiceUtterance.voice_session_id == MemoirVoiceSession.id)
            .where(
                MemoirVoiceSession.project_id == project_id,
                MemoirVoiceUtterance.id.in_(utterance_ids),
            )
        )
    ).scalars().all()
    return {row.id: row for row in rows}


def _created_at_value(value: object) -> datetime | str | None:
    if isinstance(value, (datetime, str)):
        return value
    return None


def _legacy_ref_by_utterance(refs: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    by_utterance: dict[str, dict[str, object]] = {}
    for ref in refs:
        utterance_id = source_ref_utterance_id(ref)
        if utterance_id and utterance_id not in by_utterance:
            by_utterance[utterance_id] = ref
    return by_utterance


def _legacy_order(refs: list[dict[str, object]]) -> dict[str, int]:
    order: dict[str, int] = {}
    for index, ref in enumerate(refs):
        utterance_id = source_ref_utterance_id(ref)
        if utterance_id and utterance_id not in order:
            order[utterance_id] = index
    return order


def _legacy_ref_with_utterance(
    ref: dict[str, object],
    *,
    utterance: MemoirVoiceUtterance | None,
) -> dict[str, object]:
    data = {key: value for key, value in dict(ref).items() if key in SOURCE_REF_KEYS}
    if utterance:
        data.setdefault("voice_session_id", utterance.voice_session_id)
        data.setdefault("message_id", utterance.chat_message_id or "")
        data.setdefault("audio_file_id", utterance.audio_file_id)
        data.setdefault("start_ms", utterance.start_ms)
        data.setdefault("end_ms", utterance.end_ms)
        data.setdefault("created_at", utterance.created_at)
    return data


def _link_ref(
    link: MemoirChapterSourceLink,
    *,
    utterance: MemoirVoiceUtterance | None,
    legacy_ref: dict[str, object] | None,
) -> dict[str, object] | None:
    data: dict[str, object] = {}
    if legacy_ref:
        data.update({key: value for key, value in legacy_ref.items() if key in SOURCE_REF_KEYS})
    data["utterance_id"] = link.utterance_id
    if utterance:
        data["voice_session_id"] = utterance.voice_session_id
        data["message_id"] = utterance.chat_message_id or str(data.get("message_id") or "")
        data["created_at"] = utterance.created_at
        if utterance.audio_file_id:
            data.setdefault("audio_file_id", utterance.audio_file_id)
        if utterance.start_ms is not None:
            data.setdefault("start_ms", utterance.start_ms)
        if utterance.end_ms is not None:
            data.setdefault("end_ms", utterance.end_ms)

    if link.transcript_version_id:
        data["transcript_version_id"] = link.transcript_version_id
    if link.audio_file_id:
        data["audio_file_id"] = link.audio_file_id
    if link.start_ms is not None:
        data["start_ms"] = link.start_ms
    if link.end_ms is not None:
        data["end_ms"] = link.end_ms
    if link.quote.strip():
        data["quote"] = link.quote
    else:
        data.setdefault("quote", "")

    if not isinstance(data.get("voice_session_id"), str) or not data.get("voice_session_id"):
        return None
    if not isinstance(data.get("message_id"), str):
        return None
    created_at = _created_at_value(data.get("created_at"))
    if created_at:
        data["created_at"] = created_at
    return data


def _source_refs_for_chapter(
    *,
    chapter: MemoirBookPlanChapter,
    links: list[MemoirChapterSourceLink],
    utterances: dict[str, MemoirVoiceUtterance],
) -> list[dict[str, object]]:
    legacy_refs = _legacy_source_refs(chapter)
    legacy_by_utterance = _legacy_ref_by_utterance(legacy_refs)
    order_by_utterance = _legacy_order(legacy_refs)
    link_status_by_utterance = {link.utterance_id: link.status for link in links}
    ordered_refs: list[tuple[int, datetime, str, dict[str, object]]] = []

    for link in links:
        if link.status != MemoirChapterSourceLinkStatus.ACTIVE.value:
            continue
        ref = _link_ref(
            link,
            utterance=utterances.get(link.utterance_id),
            legacy_ref=legacy_by_utterance.get(link.utterance_id),
        )
        if not ref:
            continue
        ordered_refs.append((
            order_by_utterance.get(link.utterance_id, 1000000),
            link.created_at or datetime.min,
            link.id,
            ref,
        ))

    seen = {
        source_ref_utterance_id(item[3])
        for item in ordered_refs
    }
    for legacy_ref in legacy_refs:
        utterance_id = source_ref_utterance_id(legacy_ref)
        if not utterance_id or utterance_id in seen:
            continue
        if utterance_id in link_status_by_utterance:
            continue
        ordered_refs.append((
            order_by_utterance.get(utterance_id, 1000000),
            datetime.min,
            utterance_id,
            _legacy_ref_with_utterance(legacy_ref, utterance=utterances.get(utterance_id)),
        ))
        seen.add(utterance_id)
    return [item[3] for item in sorted(ordered_refs, key=lambda item: (item[0], item[1], item[2]))]


def _link_change_state(
    *,
    chapter: MemoirBookPlanChapter,
    links: list[MemoirChapterSourceLink],
    latest_version: MemoirBookChapterDraftVersion | None,
    valid_utterance_ids: set[str] | None = None,
) -> ChapterSourceChangeState:
    legacy_state = _legacy_change_state(chapter, valid_utterance_ids=valid_utterance_ids)
    if not links:
        return legacy_state

    has_merge_state = any(link.merged_draft_version_id for link in links)
    if not latest_version or not has_merge_state:
        return legacy_state

    pending_source_ids: set[str] = set()
    removed_source_ids: set[str] = set()
    active_link_ids: set[str] = set()
    removed_link_ids: set[str] = set()
    for link in links:
        if link.status == MemoirChapterSourceLinkStatus.ACTIVE.value:
            active_link_ids.add(link.utterance_id)
            if link.merged_draft_version_id == latest_version.id:
                continue
            if valid_utterance_ids is None or link.utterance_id in valid_utterance_ids:
                pending_source_ids.add(link.utterance_id)
            continue

        if link.status != MemoirChapterSourceLinkStatus.REMOVED.value:
            continue
        removed_link_ids.add(link.utterance_id)
        if link.removed_at and link.removed_at <= latest_version.created_at:
            continue
        if link.merged_draft_version_id == latest_version.id and not link.removed_at:
            continue
        removed_source_ids.add(link.utterance_id)

    pending_source_ids.update(legacy_state.pending_source_ids - active_link_ids)
    removed_source_ids.update(legacy_state.removed_source_ids - removed_link_ids)
    return ChapterSourceChangeState(
        pending_source_ids=pending_source_ids,
        removed_source_ids=removed_source_ids,
    )


async def chapter_source_change_state(
    db: AsyncSession,
    *,
    chapter: MemoirBookPlanChapter,
    valid_utterance_ids: set[str] | None = None,
) -> ChapterSourceChangeState:
    links = (
        await db.execute(
            select(MemoirChapterSourceLink).where(
                MemoirChapterSourceLink.project_id == chapter.project_id,
                MemoirChapterSourceLink.chapter_id == chapter.id,
            )
        )
    ).scalars().all()
    latest_version = (
        await _latest_versions_by_chapter(
            db,
            project_id=chapter.project_id,
            chapter_ids={chapter.id},
        )
    ).get(chapter.id)
    return _link_change_state(
        chapter=chapter,
        links=list(links),
        latest_version=latest_version,
        valid_utterance_ids=valid_utterance_ids,
    )


async def chapter_source_change_states_by_chapter(
    db: AsyncSession,
    plan: MemoirBookPlan | None,
    *,
    valid_utterance_ids: set[str] | None = None,
) -> dict[str, ChapterSourceChangeState]:
    if not plan:
        return {}
    chapters = _sorted_chapters(plan)
    links_by_chapter = await _links_by_chapter(db, plan=plan)
    latest_versions_by_chapter = await _latest_versions_by_chapter(
        db,
        project_id=plan.project_id,
        chapter_ids={chapter.id for chapter in chapters},
    )
    return {
        chapter.id: _link_change_state(
            chapter=chapter,
            links=links_by_chapter.get(chapter.id, []),
            latest_version=latest_versions_by_chapter.get(chapter.id),
            valid_utterance_ids=valid_utterance_ids,
        )
        for chapter in chapters
    }


async def mark_chapter_sources_merged(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    draft_version_id: str,
    include_removed: bool = False,
) -> int:
    statuses = [MemoirChapterSourceLinkStatus.ACTIVE.value]
    if include_removed:
        statuses.append(MemoirChapterSourceLinkStatus.REMOVED.value)
    links = (
        await db.execute(
            select(MemoirChapterSourceLink).where(
                MemoirChapterSourceLink.project_id == project_id,
                MemoirChapterSourceLink.chapter_id == chapter_id,
                MemoirChapterSourceLink.status.in_(statuses),
            )
        )
    ).scalars().all()
    updated_count = 0
    now = datetime.now(UTC).replace(tzinfo=None)
    for link in links:
        if link.merged_draft_version_id == draft_version_id:
            continue
        link.merged_draft_version_id = draft_version_id
        link.updated_at = now
        db.add(link)
        updated_count += 1
    if updated_count:
        await db.flush()
    return updated_count


async def plan_source_ref_dicts_by_chapter(
    db: AsyncSession,
    plan: MemoirBookPlan | None,
) -> dict[str, list[dict[str, object]]]:
    if not plan:
        return {}
    chapters = _sorted_chapters(plan)
    links_by_chapter = await _links_by_chapter(db, plan=plan)
    utterance_ids = {
        link.utterance_id
        for links in links_by_chapter.values()
        for link in links
        if link.status == MemoirChapterSourceLinkStatus.ACTIVE.value
    }
    utterance_ids.update(
        utterance_id
        for chapter in chapters
        for utterance_id in source_ref_utterance_ids(_legacy_source_refs(chapter))
    )
    utterances = await _utterances_by_id(db, project_id=plan.project_id, utterance_ids=utterance_ids)
    return {
        chapter.id: _source_refs_for_chapter(
            chapter=chapter,
            links=links_by_chapter.get(chapter.id, []),
            utterances=utterances,
        )
        for chapter in chapters
    }


async def chapter_source_ref_dicts(
    db: AsyncSession,
    *,
    chapter: MemoirBookPlanChapter,
) -> list[dict[str, object]]:
    plan = await db.scalar(
        select(MemoirBookPlan)
        .where(
            MemoirBookPlan.id == chapter.plan_id,
            MemoirBookPlan.project_id == chapter.project_id,
        )
        .options(selectinload(MemoirBookPlan.chapters))
    )
    if not plan:
        return [
            _legacy_ref_with_utterance(ref, utterance=None)
            for ref in _legacy_source_refs(chapter)
        ]
    return (await plan_source_ref_dicts_by_chapter(db, plan)).get(chapter.id, [])


async def covered_utterance_ids(db: AsyncSession, plan: MemoirBookPlan | None) -> set[str]:
    refs_by_chapter = await plan_source_ref_dicts_by_chapter(db, plan)
    return {
        utterance_id
        for refs in refs_by_chapter.values()
        for utterance_id in source_ref_utterance_ids(refs)
    }


async def included_chapters_by_utterance(
    db: AsyncSession,
    plan: MemoirBookPlan | None,
) -> dict[str, list[MemoirBookSourceIncludedChapter]]:
    if not plan:
        return {}
    refs_by_chapter = await plan_source_ref_dicts_by_chapter(db, plan)
    included: dict[str, list[MemoirBookSourceIncludedChapter]] = {}
    for chapter in _sorted_chapters(plan):
        chapter_item = MemoirBookSourceIncludedChapter(
            chapter_id=chapter.id,
            title=chapter.title,
            sort_order=chapter.sort_order,
        )
        for utterance_id in source_ref_utterance_ids(refs_by_chapter.get(chapter.id, [])):
            included.setdefault(utterance_id, []).append(chapter_item)
    return included


async def source_count_by_chapter(
    db: AsyncSession,
    plan: MemoirBookPlan | None,
    *,
    valid_utterance_ids: set[str] | None = None,
) -> dict[str, int]:
    refs_by_chapter = await plan_source_ref_dicts_by_chapter(db, plan)
    counts: dict[str, int] = {}
    for chapter_id, refs in refs_by_chapter.items():
        utterance_ids = set(source_ref_utterance_ids(refs))
        if valid_utterance_ids is not None:
            utterance_ids &= valid_utterance_ids
        counts[chapter_id] = len(utterance_ids)
    return counts
