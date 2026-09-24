from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
import json
import re
import uuid

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import (
    MemoirBookPlanStatus,
    MemoirCreatedBy,
    MemoirEditingJobStatus,
    MemoirEditingJobType,
    MemoirSectionMaterialAssignedBy,
)
from app.models.memoir import (
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirEditingJob,
    MemoirSection,
    MemoirSectionMaterial,
    MemoirVoiceUtterance,
)
from app.schemas.memoir_sections import MemoirSectionRegenerateRequest
from app.services.ai_calls import generate_structured_with_client
from app.services.ai_factory import get_ai_service
from app.services.ai_references import ModelReferenceMap
from app.services.ai_timeouts import ai_text_timeout_seconds
from app.services.ai_types import AITextClient
from app.services.ai_workloads import AITextWorkload, ai_text_workload_policy
from app.services.memoir.access import load_memoir_project
from app.services.memoir import editing_jobs
from app.services.memoir.editing_jobs import EditingJobResult
from app.services.memoir.material_organization import (
    ALLOWED_SECTION_TYPES,
    MaterialOrganizationDecision,
    MaterialOrganizationSectionCandidate,
    normalize_material_organization_decision,
)
from app.services.memoir.material_roles import is_narrator_material
from app.services.memoir.project_ai_preferences import select_project_ai_model
from app.services.memoir.section_roles import (
    PENDING_ASSIGNMENT_SECTION_ROLE,
    PENDING_ASSIGNMENT_SECTION_TITLE,
    SECTION_SYSTEM_ROLE_KEY,
    requires_user_assignment,
)
from app.services.memoir.sections import (
    assign_material_to_section,
    lock_chapter_structure,
    lock_plan_structure,
    mark_section_structure_changed,
)
from app.services.memoir.transcript_projection import (
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
    source_section_id: str = ""
    source_section_title: str = ""
    source_section_order: int = 0


@dataclass(frozen=True)
class _PlannedAssignment:
    material: _MaterialSnapshot
    decision: MaterialOrganizationDecision


@dataclass
class _PlannedSection:
    candidate: MaterialOrganizationSectionCandidate
    assignments: list[_PlannedAssignment] = field(default_factory=list)


class _SectionCandidateOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    section_type: str


class _SectionStructureOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sections: list[_SectionCandidateOutput] = Field(min_length=1)


class _MaterialAssignmentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    utterance_id: str
    action: str
    section_id: str | None = None
    confidence: float | None = None
    reason: str = ""


class _MaterialAssignmentsOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignments: list[_MaterialAssignmentOutput] = Field(min_length=1)


SECTION_STRUCTURE_SOURCE_CHAR_BUDGET = 8_000
MATERIAL_BATCH_CHAR_BUDGET = 8_000
LLM_PROMPT_CHAR_LIMIT = 16_000
SECTION_REGENERATION_SYSTEM_PROMPT = (
    "你是一位克制的中文回忆录结构编辑。"
    "从全章理解素材关系，并保持每条讲述完整，不拆分或虚构内容。"
)
ProgressCallback = Callable[[dict[str, object]], Awaitable[None]]


class SectionRegenerationConflict(HTTPException):
    """The source snapshot changed while a section rewrite was pending."""


class _SectionRegenerationCancelled(RuntimeError):
    pass


async def _report_progress(
    callback: ProgressCallback | None,
    **values: object,
) -> None:
    if callback is not None:
        await callback(values)


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
        raise SectionRegenerationConflict(status_code=409, detail="本章小节已经变化，请刷新后重试")


def _current_included_section_count(chapter: MemoirBookPlanChapter) -> int:
    return sum(
        section.status == "active" and section.include_in_book
        for section in chapter.sections
    )


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
    snapshots: list[_MaterialSnapshot] = []
    for section, material in _ordered_materials(chapter):
        utterance = material.utterance
        text = current_utterance_text(utterance) if utterance else ""
        if (
            utterance is None
            or not is_narrator_material(utterance)
            or not text
        ):
            raise HTTPException(
                status_code=409,
                detail="本章包含暂不能重排的讲述，原小节结构已保留",
            )
        snapshots.append(
            _MaterialSnapshot(
                utterance_id=utterance.id,
                text=text,
                text_hash=source_text_hash(text),
                transcript_version_id=current_transcript_version_id(utterance),
                include_in_book=(
                    material.include_in_book
                    if requires_user_assignment(section.metadata_json)
                    else section.include_in_book and material.include_in_book
                ),
                source_section_id=(
                    section.id if section.status == "active" and section.include_in_book else ""
                ),
                source_section_title=section.title,
                source_section_order=section.sort_order,
            )
        )
    return snapshots


def _validate_material_snapshots(
    current: list[_MaterialSnapshot],
    expected: list[_MaterialSnapshot],
) -> None:
    if current != expected:
        raise SectionRegenerationConflict(status_code=409, detail="本章讲述已经变化，请刷新后重试")


def _compact_excerpt(value: str, *, limit: int) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    if len(text) <= limit:
        return text
    head_length = max(1, int(limit * 0.7))
    tail_length = max(1, limit - head_length - 1)
    return f"{text[:head_length]}…{text[-tail_length:]}"


def _build_section_structure_prompt(
    *,
    chapter_title: str,
    materials: list[_MaterialSnapshot],
    max_sections: int | None = None,
    current_section_count: int | None = None,
) -> str:
    excerpt_limit = max(
        120,
        min(
            600,
            SECTION_STRUCTURE_SOURCE_CHAR_BUDGET // max(1, len(materials)),
        ),
    )
    material_outlines = [
        {
            "source_index": index,
            "excerpt": _compact_excerpt(material.text, limit=excerpt_limit),
            **({"current_section": f"{material.source_section_order + 1}. {material.source_section_title}"}
               if max_sections is not None or current_section_count is not None else {}),
        }
        for index, material in enumerate(materials, start=1)
    ]
    chapter_json = json.dumps(
        {"title": chapter_title},
        ensure_ascii=False,
    )
    outlines_json = json.dumps(material_outlines, ensure_ascii=False)
    if current_section_count is not None:
        task = (
            f"任务：本章当前有 {current_section_count} 个小节。用户希望合并现有小节，让数量比现在更少。"
            "请根据现有小节和讲述的内容关系，自行判断合适的合并方式和数量，不预设合并后的具体个数；"
            "同一原小节的讲述必须留在同一个新小节，不要拆散。保留每条讲述，不虚构内容。\n"
        )
    elif max_sections is not None:
        task = (
            f"任务：合并本章现有小节，最终最多 {max_sections} 个（包含待归类组）；"
            "同一原小节的讲述必须留在同一个新小节，不要拆散。优先合并主题相近的小节，"
            "保留每条讲述，不增加小节数量。\n"
        )
    else:
        task = (
            "任务：先从全章视角设计一组语义清晰、互不重复的小节。\n"
            "按事件、人物、阶段或反思主题自然分组，不强制小节数量；"
        )
    return (
        task + "这里只设计候选小节，不做逐条归类。\n\n"
        f"章节：\n{chapter_json}\n\n"
        f"讲述概览：\n{outlines_json}\n\n"
        "只返回 JSON："
        '{"sections":[{"title":"","section_type":'
        '"event|life_fragment|person_portrait|life_stage_summary|'
        'viewpoint_reflection"}]}'
    )


def _normalize_section_candidates(
    payload: object,
    *,
    chapter_id: str,
) -> list[MaterialOrganizationSectionCandidate]:
    data = payload if isinstance(payload, dict) else {}
    raw_sections = data.get("sections")
    if not isinstance(raw_sections, list) or not raw_sections:
        raise ValueError("小节结构规划结果缺少 sections")
    if len(raw_sections) > 30:
        raise ValueError("小节结构规划结果数量过多")

    candidates: list[MaterialOrganizationSectionCandidate] = []
    seen_titles: set[str] = set()
    for raw in raw_sections:
        item = raw if isinstance(raw, dict) else {}
        title = re.sub(r"\s+", " ", str(item.get("title") or "")).strip()[:200]
        section_type = str(item.get("section_type") or "").strip()
        if not title or section_type not in ALLOWED_SECTION_TYPES:
            raise ValueError("小节结构规划结果包含无效标题或类型")
        normalized_title = title.casefold()
        if normalized_title in seen_titles:
            continue
        seen_titles.add(normalized_title)
        candidates.append(
            MaterialOrganizationSectionCandidate(
                id=str(uuid.uuid4()),
                chapter_id=chapter_id,
                title=title,
                section_type=section_type,
                existing_material_examples=[],
            )
        )
    if not candidates:
        raise ValueError("小节结构规划结果没有可用小节")
    return candidates


async def _plan_section_candidates(
    *,
    chapter_id: str,
    chapter_title: str,
    materials: list[_MaterialSnapshot],
    ai_client: AITextClient,
    max_sections: int | None = None,
    current_section_count: int | None = None,
) -> list[MaterialOrganizationSectionCandidate]:
    prompt = _build_section_structure_prompt(
        chapter_title=chapter_title,
        materials=materials,
        max_sections=max_sections,
        current_section_count=current_section_count,
    )
    if len(prompt) > LLM_PROMPT_CHAR_LIMIT:
        raise HTTPException(
            status_code=422,
            detail="本章素材数量过多，无法安全规划小节结构",
        )
    policy = ai_text_workload_policy(AITextWorkload.GENERATION_STRUCTURED)
    generated = await generate_structured_with_client(
        ai_client,
        SECTION_REGENERATION_SYSTEM_PROMPT,
        prompt,
        output_model=_SectionStructureOutput,
        requested_model=getattr(ai_client, "model_name", None),
        temperature=0.1,
        max_tokens=policy.max_tokens,
        timeout=ai_text_timeout_seconds(policy.timeout_profile),
        max_attempts=policy.max_attempts,
    )
    candidates = _normalize_section_candidates(
        generated.value.model_dump(),
        chapter_id=chapter_id,
    )
    if max_sections is not None and len(candidates) > max_sections:
        raise HTTPException(409, "AI 未按减少小节数量的要求规划，原小节保持不变，请重试")
    return candidates


def _material_batches(
    materials: list[_MaterialSnapshot],
) -> list[list[_MaterialSnapshot]]:
    batches: list[list[_MaterialSnapshot]] = []
    current: list[_MaterialSnapshot] = []
    current_text_chars = 0
    for material in materials:
        if (
            current
            and current_text_chars + len(material.text)
            > MATERIAL_BATCH_CHAR_BUDGET
        ):
            batches.append(current)
            current = []
            current_text_chars = 0
        current.append(material)
        current_text_chars += len(material.text)
    if current:
        batches.append(current)
    return batches


def _build_material_assignment_prompt(
    *,
    chapter_title: str,
    sections: list[MaterialOrganizationSectionCandidate],
    materials: list[_MaterialSnapshot],
    section_refs: ModelReferenceMap,
    material_refs: ModelReferenceMap,
    max_sections: int | None = None,
    current_section_count: int | None = None,
) -> str:
    chapter = json.dumps(
        {"title": chapter_title},
        ensure_ascii=False,
    )
    candidates = json.dumps(
        [
            {
                "section_id": section_refs.encode(section.id),
                "title": section.title,
                "section_type": section.section_type,
            }
            for section in sections
        ],
        ensure_ascii=False,
    )
    material_items = json.dumps(
        [
            {
                "utterance_id": material_refs.encode(material.utterance_id),
                "text": material.text,
                **({"current_section": f"{material.source_section_order + 1}. {material.source_section_title}"}
                   if max_sections is not None or current_section_count is not None else {}),
            }
            for material in materials
        ],
        ensure_ascii=False,
    )
    task = "任务：将下面多条讲述逐条归入当前章节的候选小节。"
    if max_sections is not None or current_section_count is not None:
        task += "同一原小节的讲述必须归入同一个候选小节，不得拆散。"
    return (
        task + "确实无法可靠归类时才标记为 leave_unassigned；"
        "不得遗漏、重复或改写 utterance_id。\n\n"
        f"当前章节：\n{chapter}\n\n"
        f"候选小节：\n{candidates}\n\n"
        f"待归类讲述：\n{material_items}\n\n"
        "只返回 JSON："
        '{"assignments":[{"utterance_id":"",'
        '"action":"assign_existing|leave_unassigned",'
        '"section_id":null,"confidence":0.0,"reason":""}]}'
    )


async def _assign_material_batch(
    *,
    chapter_id: str,
    chapter_title: str,
    sections: list[MaterialOrganizationSectionCandidate],
    materials: list[_MaterialSnapshot],
    ai_client: AITextClient,
    max_sections: int | None = None,
    current_section_count: int | None = None,
) -> list[MaterialOrganizationDecision]:
    section_refs = ModelReferenceMap("S", (section.id for section in sections))
    material_refs = ModelReferenceMap(
        "M",
        (material.utterance_id for material in materials),
    )
    prompt = _build_material_assignment_prompt(
        chapter_title=chapter_title,
        sections=sections,
        materials=materials,
        section_refs=section_refs,
        material_refs=material_refs,
        max_sections=max_sections,
        current_section_count=current_section_count,
    )
    if len(prompt) > LLM_PROMPT_CHAR_LIMIT:
        raise HTTPException(
            status_code=422,
            detail="单批讲述过长，无法安全完成小节归类",
        )
    policy = ai_text_workload_policy(AITextWorkload.LONG_GENERATION_STRUCTURED)
    generated = await generate_structured_with_client(
        ai_client,
        SECTION_REGENERATION_SYSTEM_PROMPT,
        prompt,
        output_model=_MaterialAssignmentsOutput,
        requested_model=getattr(ai_client, "model_name", None),
        temperature=0.1,
        max_tokens=policy.max_tokens,
        timeout=ai_text_timeout_seconds(policy.timeout_profile),
        max_attempts=policy.max_attempts,
    )
    raw_assignments = generated.value.model_dump()["assignments"]

    expected_ids = [material.utterance_id for material in materials]
    allowed_section_ids = {section.id for section in sections}
    decisions: dict[str, MaterialOrganizationDecision] = {}
    for raw in raw_assignments:
        item = raw if isinstance(raw, dict) else {}
        utterance_id = material_refs.decode(item.get("utterance_id"))
        action = str(item.get("action") or "").strip()
        if utterance_id not in expected_ids or utterance_id in decisions:
            raise ValueError("素材批量归类结果包含未知或重复的 utterance_id")
        if action not in {"assign_existing", "leave_unassigned"}:
            raise ValueError("素材批量归类结果包含不支持的 action")
        normalized_item = {
            **item,
            "section_id": (
                section_refs.decode(item.get("section_id"))
                if action == "assign_existing"
                else None
            ),
        }
        decision = normalize_material_organization_decision(
            normalized_item,
            allowed_section_ids=allowed_section_ids,
            allowed_chapter_ids={chapter_id},
        )
        if decision.action != action:
            raise ValueError("素材批量归类结果引用了未知小节")
        decisions[utterance_id] = decision
    if set(decisions) != set(expected_ids):
        raise ValueError("素材批量归类结果没有完整覆盖本批讲述")
    return [decisions[utterance_id] for utterance_id in expected_ids]


def _validate_planned_coverage(
    *,
    materials: list[_MaterialSnapshot],
    planned_sections: list[_PlannedSection],
    pending_assignments: list[_PlannedAssignment],
) -> None:
    expected_ids = [material.utterance_id for material in materials]
    planned_ids = [
        assignment.material.utterance_id
        for section in planned_sections
        for assignment in section.assignments
    ]
    pending_ids = [
        assignment.material.utterance_id
        for assignment in pending_assignments
    ]
    output_ids = [*planned_ids, *pending_ids]
    if (
        len(output_ids) != len(expected_ids)
        or len(output_ids) != len(set(output_ids))
        or set(output_ids) != set(expected_ids)
    ):
        raise HTTPException(
            status_code=409,
            detail="AI 小节结果没有完整覆盖本章讲述，原小节结构已保留，请重试",
        )


async def _plan_section_regeneration(
    *,
    chapter_id: str,
    chapter_title: str,
    materials: list[_MaterialSnapshot],
    ai_model: str | None,
    progress_callback: ProgressCallback | None = None,
    max_sections: int | None = None,
    current_section_count: int | None = None,
) -> tuple[
    list[_PlannedSection],
    list[_PlannedAssignment],
    str | None,
]:
    client = get_ai_service(ai_model)
    generated_model = getattr(client, "model_name", ai_model)
    batches = _material_batches(materials)
    total_chars = sum(len(material.text) for material in materials)
    await _report_progress(
        progress_callback,
        stage="planning_sections",
        batch_count=len(batches),
        resolved_ai_model=generated_model,
    )
    candidates = await _plan_section_candidates(
        chapter_id=chapter_id,
        chapter_title=chapter_title,
        materials=materials,
        ai_client=client,
        max_sections=max_sections,
        current_section_count=current_section_count,
    )
    planned_by_id = {
        candidate.id: _PlannedSection(candidate=candidate)
        for candidate in candidates
    }
    pending_assignments: list[_PlannedAssignment] = []
    completed_chars = 0
    processed_materials = 0
    await _report_progress(
        progress_callback,
        stage="assigning_materials",
    )
    for batch_index, batch in enumerate(batches, start=1):
        decisions = await _assign_material_batch(
            chapter_id=chapter_id,
            chapter_title=chapter_title,
            sections=candidates,
            materials=batch,
            ai_client=client,
            max_sections=max_sections,
            current_section_count=current_section_count,
        )
        for material, decision in zip(batch, decisions, strict=True):
            assignment = _PlannedAssignment(
                material=material,
                decision=decision,
            )
            if decision.action == "leave_unassigned":
                pending_assignments.append(assignment)
                continue
            target = planned_by_id.get(decision.section_id or "")
            if target is None:
                raise RuntimeError("素材整理结果缺少目标小章节")
            target.assignments.append(assignment)
        completed_chars += sum(len(material.text) for material in batch)
        processed_materials += len(batch)
        await _report_progress(
            progress_callback,
            stage="assigning_materials",
            completed_batch_count=batch_index,
            processed_char_count=min(completed_chars, total_chars),
            processed_material_count=processed_materials,
        )

    planned_sections = [
        section
        for section in planned_by_id.values()
        if section.assignments
    ]
    if max_sections is not None or current_section_count is not None:
        if any(assignment.material.include_in_book for assignment in pending_assignments):
            raise HTTPException(409, "AI 未能保留原有收录的讲述，原小节保持不变，请重试")
        resulting_count = len(planned_sections) + bool(pending_assignments)
        if (max_sections is not None and resulting_count > max_sections) or (
            current_section_count is not None and resulting_count >= current_section_count
        ):
            raise HTTPException(409, "AI 未减少小节数量，原小节保持不变，请重试")
        destinations: dict[str, set[str]] = {}
        for section in planned_sections:
            for assignment in section.assignments:
                if assignment.material.source_section_id:
                    destinations.setdefault(assignment.material.source_section_id, set()).add(section.candidate.id)
        for assignment in pending_assignments:
            if assignment.material.source_section_id:
                destinations.setdefault(assignment.material.source_section_id, set()).add("pending")
        if any(len(targets) > 1 for targets in destinations.values()):
            raise HTTPException(409, "AI 拆散了原有小节，原小节保持不变，请重试")
    await _report_progress(progress_callback, stage="saving")
    return planned_sections, pending_assignments, generated_model


async def regenerate_chapter_sections(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    chapter_id: str,
    req: MemoirSectionRegenerateRequest,
    allow_admin: bool = False,
    progress_callback: ProgressCallback | None = None,
    structure_updated_by: MemoirCreatedBy = MemoirCreatedBy.USER,
) -> dict[str, int | str | None]:
    project = await load_memoir_project(
        db,
        project_id,
        user_id,
        allow_admin=allow_admin,
    )
    selected_ai_model = select_project_ai_model(project, req.ai_model)
    chapter = await _load_chapter(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    _validate_expected_sections(chapter, req)
    current_section_count = (
        _current_included_section_count(chapter)
        if req.section_count_goal == "reduce" else None
    )
    if current_section_count is not None and current_section_count < 2:
        raise HTTPException(422, "本章至少需要两个当前收录的小节才能合并")
    materials = _material_snapshots(chapter)
    if not materials:
        raise HTTPException(status_code=422, detail="本章没有可用于生成小节的讲述")
    chapter_title = chapter.title

    # LLM calls may take minutes. Release the read transaction and defer every
    # mutation until all decisions are ready, then replace the structure atomically.
    await db.commit()
    (
        planned_sections,
        pending_assignments,
        generated_model,
    ) = await _plan_section_regeneration(
        chapter_id=chapter_id,
        chapter_title=chapter_title,
        materials=materials,
        ai_model=selected_ai_model,
        progress_callback=progress_callback,
        max_sections=req.max_sections,
        current_section_count=current_section_count,
    )
    if not planned_sections:
        raise HTTPException(
            status_code=409,
            detail="AI 未能为本章生成任何小节，请重试",
        )
    _validate_planned_coverage(
        materials=materials,
        planned_sections=planned_sections,
        pending_assignments=pending_assignments,
    )

    # The expensive model work runs without a transaction. Lock only for the
    # final revalidation and atomic structure replacement.
    plan = await lock_plan_structure(
        db,
        project_id=project_id,
    )
    await lock_chapter_structure(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
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
    generation_metadata = {
        "operation": "chapter_section_regeneration",
        "model": generated_model,
    }
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
                metadata_json={
                    "generation": generation_metadata,
                },
            )
        )
    pending_section_id: str | None = None
    if pending_assignments:
        pending_section_id = str(uuid.uuid4())
        db.add(
            MemoirSection(
                id=pending_section_id,
                project_id=project_id,
                chapter_id=chapter_id,
                title=PENDING_ASSIGNMENT_SECTION_TITLE,
                section_type="life_fragment",
                sort_order=len(planned_sections),
                include_in_book=False,
                metadata_json={
                    "generation": generation_metadata,
                    SECTION_SYSTEM_ROLE_KEY: PENDING_ASSIGNMENT_SECTION_ROLE,
                },
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

    if pending_section_id is not None:
        for assignment in pending_assignments:
            material = await assign_material_to_section(
                db,
                project_id=project_id,
                section_id=pending_section_id,
                utterance_id=assignment.material.utterance_id,
                assigned_by=MemoirSectionMaterialAssignedBy.AI,
                assignment_reason=(
                    assignment.decision.reason or "llm_left_unassigned"
                ),
            )
            material.assignment_confidence = assignment.decision.confidence
            material.include_in_book = assignment.material.include_in_book
            db.add(material)

    await mark_section_structure_changed(
        db,
        project_id=project_id,
        plan=plan,
        chapter_ids={chapter_id},
        operation="regenerate_chapter_sections",
        updated_by=structure_updated_by,
    )

    return {
        "material_count": len(materials),
        "assigned_count": assigned_count,
        "unassigned_count": len(pending_assignments),
        "section_count": len(planned_sections),
        "ai_model": generated_model,
    }


def _regeneration_job_dedupe_key(chapter_id: str) -> str:
    return f"regenerate-chapter-sections:{chapter_id}"


def _job_state(
    *,
    project_id: str,
    chapter_id: str,
    job: MemoirEditingJob | None,
) -> dict[str, object]:
    if job is None:
        return {
            "project_id": project_id,
            "chapter_id": chapter_id,
            "status": "idle",
        }
    result = dict(job.result_json or {})
    cancelled = result.get("cancelled") is True
    status = (
        "cancelled"
        if cancelled
        else (
            job.status
            if job.status
            in {
                MemoirEditingJobStatus.PENDING.value,
                MemoirEditingJobStatus.RUNNING.value,
                MemoirEditingJobStatus.SUCCEEDED.value,
                MemoirEditingJobStatus.FAILED.value,
            }
            else "failed"
        )
    )
    return {
        **result,
        "job_id": job.id,
        "project_id": project_id,
        "chapter_id": chapter_id,
        "status": status,
        "can_cancel": status
        in {
            MemoirEditingJobStatus.PENDING.value,
            MemoirEditingJobStatus.RUNNING.value,
        },
        "error_message": (
            job.error_message
            if status == MemoirEditingJobStatus.FAILED.value
            else ""
        ),
    }


async def enqueue_chapter_section_regeneration(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    chapter_id: str,
    req: MemoirSectionRegenerateRequest,
    allow_admin: bool = False,
    commit: bool = True,
) -> dict[str, object]:
    project = await load_memoir_project(
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
    if (
        req.section_count_goal == "reduce"
        and _current_included_section_count(chapter) < 2
    ):
        raise HTTPException(422, "本章至少需要两个当前收录的小节才能合并")
    materials = _material_snapshots(chapter)
    requested_ai_model = select_project_ai_model(project, req.ai_model)
    job = await editing_jobs.enqueue_editing_job(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        job_type=MemoirEditingJobType.REGENERATE_CHAPTER_SECTIONS,
        dedupe_key=_regeneration_job_dedupe_key(chapter_id),
        replace_completed=True,
        payload_json={
            "user_id": user_id,
            "allow_admin": allow_admin,
            "request": {
                "expected_sections": [
                    section.model_dump()
                    for section in req.expected_sections
                ],
                "ai_model": requested_ai_model,
                "max_sections": req.max_sections,
                "section_count_goal": req.section_count_goal,
            },
        },
    )
    if (
        job.status == MemoirEditingJobStatus.RUNNING.value
        and (
            (job.payload_json or {}).get("request", {}).get("max_sections") != req.max_sections
            or (job.payload_json or {}).get("request", {}).get("section_count_goal") != req.section_count_goal
        )
    ):
        raise HTTPException(409, "本章已有另一项小节整理任务在运行，请等它完成后再合并")
    if job.status == MemoirEditingJobStatus.PENDING.value:
        job.result_json = {
            "stage": "queued",
            "material_count": len(materials),
            "total_char_count": sum(
                len(material.text) for material in materials
            ),
            "batch_count": len(_material_batches(materials)),
            "requested_ai_model": requested_ai_model,
        }
        db.add(job)
    if commit:
        await db.commit()
    else:
        await db.flush()
    await db.refresh(job)
    return _job_state(
        project_id=project_id,
        chapter_id=chapter_id,
        job=job,
    )


async def _load_regeneration_job(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    job_id: str | None = None,
) -> MemoirEditingJob | None:
    statement = select(MemoirEditingJob).where(
        MemoirEditingJob.project_id == project_id,
        MemoirEditingJob.chapter_id == chapter_id,
        MemoirEditingJob.job_type
        == MemoirEditingJobType.REGENERATE_CHAPTER_SECTIONS.value,
    )
    if job_id:
        statement = statement.where(MemoirEditingJob.id == job_id)
    else:
        statement = statement.where(
            MemoirEditingJob.dedupe_key
            == _regeneration_job_dedupe_key(chapter_id)
        )
    return await db.scalar(statement)


async def get_chapter_section_regeneration_job(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    chapter_id: str,
    allow_admin: bool = False,
) -> dict[str, object]:
    await load_memoir_project(
        db,
        project_id,
        user_id,
        allow_admin=allow_admin,
    )
    job = await _load_regeneration_job(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    return _job_state(
        project_id=project_id,
        chapter_id=chapter_id,
        job=job,
    )


async def cancel_chapter_section_regeneration_job(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    chapter_id: str,
    job_id: str,
    allow_admin: bool = False,
) -> dict[str, object]:
    await load_memoir_project(
        db,
        project_id,
        user_id,
        allow_admin=allow_admin,
    )
    job = await _load_regeneration_job(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        job_id=job_id,
    )
    if job is None:
        raise HTTPException(status_code=404, detail="小节整理任务不存在")
    result_json = dict(job.result_json or {})
    result_json.update({"stage": "cancelled", "cancelled": True})
    await db.execute(
        update(MemoirEditingJob)
        .where(
            MemoirEditingJob.id == job.id,
            MemoirEditingJob.status.in_(
                {
                    MemoirEditingJobStatus.PENDING.value,
                    MemoirEditingJobStatus.RUNNING.value,
                }
            ),
        )
        .values(
            status=MemoirEditingJobStatus.SKIPPED.value,
            locked_at=None,
            lease_token=None,
            lease_expires_at=None,
            result_json=result_json,
        )
    )
    await db.commit()
    await db.refresh(job)
    return _job_state(
        project_id=project_id,
        chapter_id=chapter_id,
        job=job,
    )


async def run_chapter_section_regeneration_job(
    db: AsyncSession,
    job: MemoirEditingJob,
) -> EditingJobResult:
    payload = job.payload_json if isinstance(job.payload_json, dict) else {}
    req = MemoirSectionRegenerateRequest.model_validate(payload.get("request"))
    progress = dict(job.result_json or {})

    async def update_progress(patch: dict[str, object]) -> None:
        progress.update(patch)
        if not await editing_jobs.update_editing_job_progress(
            db,
            job,
            progress,
        ):
            raise _SectionRegenerationCancelled

    try:
        result = await regenerate_chapter_sections(
            db,
            user_id=str(payload.get("user_id") or ""),
            project_id=job.project_id,
            chapter_id=str(job.chapter_id or ""),
            req=req,
            allow_admin=payload.get("allow_admin") is True,
            progress_callback=update_progress,
        )
    except _SectionRegenerationCancelled:
        return EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            result_json={**progress, "stage": "cancelled", "cancelled": True},
        )
    progress.update(
        {
            **result,
            "stage": "completed",
            "processed_material_count": result["material_count"],
            "processed_char_count": progress.get("total_char_count", 0),
            "completed_batch_count": progress.get("batch_count", 0),
            "resolved_ai_model": result.get("ai_model"),
        }
    )
    return EditingJobResult(result_json=progress)
