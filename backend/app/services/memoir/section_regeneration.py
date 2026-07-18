from __future__ import annotations

from dataclasses import dataclass, field
import uuid

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import (
    MemoirBookPlanStatus,
    MemoirSectionMaterialAssignedBy,
)
from app.models.memoir import (
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirSection,
    MemoirSectionMaterial,
    MemoirVoiceUtterance,
)
from app.schemas.memoir_sections import MemoirSectionRegenerateRequest
from app.services.ai_factory import get_ai_service
from app.services.memoir.access import load_memoir_project
from app.services.memoir.material_organization import (
    MaterialOrganizationChapterCandidate,
    MaterialOrganizationDecision,
    MaterialOrganizationSectionCandidate,
    decide_material_organization,
    material_organization_excerpt,
)
from app.services.memoir.material_roles import is_narrator_material
from app.services.memoir.sections import assign_material_to_section
from app.services.memoir.utterance_editing_pipeline import (
    current_transcript_version_id,
    current_utterance_text,
    source_text_hash,
)


@dataclass(frozen=True)
class _MaterialSnapshot:
    utterance_id: str
    text: str
    text_hash: str
    transcript_version_id: str | None
    include_in_book: bool


@dataclass(frozen=True)
class _PlannedAssignment:
    material: _MaterialSnapshot
    decision: MaterialOrganizationDecision


@dataclass
class _PlannedSection:
    candidate: MaterialOrganizationSectionCandidate
    assignments: list[_PlannedAssignment] = field(default_factory=list)


async def _load_chapter(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> MemoirBookPlanChapter:
    chapter = await db.scalar(
        select(MemoirBookPlanChapter)
        .join(MemoirBookPlan, MemoirBookPlanChapter.plan_id == MemoirBookPlan.id)
        .where(
            MemoirBookPlanChapter.project_id == project_id,
            MemoirBookPlanChapter.id == chapter_id,
            MemoirBookPlan.status == MemoirBookPlanStatus.CONFIRMED.value,
        )
        .execution_options(populate_existing=True)
        .options(
            selectinload(MemoirBookPlanChapter.sections)
            .selectinload(MemoirSection.materials)
            .selectinload(MemoirSectionMaterial.utterance)
            .selectinload(MemoirVoiceUtterance.chat_message),
        )
    )
    if chapter is None:
        raise HTTPException(status_code=404, detail="章节不存在或章节规划尚未确认")
    return chapter


def _validate_expected_sections(
    chapter: MemoirBookPlanChapter,
    req: MemoirSectionRegenerateRequest,
) -> None:
    current = {
        section.id: section.version
        for section in chapter.sections
    }
    expected = {
        section.id: section.version
        for section in req.expected_sections
    }
    if current != expected:
        raise HTTPException(status_code=409, detail="本章小节已经变化，请刷新后重试")


def _ordered_materials(
    chapter: MemoirBookPlanChapter,
) -> list[tuple[MemoirSection, MemoirSectionMaterial]]:
    materials = [
        (section, material)
        for section in chapter.sections
        for material in section.materials
    ]
    if not materials:
        raise HTTPException(status_code=422, detail="本章没有可用于生成小节的讲述")
    ids = [material.utterance_id for _, material in materials]
    if len(ids) != len(set(ids)):
        raise HTTPException(status_code=409, detail="本章讲述存在重复归属，请先处理")
    return sorted(
        materials,
        key=lambda item: (
            item[1].utterance.created_at,
            item[1].utterance_id,
        ),
    )


def _material_snapshots(
    chapter: MemoirBookPlanChapter,
) -> list[_MaterialSnapshot]:
    return [
        _MaterialSnapshot(
            utterance_id=utterance.id,
            text=text,
            text_hash=source_text_hash(text),
            transcript_version_id=current_transcript_version_id(utterance),
            include_in_book=(
                section.include_in_book and material.include_in_book
            ),
        )
        for section, material in _ordered_materials(chapter)
        if (utterance := material.utterance)
        if is_narrator_material(utterance)
        and (text := current_utterance_text(utterance))
    ]


def _validate_material_snapshots(
    current: list[_MaterialSnapshot],
    expected: list[_MaterialSnapshot],
) -> None:
    if current != expected:
        raise HTTPException(status_code=409, detail="本章讲述已经变化，请刷新后重试")


def _append_candidate_example(
    candidate: MaterialOrganizationSectionCandidate,
    material_text: str,
) -> None:
    excerpt = material_organization_excerpt(material_text)
    if excerpt:
        candidate.existing_material_examples = [
            *candidate.existing_material_examples,
            excerpt,
        ][-2:]


async def _plan_section_regeneration(
    *,
    chapter_id: str,
    chapter_title: str,
    materials: list[_MaterialSnapshot],
    ai_model: str | None,
) -> tuple[list[_PlannedSection], int]:
    client = get_ai_service(ai_model)
    chapters = [
        MaterialOrganizationChapterCandidate(
            id=chapter_id,
            title=chapter_title,
        )
    ]
    planned_sections: list[_PlannedSection] = []
    unassigned_count = 0
    for material in materials:
        decision = await decide_material_organization(
            material_text=material.text,
            chapters=chapters,
            sections=[section.candidate for section in planned_sections],
            ai_client=client,
        )
        if decision.action == "leave_unassigned":
            unassigned_count += 1
            continue
        if decision.action == "create_section":
            candidate = MaterialOrganizationSectionCandidate(
                id=str(uuid.uuid4()),
                chapter_id=chapter_id,
                title=decision.title,
                section_type=decision.section_type,
                existing_material_examples=[],
            )
            planned_sections.append(_PlannedSection(candidate=candidate))

        target = next(
            (
                section
                for section in planned_sections
                if section.candidate.id == decision.section_id
            ),
            None,
        )
        if decision.action == "create_section":
            target = planned_sections[-1]
        if target is None:
            raise RuntimeError("素材整理结果缺少目标小章节")
        target.assignments.append(
            _PlannedAssignment(
                material=material,
                decision=decision,
            )
        )
        _append_candidate_example(target.candidate, material.text)
    return planned_sections, unassigned_count


async def regenerate_chapter_sections(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    chapter_id: str,
    req: MemoirSectionRegenerateRequest,
    allow_admin: bool = False,
) -> dict[str, int]:
    await load_memoir_project(
        db,
        project_id,
        user_id,
        allow_admin=allow_admin,
    )
    chapter = await _load_chapter(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    _validate_expected_sections(chapter, req)
    materials = _material_snapshots(chapter)
    if not materials:
        raise HTTPException(status_code=422, detail="本章没有可用于生成小节的讲述")
    chapter_title = chapter.title

    # LLM calls may take minutes. Release the read transaction and defer every
    # mutation until all decisions are ready, then replace the structure atomically.
    await db.commit()
    planned_sections, unassigned_count = await _plan_section_regeneration(
        chapter_id=chapter_id,
        chapter_title=chapter_title,
        materials=materials,
        ai_model=req.ai_model,
    )
    if not planned_sections:
        raise HTTPException(
            status_code=409,
            detail="AI 未能为本章生成任何小节，请重试",
        )

    chapter = await _load_chapter(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    _validate_expected_sections(chapter, req)
    _validate_material_snapshots(_material_snapshots(chapter), materials)
    await db.execute(
        delete(MemoirSection)
        .where(
            MemoirSection.project_id == project_id,
            MemoirSection.chapter_id == chapter_id,
        )
        .execution_options(synchronize_session=False)
    )
    await db.flush()

    assigned_count = 0
    for index, planned_section in enumerate(planned_sections):
        candidate = planned_section.candidate
        db.add(
            MemoirSection(
                id=candidate.id,
                project_id=project_id,
                chapter_id=chapter_id,
                title=candidate.title,
                section_type=candidate.section_type,
                sort_order=index,
            )
        )
    await db.flush()

    for planned_section in planned_sections:
        for assignment in planned_section.assignments:
            material = await assign_material_to_section(
                db,
                project_id=project_id,
                section_id=planned_section.candidate.id,
                utterance_id=assignment.material.utterance_id,
                assigned_by=MemoirSectionMaterialAssignedBy.AI,
                assignment_reason=(
                    assignment.decision.reason or "llm_material_organization"
                ),
            )
            material.assignment_confidence = assignment.decision.confidence
            material.include_in_book = assignment.material.include_in_book
            db.add(material)
            assigned_count += 1

    return {
        "material_count": len(materials),
        "assigned_count": assigned_count,
        "unassigned_count": unassigned_count,
        "section_count": len(planned_sections),
    }
