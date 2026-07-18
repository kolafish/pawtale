from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import MemoirSectionStatus
from app.models.memoir import (
    MemoirBookChapterDraftVersion,
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirSection,
    MemoirSectionMaterial,
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


def source_ref_utterance_id(ref: object) -> str:
    if isinstance(ref, dict):
        value = ref.get("utterance_id")
    else:
        value = getattr(ref, "utterance_id", "")
    return value.strip() if isinstance(value, str) else ""


def source_ref_utterance_ids(refs: list[dict[str, object]]) -> list[str]:
    return [
        value
        for value in (source_ref_utterance_id(ref) for ref in refs)
        if value
    ]


def _material_ref(
    material: MemoirSectionMaterial,
    utterance: MemoirVoiceUtterance,
) -> dict[str, object]:
    text = ""
    if material.transcript_version is not None:
        text = (material.transcript_version.content or "").strip()
    if not text:
        text = (utterance.transcript_edited or utterance.transcript_raw or "").strip()
    ref: dict[str, object] = {
        "voice_session_id": utterance.voice_session_id,
        "message_id": utterance.chat_message_id or "",
        "utterance_id": utterance.id,
        "created_at": utterance.created_at,
        "quote": text,
    }
    if material.transcript_version_id:
        ref["transcript_version_id"] = material.transcript_version_id
    if utterance.audio_file_id:
        ref["audio_file_id"] = utterance.audio_file_id
    if utterance.start_ms is not None:
        ref["start_ms"] = utterance.start_ms
    if utterance.end_ms is not None:
        ref["end_ms"] = utterance.end_ms
    return ref


async def _plan_chapters(
    db: AsyncSession,
    plan: MemoirBookPlan,
) -> list[MemoirBookPlanChapter]:
    return list(
        (
            await db.scalars(
                select(MemoirBookPlanChapter)
                .where(
                    MemoirBookPlanChapter.plan_id == plan.id,
                    MemoirBookPlanChapter.project_id == plan.project_id,
                )
                .order_by(
                    MemoirBookPlanChapter.sort_order,
                    MemoirBookPlanChapter.id,
                )
            )
        ).all()
    )


async def plan_source_ref_dicts_by_chapter(
    db: AsyncSession,
    plan: MemoirBookPlan | None,
) -> dict[str, list[dict[str, object]]]:
    if plan is None:
        return {}
    rows = (
        await db.execute(
            select(MemoirBookPlanChapter.id, MemoirSectionMaterial, MemoirVoiceUtterance)
            .options(selectinload(MemoirSectionMaterial.transcript_version))
            .join(MemoirSection, MemoirSection.chapter_id == MemoirBookPlanChapter.id)
            .join(
                MemoirSectionMaterial,
                MemoirSectionMaterial.section_id == MemoirSection.id,
            )
            .join(
                MemoirVoiceUtterance,
                MemoirVoiceUtterance.id == MemoirSectionMaterial.utterance_id,
            )
            .where(
                MemoirBookPlanChapter.plan_id == plan.id,
                MemoirBookPlanChapter.project_id == plan.project_id,
                MemoirSectionMaterial.project_id == plan.project_id,
                MemoirSection.status == MemoirSectionStatus.ACTIVE.value,
                MemoirSection.include_in_book.is_(True),
                MemoirSectionMaterial.include_in_book.is_(True),
            )
            .order_by(
                MemoirBookPlanChapter.sort_order,
                MemoirSection.sort_order,
                MemoirSectionMaterial.sort_order,
                MemoirSectionMaterial.id,
            )
        )
    ).all()
    grouped: dict[str, list[dict[str, object]]] = {
        chapter.id: [] for chapter in await _plan_chapters(db, plan)
    }
    for chapter_id, material, utterance in rows:
        grouped.setdefault(chapter_id, []).append(_material_ref(material, utterance))
    return grouped


async def chapter_source_ref_dicts(
    db: AsyncSession,
    *,
    chapter: MemoirBookPlanChapter,
) -> list[dict[str, object]]:
    plan = await db.get(MemoirBookPlan, chapter.plan_id)
    return (await plan_source_ref_dicts_by_chapter(db, plan)).get(chapter.id, [])


async def covered_utterance_ids(
    db: AsyncSession,
    plan: MemoirBookPlan | None,
) -> set[str]:
    refs = await plan_source_ref_dicts_by_chapter(db, plan)
    return {
        utterance_id
        for chapter_refs in refs.values()
        for utterance_id in source_ref_utterance_ids(chapter_refs)
    }


async def included_chapters_by_utterance(
    db: AsyncSession,
    plan: MemoirBookPlan | None,
) -> dict[str, list[MemoirBookSourceIncludedChapter]]:
    if plan is None:
        return {}
    refs = await plan_source_ref_dicts_by_chapter(db, plan)
    result: dict[str, list[MemoirBookSourceIncludedChapter]] = {}
    for chapter in await _plan_chapters(db, plan):
        summary = MemoirBookSourceIncludedChapter(
            chapter_id=chapter.id,
            title=chapter.title,
            sort_order=chapter.sort_order,
            include_in_book=chapter.include_in_book,
        )
        for utterance_id in source_ref_utterance_ids(refs.get(chapter.id, [])):
            result.setdefault(utterance_id, []).append(summary)
    return result


async def source_count_by_chapter(
    db: AsyncSession,
    plan: MemoirBookPlan | None,
    *,
    valid_utterance_ids: set[str] | None = None,
) -> dict[str, int]:
    refs = await plan_source_ref_dicts_by_chapter(db, plan)
    result: dict[str, int] = {}
    for chapter_id, chapter_refs in refs.items():
        ids = set(source_ref_utterance_ids(chapter_refs))
        if valid_utterance_ids is not None:
            ids &= valid_utterance_ids
        result[chapter_id] = len(ids)
    return result


async def _latest_draft_versions(
    db: AsyncSession,
    *,
    project_id: str,
) -> dict[str, MemoirBookChapterDraftVersion]:
    rows = (
        await db.scalars(
            select(MemoirBookChapterDraftVersion)
            .where(MemoirBookChapterDraftVersion.project_id == project_id)
            .order_by(
                MemoirBookChapterDraftVersion.chapter_id,
                MemoirBookChapterDraftVersion.version,
                MemoirBookChapterDraftVersion.created_at,
            )
        )
    ).all()
    latest: dict[str, MemoirBookChapterDraftVersion] = {}
    for row in rows:
        latest[row.chapter_id] = row
    return latest


def _snapshot_material_ids(version: MemoirBookChapterDraftVersion) -> set[str]:
    result: set[str] = set()
    for item in version.material_snapshot_json or []:
        if not isinstance(item, dict):
            continue
        value = item.get("material_id")
        if isinstance(value, str) and value.strip():
            result.add(value.strip())
    return result


async def chapter_source_change_states_by_chapter(
    db: AsyncSession,
    plan: MemoirBookPlan | None,
    *,
    valid_utterance_ids: set[str] | None = None,
) -> dict[str, ChapterSourceChangeState]:
    if plan is None:
        return {}
    refs = await plan_source_ref_dicts_by_chapter(db, plan)
    versions = await _latest_draft_versions(db, project_id=plan.project_id)
    result: dict[str, ChapterSourceChangeState] = {}
    for chapter in await _plan_chapters(db, plan):
        current = set(source_ref_utterance_ids(refs.get(chapter.id, [])))
        if valid_utterance_ids is not None:
            current &= valid_utterance_ids
        version = versions.get(chapter.id)
        if version is None:
            result[chapter.id] = ChapterSourceChangeState(set(), set())
            continue
        snapshot = _snapshot_material_ids(version)
        result[chapter.id] = ChapterSourceChangeState(
            pending_source_ids=current - snapshot,
            removed_source_ids=snapshot - current,
        )
    return result


async def chapter_source_change_state(
    db: AsyncSession,
    *,
    chapter: MemoirBookPlanChapter,
    valid_utterance_ids: set[str] | None = None,
) -> ChapterSourceChangeState:
    plan = await db.get(MemoirBookPlan, chapter.plan_id)
    return (
        await chapter_source_change_states_by_chapter(
            db,
            plan,
            valid_utterance_ids=valid_utterance_ids,
        )
    ).get(chapter.id, ChapterSourceChangeState(set(), set()))
