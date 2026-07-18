from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import (
    MemoirChapterSourceLinkStatus,
    MemoirSectionMaterialAssignedBy,
    MemoirSectionStatus,
)
from app.models.memoir import (
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirChapterSourceLink,
    MemoirSection,
    MemoirSectionMaterial,
    MemoirTranscriptVersion,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.schemas.memoir_sections import (
    MemoirSectionCollectionResponse,
    MemoirSectionCreateRequest,
    MemoirSectionMaterialAssignmentRequest,
    MemoirSectionMaterialMoveRequest,
    MemoirSectionMaterialResponse,
    MemoirSectionMergeRequest,
    MemoirSectionReorderRequest,
    MemoirSectionResponse,
    MemoirSectionSplitRequest,
    MemoirSectionUpdateRequest,
)
from app.services.memoir.access import load_memoir_project


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


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


def _material_text(material: MemoirSectionMaterial) -> str:
    if material.transcript_version is not None:
        text = (material.transcript_version.content or "").strip()
        if text:
            return text
    utterance = material.utterance
    return (utterance.transcript_edited or utterance.transcript_raw or "").strip()


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
        version=section.version,
        materials=[
            MemoirSectionMaterialResponse(
                id=material.id,
                utterance_id=material.utterance_id,
                transcript_version_id=material.transcript_version_id,
                sort_order=material.sort_order,
                text=_material_text(material),
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
) -> None:
    original_orders = {section.id: section.sort_order for section in sections}
    for index, section in enumerate(sections):
        section.sort_order = -(index + 1)
        db.add(section)
    await db.flush()
    now = _utcnow()
    for index, section in enumerate(sections):
        if original_orders[section.id] != index:
            section.version += 1
        section.sort_order = index
        section.updated_at = now
        db.add(section)
    await db.flush()


async def _set_material_order(
    db: AsyncSession,
    *,
    materials: list[MemoirSectionMaterial],
) -> None:
    for index, material in enumerate(materials):
        material.sort_order = -(index + 1)
        db.add(material)
    await db.flush()
    now = _utcnow()
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
    await _load_chapter(db, project_id=project_id, chapter_id=chapter_id)
    sections = await _load_sections(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    section = MemoirSection(
        project_id=project_id,
        chapter_id=chapter_id,
        title=req.title,
        section_type=req.section_type.value,
        sort_order=len(sections),
    )
    db.add(section)
    await db.flush()
    insert_at = (
        len(sections)
        if req.sort_order is None
        else min(req.sort_order, len(sections))
    )
    sections.insert(insert_at, section)
    await _set_section_order(db, sections=sections)
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
) -> MemoirSectionCollectionResponse:
    await load_memoir_project(
        db,
        project_id,
        user_id,
        allow_admin=allow_admin,
    )
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
            setattr(section, field, value)
            changed = True
    if changed:
        section.version += 1
        section.updated_at = _utcnow()
        db.add(section)
        await db.commit()
    return await _collection_response(db, project_id=project_id)


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
    await _load_chapter(db, project_id=project_id, chapter_id=chapter_id)
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
    await _set_section_order(db, sections=ordered)
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
            )
        ).scalars().all()
    )
    by_id = {utterance.id: utterance for utterance in utterances}
    if set(by_id) != set(utterance_ids):
        raise HTTPException(status_code=404, detail="讲述素材不存在")
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
    await _load_project_utterances(
        db,
        project_id=project_id,
        utterance_ids=utterance_ids,
    )
    existing = list(
        (
            await db.execute(
                select(MemoirSectionMaterial).where(
                    MemoirSectionMaterial.project_id == project_id,
                    MemoirSectionMaterial.utterance_id.in_(utterance_ids),
                )
            )
        ).scalars().all()
    )
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
    now = _utcnow()
    for section in sections:
        section.version += 1
        section.updated_at = now
        db.add(section)
    await db.flush()
    return affected_section_ids


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
    await _move_materials(
        db,
        project_id=project_id,
        target=target,
        utterance_ids=req.utterance_ids,
        assigned_by=MemoirSectionMaterialAssignedBy.USER,
        assignment_reason="manual_assignment",
    )
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
    await _move_materials(
        db,
        project_id=project_id,
        target=target,
        utterance_ids=[utterance_id],
        target_sort_order=req.target_sort_order,
        assigned_by=MemoirSectionMaterialAssignedBy.USER,
        assignment_reason="manual_move",
    )
    await db.commit()
    return await _collection_response(db, project_id=project_id)


async def merge_sections(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    target_section_id: str,
    req: MemoirSectionMergeRequest,
    allow_admin: bool = False,
) -> MemoirSectionCollectionResponse:
    await load_memoir_project(
        db,
        project_id,
        user_id,
        allow_admin=allow_admin,
    )
    if target_section_id in req.source_section_ids:
        raise HTTPException(status_code=400, detail="目标小章节不能同时作为来源")
    target = await _load_section(
        db,
        project_id=project_id,
        section_id=target_section_id,
    )
    source_sections = [
        await _load_section(db, project_id=project_id, section_id=section_id)
        for section_id in req.source_section_ids
    ]
    if any(section.chapter_id != target.chapter_id for section in source_sections):
        raise HTTPException(status_code=400, detail="只能合并同一章内的小章节")
    utterance_ids = [
        material.utterance_id
        for section in sorted(
            source_sections,
            key=lambda item: (item.sort_order, item.id),
        )
        for material in sorted(
            section.materials,
            key=lambda item: (item.sort_order, item.id),
        )
    ]
    if utterance_ids:
        await _move_materials(
            db,
            project_id=project_id,
            target=target,
            utterance_ids=utterance_ids,
            assigned_by=MemoirSectionMaterialAssignedBy.USER,
            assignment_reason="section_merge",
        )
    if req.title is not None:
        target.title = req.title
        target.version += 1
        target.updated_at = _utcnow()
        db.add(target)
    await db.execute(
        delete(MemoirSection).where(
            MemoirSection.project_id == project_id,
            MemoirSection.id.in_(req.source_section_ids),
        )
    )
    await db.flush()
    remaining = await _load_sections(
        db,
        project_id=project_id,
        chapter_id=target.chapter_id,
    )
    await _set_section_order(db, sections=remaining)
    await db.commit()
    return await _collection_response(db, project_id=project_id)


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
        section_type=req.section_type.value,
        sort_order=len(sections),
    )
    db.add(new_section)
    await db.flush()
    await _move_materials(
        db,
        project_id=project_id,
        target=new_section,
        utterance_ids=req.utterance_ids,
        assigned_by=MemoirSectionMaterialAssignedBy.USER,
        assignment_reason="section_split",
    )
    source_index = next(
        index for index, section in enumerate(sections) if section.id == source.id
    )
    sections.insert(source_index + 1, new_section)
    await _set_section_order(db, sections=sections)
    await db.commit()
    return await _collection_response(db, project_id=project_id)


async def bootstrap_sections(
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

    links = list(
        (
            await db.execute(
                select(MemoirChapterSourceLink)
                .join(
                    MemoirBookPlanChapter,
                    MemoirChapterSourceLink.chapter_id
                    == MemoirBookPlanChapter.id,
                )
                .where(
                    MemoirChapterSourceLink.project_id == project_id,
                    MemoirChapterSourceLink.plan_id == plan.id,
                    MemoirChapterSourceLink.status
                    == MemoirChapterSourceLinkStatus.ACTIVE.value,
                )
                .order_by(
                    MemoirBookPlanChapter.sort_order,
                    MemoirChapterSourceLink.created_at,
                    MemoirChapterSourceLink.id,
                )
            )
        ).scalars().all()
    )
    existing_membership_ids = set(
        (
            await db.execute(
                select(MemoirSectionMaterial.utterance_id).where(
                    MemoirSectionMaterial.project_id == project_id
                )
            )
        ).scalars().all()
    )
    links_by_section: dict[str, list[MemoirChapterSourceLink]] = {}
    for link in links:
        section = first_section_by_chapter.get(link.chapter_id)
        if section is None or link.utterance_id in existing_membership_ids:
            continue
        links_by_section.setdefault(section.id, []).append(link)
        existing_membership_ids.add(link.utterance_id)

    sections_by_id = {
        section.id: section for section in first_section_by_chapter.values()
    }
    for section_id, section_links in links_by_section.items():
        section = sections_by_id[section_id]
        await _move_materials(
            db,
            project_id=project_id,
            target=section,
            utterance_ids=[link.utterance_id for link in section_links],
            assigned_by=MemoirSectionMaterialAssignedBy.SYSTEM,
            assignment_reason="bootstrap_from_chapter_source",
        )
    await db.commit()
    return await _collection_response(db, project_id=project_id)
