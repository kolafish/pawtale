from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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


async def _load_plan_chapters(db: AsyncSession, plan: MemoirBookPlan) -> list[MemoirBookPlanChapter]:
    return (
        await db.execute(
            select(MemoirBookPlanChapter)
            .where(
                MemoirBookPlanChapter.project_id == plan.project_id,
                MemoirBookPlanChapter.plan_id == plan.id,
            )
            .order_by(MemoirBookPlanChapter.sort_order, MemoirBookPlanChapter.id)
        )
    ).scalars().all()


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


def _link_ref(
    link: MemoirChapterSourceLink,
    *,
    utterance: MemoirVoiceUtterance | None,
) -> dict[str, object] | None:
    data: dict[str, object] = {}
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
    links: list[MemoirChapterSourceLink],
    utterances: dict[str, MemoirVoiceUtterance],
) -> list[dict[str, object]]:
    ordered_refs: list[tuple[datetime, str, dict[str, object]]] = []

    for link in links:
        if link.status != MemoirChapterSourceLinkStatus.ACTIVE.value:
            continue
        ref = _link_ref(
            link,
            utterance=utterances.get(link.utterance_id),
        )
        if not ref:
            continue
        ordered_refs.append((
            link.created_at or datetime.min,
            link.id,
            ref,
        ))
    return [item[2] for item in sorted(ordered_refs, key=lambda item: (item[0], item[1]))]


def _link_change_state(
    *,
    chapter: MemoirBookPlanChapter,
    links: list[MemoirChapterSourceLink],
    latest_version: MemoirBookChapterDraftVersion | None,
    valid_utterance_ids: set[str] | None = None,
) -> ChapterSourceChangeState:
    if not links:
        return ChapterSourceChangeState(pending_source_ids=set(), removed_source_ids=set())

    if not latest_version:
        return ChapterSourceChangeState(pending_source_ids=set(), removed_source_ids=set())

    pending_source_ids: set[str] = set()
    removed_source_ids: set[str] = set()
    for link in links:
        if link.status == MemoirChapterSourceLinkStatus.ACTIVE.value:
            if link.merged_draft_version_id == latest_version.id:
                continue
            if valid_utterance_ids is None or link.utterance_id in valid_utterance_ids:
                pending_source_ids.add(link.utterance_id)
            continue

        if link.status != MemoirChapterSourceLinkStatus.REMOVED.value:
            continue
        if link.removed_at and link.removed_at <= latest_version.created_at:
            continue
        if link.merged_draft_version_id == latest_version.id and not link.removed_at:
            continue
        removed_source_ids.add(link.utterance_id)

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
    chapters = await _load_plan_chapters(db, plan)
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
    chapters = await _load_plan_chapters(db, plan)
    links_by_chapter = await _links_by_chapter(db, plan=plan)
    utterance_ids = {
        link.utterance_id
        for links in links_by_chapter.values()
        for link in links
        if link.status == MemoirChapterSourceLinkStatus.ACTIVE.value
    }
    utterances = await _utterances_by_id(db, project_id=plan.project_id, utterance_ids=utterance_ids)
    return {
        chapter.id: _source_refs_for_chapter(
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
    )
    if not plan:
        return []
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
    for chapter in await _load_plan_chapters(db, plan):
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
