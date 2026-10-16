from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.clock import utcnow_naive
from app.domain.memoir.limits import MAX_MEMOIR_BOOK_PLAN_CHAPTERS
from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirCreatedBy,
    MemoirSectionMaterialAssignedBy,
    MemoirSectionStatus,
    MemoirSectionType,
)
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirBookOutput,
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirSection,
    MemoirSectionMaterial,
    MemoirTranscriptVersion,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.schemas.memoir_sections import (
    MemoirChapterSectionMaterialSummary,
    MemoirChapterSectionSummary,
    MemoirSectionCollectionResponse,
    MemoirSectionCreateRequest,
    MemoirSectionMaterialAssignmentRequest,
    MemoirSectionMaterialMoveRequest,
    MemoirSectionMaterialPlacementRequest,
    MemoirSectionMaterialPlacementResponse,
    MemoirSectionMaterialResponse,
    MemoirSectionMergeRequest,
    MemoirSectionReorderRequest,
    MemoirSectionResponse,
    MemoirSectionSplitRequest,
    MemoirSectionUpdateRequest,
)
from app.services.memoir.access import load_memoir_project
from app.services.memoir.book_output_state import invalidate_book_output
from app.services.memoir.material_roles import (
    is_countable_material,
    narrator_material_clause,
)
from app.services.memoir.section_digests import mark_section_digests_stale
from app.services.memoir.section_roles import requires_user_assignment
from app.services.memoir.transcript_projection import (
    current_material_text, current_transcript_version_id, current_utterance_text, source_text_hash,
)


@dataclass(frozen=True)
class SectionChanges:
    section_ids: frozenset[str]
    chapter_ids: frozenset[str]


async def finalize_section_changes(
    db: AsyncSession,
    *,
    project_id: str,
    section_ids: set[str],
    source: str = "materials",
    settled: bool = False,
) -> None:
    """Invalidate changed section inputs and schedule once in the caller's transaction."""
    await mark_section_digests_stale(db, project_id=project_id, section_ids=section_ids)
    from app.services.memoir.automatic_book_draft import schedule_book_draft
    await schedule_book_draft(db, project_id=project_id, source=source, settled=settled)


async def _load_plan(
    db: AsyncSession,
    *,
    project_id: str,
) -> MemoirBookPlan:
    plan = await db.scalar(
        select(MemoirBookPlan).where(MemoirBookPlan.project_id == project_id)
    )
    if plan is None:
        raise HTTPException(status_code=404, detail="章节规划不存在")
    return plan


async def lock_project_chapters(db: AsyncSession, *, project_id: str) -> None:
    # All directory writers take plan -> chapters, matching section moves and
    # the end-of-narration review. Reversing these locks can deadlock a rename.
    await db.execute(select(MemoirBookPlan.id).where(
        MemoirBookPlan.project_id == project_id,
    ).with_for_update())
    await db.execute(select(MemoirBookPlanChapter.id).where(
        MemoirBookPlanChapter.project_id == project_id,
    ).order_by(MemoirBookPlanChapter.id).with_for_update())


async def lock_plan_structure(
    db: AsyncSession,
    *,
    project_id: str,
) -> MemoirBookPlan:
    """Lock the plan before a write that also locks chapters."""

    plan = await db.scalar(
        select(MemoirBookPlan)
        .where(MemoirBookPlan.project_id == project_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if plan is None:
        raise HTTPException(status_code=404, detail="章节规划不存在")
    return plan


async def _load_chapter(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> MemoirBookPlanChapter:
    chapter = await db.scalar(
        select(MemoirBookPlanChapter).where(
            MemoirBookPlanChapter.project_id == project_id,
            MemoirBookPlanChapter.id == chapter_id,
        )
    )
    if chapter is None:
        raise HTTPException(status_code=404, detail="章节不存在")
    return chapter


async def lock_chapter_structure(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> MemoirBookPlanChapter:
    """Serialize short section-structure writes within one chapter."""

    chapter = await db.scalar(
        select(MemoirBookPlanChapter)
        .where(
            MemoirBookPlanChapter.project_id == project_id,
            MemoirBookPlanChapter.id == chapter_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if chapter is None:
        raise HTTPException(status_code=404, detail="章节不存在")
    return chapter


def _section_load_options():
    return (
        selectinload(MemoirSection.materials).selectinload(
            MemoirSectionMaterial.utterance
        ),
        selectinload(MemoirSection.materials).selectinload(
            MemoirSectionMaterial.transcript_version
        ),
    )


async def _load_section(
    db: AsyncSession,
    *,
    project_id: str,
    section_id: str,
) -> MemoirSection:
    section = await db.scalar(
        select(MemoirSection)
        .where(
            MemoirSection.project_id == project_id,
            MemoirSection.id == section_id,
        )
        .options(*_section_load_options())
        .execution_options(populate_existing=True)
    )
    if section is None:
        raise HTTPException(status_code=404, detail="小章节不存在")
    return section


async def _load_sections(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str | None = None,
) -> list[MemoirSection]:
    query = (
        select(MemoirSection)
        .join(
            MemoirBookPlanChapter,
            MemoirSection.chapter_id == MemoirBookPlanChapter.id,
        )
        .where(MemoirSection.project_id == project_id)
        .options(*_section_load_options())
        .execution_options(populate_existing=True)
        .order_by(
            MemoirBookPlanChapter.sort_order,
            MemoirSection.sort_order,
            MemoirSection.id,
        )
    )
    if chapter_id is not None:
        query = query.where(MemoirSection.chapter_id == chapter_id)
    return list((await db.execute(query)).scalars().unique().all())


async def _chapter_titles(
    db: AsyncSession,
    *,
    project_id: str,
) -> dict[str, str]:
    rows = (
        await db.execute(
            select(MemoirBookPlanChapter.id, MemoirBookPlanChapter.title).where(
                MemoirBookPlanChapter.project_id == project_id
            )
        )
    ).all()
    return {chapter_id: title for chapter_id, title in rows}


def _section_response(
    section: MemoirSection,
    *,
    chapter_title: str,
) -> MemoirSectionResponse:
    materials = sorted(section.materials, key=lambda item: (item.sort_order, item.id))
    return MemoirSectionResponse(
        id=section.id,
        project_id=section.project_id,
        chapter_id=section.chapter_id,
        chapter_title=chapter_title,
        title=section.title,
        section_type=section.section_type,
        sort_order=section.sort_order,
        status=section.status,
        include_in_book=section.include_in_book,
        requires_user_assignment=requires_user_assignment(
            section.metadata_json
        ),
        version=section.version,
        materials=[
            MemoirSectionMaterialResponse(
                id=material.id,
                utterance_id=material.utterance_id,
                transcript_version_id=material.transcript_version_id,
                sort_order=material.sort_order,
                text=current_material_text(material),
                include_in_book=material.include_in_book,
                assigned_by=material.assigned_by,
                assignment_confidence=material.assignment_confidence,
                assignment_reason=material.assignment_reason,
                created_at=material.created_at,
                updated_at=material.updated_at,
            )
            for material in materials
        ],
        created_at=section.created_at,
        updated_at=section.updated_at,
    )


def _section_summary(section: MemoirSection) -> MemoirChapterSectionSummary:
    materials = sorted(
        section.materials,
        key=lambda item: (item.sort_order, item.id),
    )
    return MemoirChapterSectionSummary(
        id=section.id,
        title=section.title,
        section_type=section.section_type,
        sort_order=section.sort_order,
        status=section.status,
        include_in_book=section.include_in_book,
        requires_user_assignment=requires_user_assignment(
            section.metadata_json
        ),
        version=section.version,
        material_count=len(materials),
        included_material_count=sum(
            1 for material in materials if material.include_in_book
        ),
        materials=[
            MemoirChapterSectionMaterialSummary(
                utterance_id=material.utterance_id,
                sort_order=material.sort_order,
                text=current_material_text(material),
                include_in_book=material.include_in_book,
            )
            for material in materials
        ],
    )


async def pending_assignment_material_count(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> int:
    section_rows = (
        await db.execute(
            select(MemoirSection.id, MemoirSection.metadata_json).where(
                MemoirSection.project_id == project_id,
                MemoirSection.chapter_id == chapter_id,
            )
        )
    ).all()
    pending_section_ids = [
        section_id
        for section_id, metadata_json in section_rows
        if requires_user_assignment(metadata_json)
    ]
    if not pending_section_ids:
        return 0
    return int(
        await db.scalar(
            select(func.count())
            .select_from(MemoirSectionMaterial)
            .where(
                MemoirSectionMaterial.project_id == project_id,
                MemoirSectionMaterial.section_id.in_(pending_section_ids),
            )
        )
        or 0
    )


async def section_summaries_by_chapter(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str | None = None,
) -> dict[str, list[MemoirChapterSectionSummary]]:
    result: dict[str, list[MemoirChapterSectionSummary]] = {}
    for section in await _load_sections(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    ):
        result.setdefault(section.chapter_id, []).append(_section_summary(section))
    return result


async def _unassigned_material_count(
    db: AsyncSession,
    *,
    project_id: str,
) -> int:
    assigned = select(MemoirSectionMaterial.utterance_id).where(
        MemoirSectionMaterial.project_id == project_id
    )
    return int(
        await db.scalar(
            select(func.count())
            .select_from(MemoirVoiceUtterance)
            .join(
                MemoirVoiceSession,
                MemoirVoiceUtterance.voice_session_id == MemoirVoiceSession.id,
            )
            .where(
                MemoirVoiceSession.project_id == project_id,
                ~MemoirVoiceUtterance.id.in_(assigned),
                narrator_material_clause(),
            )
        )
        or 0
    )


async def _collection_response(
    db: AsyncSession,
    *,
    project_id: str,
) -> MemoirSectionCollectionResponse:
    sections = await _load_sections(db, project_id=project_id)
    titles = await _chapter_titles(db, project_id=project_id)
    return MemoirSectionCollectionResponse(
        project_id=project_id,
        sections=[
            _section_response(
                section,
                chapter_title=titles.get(section.chapter_id, ""),
            )
            for section in sections
        ],
        unassigned_material_count=await _unassigned_material_count(
            db,
            project_id=project_id,
        ),
    )


async def list_sections(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    allow_admin: bool = False,
) -> MemoirSectionCollectionResponse:
    await load_memoir_project(
        db,
        project_id,
        user_id,
        allow_admin=allow_admin,
    )
    return await _collection_response(db, project_id=project_id)


async def _set_section_order(
    db: AsyncSession,
    *,
    sections: list[MemoirSection],
) -> set[str]:
    original_orders = {section.id: section.sort_order for section in sections}
    for index, section in enumerate(sections):
        section.sort_order = -(index + 1)
        db.add(section)
    await db.flush()
    now = utcnow_naive()
    changed_ids: set[str] = set()
    for index, section in enumerate(sections):
        if original_orders[section.id] != index:
            section.version += 1
            changed_ids.add(section.id)
        section.sort_order = index
        section.updated_at = now
        db.add(section)
    await db.flush()
    return changed_ids


async def _set_material_order(
    db: AsyncSession,
    *,
    materials: list[MemoirSectionMaterial],
) -> None:
    for index, material in enumerate(materials):
        material.sort_order = 1_000_000 + index
        db.add(material)
    await db.flush()
    now = utcnow_naive()
    for index, material in enumerate(materials):
        material.sort_order = index
        material.updated_at = now
        db.add(material)
    await db.flush()


async def create_section(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    chapter_id: str,
    req: MemoirSectionCreateRequest,
    allow_admin: bool = False,
) -> MemoirSectionCollectionResponse:
    await load_memoir_project(
        db,
        project_id,
        user_id,
        allow_admin=allow_admin,
    )
    await lock_chapter_structure(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    sections = await _load_sections(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    section = MemoirSection(
        project_id=project_id,
        chapter_id=chapter_id,
        title=req.title,
        metadata_json={"title_updated_by": "user"},
        section_type=req.section_type.value,
        sort_order=max((item.sort_order for item in sections), default=-1) + 1,
    )
    db.add(section)
    await db.flush()
    insert_at = (
        len(sections)
        if req.sort_order is None
        else min(req.sort_order, len(sections))
    )
    sections.insert(insert_at, section)
    changed_ids = await _set_section_order(db, sections=sections)
    if changed_ids:
        await finalize_section_changes(db, project_id=project_id, section_ids=changed_ids)
    await db.commit()
    return await _collection_response(db, project_id=project_id)


async def update_section(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    section_id: str,
    req: MemoirSectionUpdateRequest,
    allow_admin: bool = False,
    commit: bool = True,
) -> MemoirSectionCollectionResponse:
    await load_memoir_project(
        db,
        project_id,
        user_id,
        allow_admin=allow_admin,
    )
    await lock_plan_structure(db, project_id=project_id)
    section = await _load_section(
        db,
        project_id=project_id,
        section_id=section_id,
    )
    if section.version != req.expected_version:
        raise HTTPException(status_code=409, detail="小章节已经发生变化，请刷新后重试")
    changed = False
    for field, value in (
        ("title", req.title),
        (
            "section_type",
            req.section_type.value if req.section_type is not None else None,
        ),
        ("status", req.status.value if req.status is not None else None),
        ("include_in_book", req.include_in_book),
    ):
        if value is not None and getattr(section, field) != value:
            if field == "title":
                section.metadata_json = {**section.metadata_json, "title_updated_by": "user", "previous_title": section.title}
            setattr(section, field, value)
            changed = True
    if changed:
        section.version += 1
        section.updated_at = utcnow_naive()
        db.add(section)
        await finalize_section_changes(
            db,
            project_id=project_id,
            section_ids={section.id},
        )
        await (db.commit() if commit else db.flush())
    return await _collection_response(db, project_id=project_id)


async def delete_section(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    section_id: str,
    commit: bool = True,
) -> None:
    await load_memoir_project(db, project_id, user_id)
    section = await _load_section(db, project_id=project_id, section_id=section_id)
    chapter = await lock_chapter_structure(
        db,
        project_id=project_id,
        chapter_id=section.chapter_id,
    )
    section = await _load_section(
        db,
        project_id=project_id,
        section_id=section_id,
    )
    await db.delete(section)
    chapter.updated_at = utcnow_naive()
    await db.flush()
    remaining = await _load_sections(
        db,
        project_id=project_id,
        chapter_id=chapter.id,
    )
    changed_ids = {section.id} | await _set_section_order(db, sections=remaining)
    draft = await db.scalar(select(MemoirBookChapterDraft).where(MemoirBookChapterDraft.chapter_id == chapter.id))
    if draft:
        draft.status = MemoirBookChapterDraftStatus.DRAFT.value
        draft.metadata_json = {**(draft.metadata_json or {}), "status_reason": "materials_deleted"}
    output = await db.scalar(select(MemoirBookOutput).where(MemoirBookOutput.project_id == project_id))
    if output:
        invalidate_book_output(output)
    await finalize_section_changes(db, project_id=project_id, section_ids=changed_ids, source="structure")
    await (db.commit() if commit else db.flush())


async def reorder_sections(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    chapter_id: str,
    req: MemoirSectionReorderRequest,
    allow_admin: bool = False,
) -> MemoirSectionCollectionResponse:
    await load_memoir_project(
        db,
        project_id,
        user_id,
        allow_admin=allow_admin,
    )
    await lock_chapter_structure(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    sections = await _load_sections(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    by_id = {section.id: section for section in sections}
    if set(req.section_ids) != set(by_id):
        raise HTTPException(
            status_code=400,
            detail="section_ids 必须包含本章全部小章节",
        )
    ordered = [by_id[section_id] for section_id in req.section_ids]
    changed_ids = await _set_section_order(db, sections=ordered)
    if changed_ids:
        await finalize_section_changes(db, project_id=project_id, section_ids=changed_ids)
    await db.commit()
    return await _collection_response(db, project_id=project_id)


async def _load_project_utterances(
    db: AsyncSession,
    *,
    project_id: str,
    utterance_ids: list[str],
) -> dict[str, MemoirVoiceUtterance]:
    utterances = list(
        (
            await db.execute(
                select(MemoirVoiceUtterance)
                .join(
                    MemoirVoiceSession,
                    MemoirVoiceUtterance.voice_session_id
                    == MemoirVoiceSession.id,
                )
                .where(
                    MemoirVoiceSession.project_id == project_id,
                    MemoirVoiceUtterance.id.in_(utterance_ids),
                )
                .options(selectinload(MemoirVoiceUtterance.chat_message))
                .order_by(MemoirVoiceUtterance.id)
                .with_for_update(of=MemoirVoiceUtterance)
                .execution_options(populate_existing=True)
            )
        ).scalars().all()
    )
    by_id = {utterance.id: utterance for utterance in utterances}
    if set(by_id) != set(utterance_ids):
        raise HTTPException(status_code=404, detail="讲述素材不存在")
    if any(not is_countable_material(utterance) for utterance in utterances):
        raise HTTPException(
            status_code=409,
            detail="只有已有文字的叙述者素材可以归入小章节",
        )
    return by_id


async def _latest_transcript_versions(
    db: AsyncSession,
    *,
    utterance_ids: list[str],
) -> dict[str, MemoirTranscriptVersion]:
    versions = list(
        (
            await db.execute(
                select(MemoirTranscriptVersion)
                .where(MemoirTranscriptVersion.source_artifact_id.in_(utterance_ids))
                .order_by(
                    MemoirTranscriptVersion.source_artifact_id,
                    MemoirTranscriptVersion.created_at,
                    MemoirTranscriptVersion.id,
                )
            )
        ).scalars().all()
    )
    latest: dict[str, MemoirTranscriptVersion] = {}
    for version in versions:
        if version.source_artifact_id:
            latest[version.source_artifact_id] = version
    return latest


async def _lock_material_move_chapters(
    db: AsyncSession,
    *,
    project_id: str,
    target_chapter_id: str | None,
    utterance_ids: list[str],
) -> tuple[
    dict[str, tuple[str, str]],
    dict[str, MemoirBookPlanChapter],
]:
    rows = (
        await db.execute(
            select(
                MemoirSectionMaterial.utterance_id,
                MemoirSectionMaterial.id,
                MemoirSectionMaterial.section_id,
                MemoirSection.chapter_id,
            )
            .join(
                MemoirSection,
                MemoirSection.id == MemoirSectionMaterial.section_id,
            )
            .where(
                MemoirSectionMaterial.project_id == project_id,
                MemoirSectionMaterial.utterance_id.in_(utterance_ids),
            )
        )
    ).all()
    membership_snapshot = {
        utterance_id: (material_id, section_id)
        for utterance_id, material_id, section_id, _chapter_id in rows
    }
    chapter_ids = {
        chapter_id
        for _utterance_id, _material_id, _section_id, chapter_id in rows
    }
    if target_chapter_id is not None:
        chapter_ids.add(target_chapter_id)
    locked_chapters: dict[str, MemoirBookPlanChapter] = {}
    for chapter_id in sorted(chapter_ids):
        locked_chapters[chapter_id] = await lock_chapter_structure(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
        )
    return membership_snapshot, locked_chapters


async def _move_materials(
    db: AsyncSession,
    *,
    project_id: str,
    target: MemoirSection,
    utterance_ids: list[str],
    target_sort_order: int | None = None,
    assigned_by: MemoirSectionMaterialAssignedBy,
    assignment_reason: str,
) -> set[str]:
    membership_snapshot, _locked_chapters = await _lock_material_move_chapters(
        db,
        project_id=project_id,
        target_chapter_id=target.chapter_id,
        utterance_ids=utterance_ids,
    )
    target = await _load_section(
        db,
        project_id=project_id,
        section_id=target.id,
    )
    await _load_project_utterances(
        db,
        project_id=project_id,
        utterance_ids=utterance_ids,
    )
    existing = list(
        (
            await db.execute(
                select(MemoirSectionMaterial)
                .where(
                    MemoirSectionMaterial.project_id == project_id,
                    MemoirSectionMaterial.utterance_id.in_(utterance_ids),
                )
                .execution_options(populate_existing=True)
            )
        ).scalars().all()
    )
    if {
        item.utterance_id: (item.id, item.section_id)
        for item in existing
    } != membership_snapshot:
        raise HTTPException(status_code=409, detail="素材归属已变化，请刷新后重试")
    existing_by_utterance = {item.utterance_id: item for item in existing}
    affected_section_ids = {
        target.id,
        *(item.section_id for item in existing),
    }
    all_affected = list(
        (
            await db.execute(
                select(MemoirSectionMaterial)
                .where(
                    MemoirSectionMaterial.project_id == project_id,
                    MemoirSectionMaterial.section_id.in_(affected_section_ids),
                )
                .order_by(
                    MemoirSectionMaterial.section_id,
                    MemoirSectionMaterial.sort_order,
                    MemoirSectionMaterial.id,
                )
            )
        ).scalars().all()
    )
    by_section: dict[str, list[MemoirSectionMaterial]] = {
        section_id: [] for section_id in affected_section_ids
    }
    moving_ids = set(utterance_ids)
    for material in all_affected:
        if material.utterance_id not in moving_ids:
            by_section[material.section_id].append(material)

    for index, material in enumerate(all_affected):
        material.sort_order = -(index + 1)
        db.add(material)
    await db.flush()

    latest_versions = await _latest_transcript_versions(
        db,
        utterance_ids=utterance_ids,
    )
    moving: list[MemoirSectionMaterial] = []
    for index, utterance_id in enumerate(utterance_ids):
        material = existing_by_utterance.get(utterance_id)
        if material is None:
            version = latest_versions.get(utterance_id)
            material = MemoirSectionMaterial(
                project_id=project_id,
                section_id=target.id,
                utterance_id=utterance_id,
                transcript_version_id=version.id if version else None,
                sort_order=-(100_000 + index),
                assigned_by=assigned_by.value,
                assignment_reason=assignment_reason,
            )
        else:
            material.section_id = target.id
            material.sort_order = -(100_000 + index)
            material.assigned_by = assigned_by.value
            material.assignment_reason = assignment_reason
        db.add(material)
        moving.append(material)
    await db.flush()

    target_materials = by_section[target.id]
    insert_at = (
        len(target_materials)
        if target_sort_order is None
        else min(target_sort_order, len(target_materials))
    )
    target_materials[insert_at:insert_at] = moving
    by_section[target.id] = target_materials

    for materials in by_section.values():
        await _set_material_order(db, materials=materials)

    sections = list(
        (
            await db.execute(
                select(MemoirSection).where(
                    MemoirSection.project_id == project_id,
                    MemoirSection.id.in_(affected_section_ids),
                )
            )
        ).scalars().all()
    )
    now = utcnow_naive()
    for section in sections:
        section.version += 1
        section.updated_at = now
        db.add(section)
    await db.flush()
    return affected_section_ids


async def assign_material_to_section(
    db: AsyncSession,
    *,
    project_id: str,
    section_id: str,
    utterance_id: str,
    assigned_by: MemoirSectionMaterialAssignedBy,
    assignment_reason: str,
) -> tuple[MemoirSectionMaterial, set[str]]:
    target = await _load_section(
        db,
        project_id=project_id,
        section_id=section_id,
    )
    changed_ids = await _move_materials(
        db,
        project_id=project_id,
        target=target,
        utterance_ids=[utterance_id],
        assigned_by=assigned_by,
        assignment_reason=assignment_reason,
    )
    material = await db.scalar(
        select(MemoirSectionMaterial).where(
            MemoirSectionMaterial.project_id == project_id,
            MemoirSectionMaterial.utterance_id == utterance_id,
        )
    )
    if material is None:
        raise RuntimeError("讲述素材归入小章节失败")
    return material, changed_ids


async def refresh_material_transcript_version(
    db: AsyncSession,
    *,
    project_id: str,
    utterance_id: str,
    transcript_version_id: str,
) -> MemoirSectionMaterial | None:
    material = await db.scalar(
        select(MemoirSectionMaterial).where(
            MemoirSectionMaterial.project_id == project_id,
            MemoirSectionMaterial.utterance_id == utterance_id,
        )
    )
    if material is None:
        return None
    valid_version_id = await db.scalar(
        select(MemoirTranscriptVersion.id)
        .join(
            MemoirVoiceSession,
            MemoirVoiceSession.id
            == MemoirTranscriptVersion.voice_session_id,
        )
        .where(
            MemoirTranscriptVersion.id == transcript_version_id,
            MemoirTranscriptVersion.source_artifact_id == utterance_id,
            MemoirVoiceSession.project_id == project_id,
        )
    )
    if valid_version_id is None:
        raise HTTPException(
            status_code=409,
            detail="正文文本版本不属于当前讲述素材",
        )
    if material.transcript_version_id == transcript_version_id:
        return material
    material.transcript_version_id = transcript_version_id
    material.updated_at = utcnow_naive()
    db.add(material)
    await finalize_section_changes(
        db,
        project_id=project_id,
        section_ids={material.section_id},
    )
    await db.flush()
    return material


async def assign_materials(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    section_id: str,
    req: MemoirSectionMaterialAssignmentRequest,
    allow_admin: bool = False,
) -> MemoirSectionCollectionResponse:
    await load_memoir_project(
        db,
        project_id,
        user_id,
        allow_admin=allow_admin,
    )
    target = await _load_section(
        db,
        project_id=project_id,
        section_id=section_id,
    )
    changed_ids = await _move_materials(
        db,
        project_id=project_id,
        target=target,
        utterance_ids=req.utterance_ids,
        assigned_by=MemoirSectionMaterialAssignedBy.USER,
        assignment_reason="manual_assignment",
    )
    await finalize_section_changes(db, project_id=project_id, section_ids=changed_ids)
    await db.commit()
    return await _collection_response(db, project_id=project_id)


async def move_material(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    utterance_id: str,
    req: MemoirSectionMaterialMoveRequest,
    allow_admin: bool = False,
) -> MemoirSectionCollectionResponse:
    await load_memoir_project(
        db,
        project_id,
        user_id,
        allow_admin=allow_admin,
    )
    target = await _load_section(
        db,
        project_id=project_id,
        section_id=req.target_section_id,
    )
    changed_ids = await _move_materials(
        db,
        project_id=project_id,
        target=target,
        utterance_ids=[utterance_id],
        target_sort_order=req.target_sort_order,
        assigned_by=MemoirSectionMaterialAssignedBy.USER,
        assignment_reason="manual_move",
    )
    await finalize_section_changes(db, project_id=project_id, section_ids=changed_ids)
    await db.commit()
    return await _collection_response(db, project_id=project_id)


async def _create_section_for_placement(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    title: str,
) -> tuple[MemoirSection, set[str]]:
    await lock_chapter_structure(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    sections = await _load_sections(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    section = MemoirSection(
        project_id=project_id,
        chapter_id=chapter_id,
        title=title,
        section_type=MemoirSectionType.EVENT.value,
        sort_order=max((item.sort_order for item in sections), default=-1) + 1,
    )
    db.add(section)
    await db.flush()
    insert_at = next(
        (
            index
            for index, existing in enumerate(sections)
            if requires_user_assignment(existing.metadata_json)
        ),
        len(sections),
    )
    sections.insert(insert_at, section)
    changed_ids = await _set_section_order(db, sections=sections)
    return section, changed_ids


async def mark_section_structure_changed(
    db: AsyncSession,
    *,
    project_id: str,
    plan: MemoirBookPlan,
    chapter_ids: set[str],
    section_ids: set[str],
    operation: str,
    updated_by: MemoirCreatedBy = MemoirCreatedBy.USER,
    source: str = "materials",
    settled: bool = False,
) -> None:
    drafts = list(
        (
            await db.scalars(
                select(MemoirBookChapterDraft).where(
                    MemoirBookChapterDraft.project_id == project_id,
                    MemoirBookChapterDraft.chapter_id.in_(chapter_ids),
                )
            )
        ).all()
    )
    for draft in drafts:
        draft.status = MemoirBookChapterDraftStatus.DRAFT.value
        draft.metadata_json = {
            **(draft.metadata_json or {}),
            "status_reason": "section_structure_changed",
        }
        db.add(draft)

    output = await db.scalar(
        select(MemoirBookOutput).where(MemoirBookOutput.project_id == project_id)
    )
    if output is not None:
        invalidate_book_output(output)
        db.add(output)

    now = datetime.now(UTC).isoformat()
    plan.created_by = updated_by.value
    plan.metadata_json = {
        **(plan.metadata_json or {}),
        "structure_updated_at": now,
        "structure_updated_by": updated_by.value,
        "structure_operation": operation,
    }
    db.add(plan)

    await finalize_section_changes(
        db, project_id=project_id, section_ids=section_ids, source=source, settled=settled,
    )


async def place_material(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    utterance_id: str,
    req: MemoirSectionMaterialPlacementRequest,
    allow_admin: bool = False,
    assigned_by: MemoirSectionMaterialAssignedBy = MemoirSectionMaterialAssignedBy.USER,
    structure_updated_by: MemoirCreatedBy = MemoirCreatedBy.USER,
    assignment_reason: str | None = None,
    expected_source: tuple[str, str | None] | None = None,
    expected_section_version: int | None = None,
    expected_chapter_updated_at: datetime | None = None,
    commit: bool = True,
) -> MemoirSectionMaterialPlacementResponse:
    await load_memoir_project(
        db,
        project_id,
        user_id,
        allow_admin=allow_admin,
    )
    plan = await lock_plan_structure(db, project_id=project_id)
    target: MemoirSection | None = None
    target_chapter_id: str | None = None
    if req.target_section_id:
        target = await _load_section(
            db,
            project_id=project_id,
            section_id=req.target_section_id,
        )
        target_chapter_id = target.chapter_id
    elif req.target_chapter_id:
        target_chapter_id = req.target_chapter_id
    membership_snapshot, locked_chapters = await _lock_material_move_chapters(
        db,
        project_id=project_id,
        target_chapter_id=target_chapter_id,
        utterance_ids=[utterance_id],
    )
    locked_target_chapter = locked_chapters.get(target_chapter_id or "")
    if req.target_section_id:
        target = await _load_section(
            db,
            project_id=project_id,
            section_id=req.target_section_id,
        )

    # Structural writers use one lock order: plan, chapters, then utterances.
    # Merge and regeneration use the same order, avoiding circular waits.
    utterances = await _load_project_utterances(
        db,
        project_id=project_id,
        utterance_ids=[utterance_id],
    )
    current_material = await db.scalar(
        select(MemoirSectionMaterial).where(
            MemoirSectionMaterial.project_id == project_id,
            MemoirSectionMaterial.utterance_id == utterance_id,
        ).execution_options(populate_existing=True)
    )
    current_membership = (
        (current_material.id, current_material.section_id)
        if current_material is not None
        else None
    )
    if current_membership != membership_snapshot.get(utterance_id):
        raise HTTPException(status_code=409, detail="素材归属已变化，请刷新后重试")
    if expected_source is not None:
        utterance = utterances[utterance_id]
        source = (source_text_hash(current_utterance_text(utterance)), current_transcript_version_id(utterance))
        if current_material is not None or source != expected_source:
            raise HTTPException(status_code=409, detail="素材文字或归属已变化，已放弃过时的自动归类")
    current_section = (
        await _load_section(
            db,
            project_id=project_id,
            section_id=current_material.section_id,
        )
        if current_material is not None
        else None
    )

    if expected_section_version is not None and target is not None:
        await db.refresh(target, with_for_update=True)
        if target.version != expected_section_version:
            raise HTTPException(status_code=409, detail="目标小节已变化，已放弃过时的自动归类")
    if expected_chapter_updated_at is not None and target_chapter_id is not None:
        if (
            locked_target_chapter is None
            or locked_target_chapter.updated_at != expected_chapter_updated_at
        ):
            raise HTTPException(status_code=409, detail="目标章节已变化，已放弃过时的自动归类")

    moves_included_source = bool(
        current_material is not None
        and current_material.include_in_book
        and current_section is not None
        and current_section.status == MemoirSectionStatus.ACTIVE.value
        and current_section.include_in_book
    )
    if moves_included_source and current_section is not None and (
        target_chapter_id is None
        or current_section.chapter_id != target_chapter_id
    ):
        remaining_material_count = await db.scalar(
            select(func.count(MemoirSectionMaterial.id))
            .join(
                MemoirSection,
                MemoirSection.id == MemoirSectionMaterial.section_id,
            )
            .where(
                MemoirSection.project_id == project_id,
                MemoirSection.chapter_id == current_section.chapter_id,
                MemoirSection.status == MemoirSectionStatus.ACTIVE.value,
                MemoirSection.include_in_book.is_(True),
                MemoirSectionMaterial.include_in_book.is_(True),
                MemoirSectionMaterial.utterance_id != utterance_id,
            )
        )
        if not remaining_material_count:
            raise HTTPException(
                status_code=409,
                detail="原章节至少需要保留 1 条素材",
            )

    changed_ids: set[str] = set()
    created_chapter = False
    created_section = False
    if target is None and target_chapter_id is not None:
        target, changed_ids = await _create_section_for_placement(
            db,
            project_id=project_id,
            chapter_id=target_chapter_id,
            title=req.new_section_title or "",
        )
        created_section = True
    elif target is None:
        chapter_count = await db.scalar(
            select(func.count(MemoirBookPlanChapter.id)).where(
                MemoirBookPlanChapter.project_id == project_id,
                MemoirBookPlanChapter.plan_id == plan.id,
            )
        )
        if (chapter_count or 0) >= MAX_MEMOIR_BOOK_PLAN_CHAPTERS:
            raise HTTPException(
                status_code=400,
                detail=f"章节数量不能超过 {MAX_MEMOIR_BOOK_PLAN_CHAPTERS} 章",
            )
        chapters = list(
            (
                await db.scalars(
                    select(MemoirBookPlanChapter)
                    .where(
                        MemoirBookPlanChapter.project_id == project_id,
                        MemoirBookPlanChapter.plan_id == plan.id,
                    )
                    .order_by(
                        MemoirBookPlanChapter.sort_order,
                        MemoirBookPlanChapter.id,
                    )
                )
            ).all()
        )
        chapter = MemoirBookPlanChapter(
            plan_id=plan.id,
            project_id=project_id,
            title=req.new_chapter_title or "",
            sort_order=max(
                (item.sort_order for item in chapters),
                default=-1,
            ) + 1,
            include_in_book=True,
            metadata_json={
                "created_from_material_assignment_at": datetime.now(UTC).isoformat(),
                "created_from_material_assignment_by": structure_updated_by.value,
            },
        )
        db.add(chapter)
        await db.flush()
        target, changed_ids = await _create_section_for_placement(
            db,
            project_id=project_id,
            chapter_id=chapter.id,
            title=req.new_section_title or "",
        )
        created_chapter = True
        created_section = True

    changed_ids |= await _move_materials(
        db,
        project_id=project_id,
        target=target,
        utterance_ids=[utterance_id],
        assigned_by=assigned_by,
        assignment_reason=assignment_reason or (
            "manual_create_chapter"
            if created_chapter
            else "manual_create_section"
            if created_section
            else "manual_assignment"
        ),
    )
    affected_chapter_ids = {target.chapter_id}
    if current_section is not None:
        affected_chapter_ids.add(current_section.chapter_id)
    await mark_section_structure_changed(
        db,
        project_id=project_id,
        plan=plan,
        chapter_ids=affected_chapter_ids,
        section_ids=changed_ids,
        operation=(
            "create_chapter_from_material"
            if created_chapter
            else "create_section_from_material"
            if created_section
            else "place_material"
        ),
        updated_by=structure_updated_by,
    )
    if commit:
        await db.commit()
    else:
        await db.flush()
    return MemoirSectionMaterialPlacementResponse(
        project_id=project_id,
        utterance_id=utterance_id,
        chapter_id=target.chapter_id,
        section_id=target.id,
        created_chapter=created_chapter,
        created_section=created_section,
    )


async def apply_section_merge_groups(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    groups: list[tuple[str, MemoirSectionMergeRequest]],
    assigned_by: MemoirSectionMaterialAssignedBy = MemoirSectionMaterialAssignedBy.USER,
    updated_by: MemoirCreatedBy = MemoirCreatedBy.USER,
) -> SectionChanges:
    """Stage merges under the caller's locks and return changes for one finalization."""
    all_ids = [
        section_id
        for target_id, req in groups
        for section_id in (target_id, *req.source_section_ids)
    ]
    if any(target_id in req.source_section_ids for target_id, req in groups):
        raise HTTPException(status_code=400, detail="目标小章节不能同时作为来源")
    if len(all_ids) != len(set(all_ids)):
        raise HTTPException(status_code=400, detail="同一个小章节不能重复合并")
    sections_by_id = {
        section_id: await _load_section(db, project_id=project_id, section_id=section_id)
        for section_id in all_ids
    }
    if any(sections_by_id[target_id].chapter_id != chapter_id for target_id, _ in groups):
        raise HTTPException(status_code=400, detail="目标小节不在目标章节中")
    affected_chapter_ids = {section.chapter_id for section in sections_by_id.values()}
    for affected_chapter_id in sorted(affected_chapter_ids):
        await lock_chapter_structure(db, project_id=project_id, chapter_id=affected_chapter_id)

    changed_ids = set(all_ids)
    for target_id, req in groups:
        target = sections_by_id[target_id]
        source_sections = [sections_by_id[section_id] for section_id in req.source_section_ids]
        utterance_ids = [
            material.utterance_id
            for section in sorted(source_sections, key=lambda item: (item.sort_order, item.id))
            for material in sorted(section.materials, key=lambda item: (item.sort_order, item.id))
        ]
        if utterance_ids:
            changed_ids |= await _move_materials(
                db,
                project_id=project_id,
                target=target,
                utterance_ids=utterance_ids,
                assigned_by=assigned_by,
                assignment_reason=(
                    "ai_section_merge"
                    if assigned_by == MemoirSectionMaterialAssignedBy.AI
                    else "section_merge"
                ),
            )
        if req.title is not None and req.title != target.title:
            target.metadata_json = {**target.metadata_json, "title_updated_by": updated_by.value, "previous_title": target.title}
            target.title = req.title
            target.version += 1
            target.updated_at = utcnow_naive()
            db.add(target)
        await db.execute(
            delete(MemoirSection).where(
                MemoirSection.project_id == project_id,
                MemoirSection.id.in_(req.source_section_ids),
            )
        )
        await db.flush()

    for affected_chapter_id in sorted(affected_chapter_ids):
        remaining = await _load_sections(db, project_id=project_id, chapter_id=affected_chapter_id)
        changed_ids |= await _set_section_order(db, sections=remaining)
    return SectionChanges(frozenset(changed_ids), frozenset(affected_chapter_ids))


async def merge_sections(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    target_section_id: str,
    req: MemoirSectionMergeRequest,
    allow_admin: bool = False,
    commit: bool = True,
) -> MemoirSectionCollectionResponse:
    await load_memoir_project(db, project_id, user_id, allow_admin=allow_admin)
    plan = await lock_plan_structure(db, project_id=project_id)
    target = await _load_section(db, project_id=project_id, section_id=target_section_id)
    changes = await apply_section_merge_groups(
        db,
        project_id=project_id,
        chapter_id=target.chapter_id,
        groups=[(target_section_id, req)],
    )
    await mark_section_structure_changed(
        db, project_id=project_id, plan=plan, chapter_ids=set(changes.chapter_ids),
        section_ids=set(changes.section_ids), operation="merge_sections",
    )
    await (db.commit() if commit else db.flush())
    return await _collection_response(db, project_id=project_id)


async def apply_section_split(
    db: AsyncSession,
    *,
    project_id: str,
    source_section_id: str,
    req: MemoirSectionSplitRequest,
    assigned_by: MemoirSectionMaterialAssignedBy = MemoirSectionMaterialAssignedBy.USER,
) -> tuple[MemoirSection, SectionChanges]:
    """Split whole materials under the caller's structure locks, without committing."""
    source = await _load_section(db, project_id=project_id, section_id=source_section_id)
    current_ids = {material.utterance_id for material in source.materials}
    requested_ids = set(req.utterance_ids)
    if not requested_ids.issubset(current_ids):
        raise HTTPException(status_code=400, detail="拆分素材不属于来源小章节")
    if requested_ids == current_ids:
        raise HTTPException(status_code=400, detail="拆分后来源小章节不能为空")

    sections = await _load_sections(
        db,
        project_id=project_id,
        chapter_id=source.chapter_id,
    )
    new_section = MemoirSection(
        project_id=project_id,
        chapter_id=source.chapter_id,
        title=req.title,
        metadata_json={"title_updated_by": assigned_by.value},
        section_type=req.section_type.value,
        sort_order=max((item.sort_order for item in sections), default=-1) + 1,
    )
    db.add(new_section)
    await db.flush()
    changed_ids = await _move_materials(
        db,
        project_id=project_id,
        target=new_section,
        utterance_ids=req.utterance_ids,
        assigned_by=assigned_by,
        assignment_reason="section_split",
    )
    source_index = next(
        index for index, section in enumerate(sections) if section.id == source.id
    )
    sections.insert(source_index + 1, new_section)
    changed_ids |= await _set_section_order(db, sections=sections)
    return new_section, SectionChanges(frozenset(changed_ids), frozenset({source.chapter_id}))


async def split_section(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    source_section_id: str,
    req: MemoirSectionSplitRequest,
    allow_admin: bool = False,
) -> MemoirSectionCollectionResponse:
    await load_memoir_project(
        db,
        project_id,
        user_id,
        allow_admin=allow_admin,
    )
    source = await _load_section(
        db,
        project_id=project_id,
        section_id=source_section_id,
    )
    await lock_chapter_structure(
        db,
        project_id=project_id,
        chapter_id=source.chapter_id,
    )
    _, changes = await apply_section_split(
        db, project_id=project_id, source_section_id=source_section_id, req=req,
    )
    await finalize_section_changes(db, project_id=project_id, section_ids=set(changes.section_ids))
    await db.commit()
    return await _collection_response(db, project_id=project_id)


async def bootstrap_sections(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    allow_admin: bool = False,
    commit: bool = True,
) -> MemoirSectionCollectionResponse:
    await load_memoir_project(
        db,
        project_id,
        user_id,
        allow_admin=allow_admin,
    )
    plan = await _load_plan(db, project_id=project_id)
    chapters = list(
        (
            await db.execute(
                select(MemoirBookPlanChapter)
                .where(
                    MemoirBookPlanChapter.project_id == project_id,
                    MemoirBookPlanChapter.plan_id == plan.id,
                )
                .order_by(
                    MemoirBookPlanChapter.sort_order,
                    MemoirBookPlanChapter.id,
                )
            )
        ).scalars().all()
    )
    for chapter in sorted(chapters, key=lambda item: item.id):
        await lock_chapter_structure(
            db,
            project_id=project_id,
            chapter_id=chapter.id,
        )
    existing_sections = await _load_sections(db, project_id=project_id)
    first_section_by_chapter = {
        chapter_id: sorted(items, key=lambda item: (item.sort_order, item.id))[0]
        for chapter_id in {section.chapter_id for section in existing_sections}
        if (
            items := [
                section
                for section in existing_sections
                if section.chapter_id == chapter_id
            ]
        )
    }
    for chapter in chapters:
        if chapter.id in first_section_by_chapter:
            continue
        section = MemoirSection(
            project_id=project_id,
            chapter_id=chapter.id,
            title=chapter.title,
            sort_order=0,
        )
        db.add(section)
        await db.flush()
        first_section_by_chapter[chapter.id] = section

    await (db.commit() if commit else db.flush())
    return await _collection_response(db, project_id=project_id)


async def sync_materials_to_chapters(
    db: AsyncSession,
    *,
    project_id: str,
    utterance_ids_by_chapter: dict[str, list[str]],
    assigned_by: MemoirSectionMaterialAssignedBy,
    assignment_reason: str,
) -> set[str]:
    """Stage membership changes and return section IDs for the caller to finalize."""
    seen_utterances: set[str] = set()
    for utterance_ids in utterance_ids_by_chapter.values():
        chapter_utterances = set(utterance_ids)
        if seen_utterances & chapter_utterances:
            raise HTTPException(status_code=400, detail="同一条讲述记录不能重复归入多个章节")
        seen_utterances.update(chapter_utterances)
    chapter_ids = set(utterance_ids_by_chapter)
    chapters = list(
        (
            await db.scalars(
                select(MemoirBookPlanChapter)
                .where(
                    MemoirBookPlanChapter.project_id == project_id,
                    MemoirBookPlanChapter.id.in_(chapter_ids),
                )
                .order_by(MemoirBookPlanChapter.id)
                .with_for_update()
            )
        ).all()
    )
    if {chapter.id for chapter in chapters} != chapter_ids:
        raise HTTPException(status_code=404, detail="章节不存在")

    sections = await _load_sections(db, project_id=project_id)
    first_by_chapter: dict[str, MemoirSection] = {}
    for section in sections:
        if section.status == MemoirSectionStatus.ACTIVE.value and section.include_in_book:
            first_by_chapter.setdefault(section.chapter_id, section)

    desired_ids = {
        utterance_id
        for utterance_ids in utterance_ids_by_chapter.values()
        for utterance_id in utterance_ids
    }
    existing = list(
        (
            await db.scalars(
                select(MemoirSectionMaterial).where(
                    MemoirSectionMaterial.project_id == project_id
                )
            )
        ).all()
    )
    existing_by_utterance = {material.utterance_id: material for material in existing}
    chapter_by_section = {section.id: section.chapter_id for section in sections}
    included_sections = {section.id for section in sections if section.include_in_book and section.status == MemoirSectionStatus.ACTIVE.value}
    removed = [material for material in existing if material.include_in_book
        and material.section_id in included_sections
        and chapter_by_section.get(material.section_id) in chapter_ids and material.utterance_id not in desired_ids]
    affected = {
        material.section_id
        for material in removed
    }
    for material in removed:
        await db.delete(material)
    await db.flush()

    for chapter in chapters:
        utterance_ids = [uid for uid in dict.fromkeys(utterance_ids_by_chapter[chapter.id])
            if uid not in existing_by_utterance or chapter_by_section.get(existing_by_utterance[uid].section_id) != chapter.id]
        if not utterance_ids:
            continue
        if chapter.id not in first_by_chapter:
            section = MemoirSection(
                project_id=project_id,
                chapter_id=chapter.id,
                title=chapter.title,
                section_type=MemoirSectionType.LIFE_FRAGMENT.value,
                sort_order=max((item.sort_order for item in sections if item.chapter_id == chapter.id), default=-1) + 1,
            )
            db.add(section)
            await db.flush()
            first_by_chapter[chapter.id] = section
        affected |= await _move_materials(
            db,
            project_id=project_id,
            target=first_by_chapter[chapter.id],
            utterance_ids=utterance_ids,
            assigned_by=assigned_by,
            assignment_reason=assignment_reason,
        )
    return affected
