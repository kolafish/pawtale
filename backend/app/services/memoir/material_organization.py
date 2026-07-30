from __future__ import annotations

from dataclasses import dataclass
import json
import re

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import (
    MemoirBookPlanStatus,
    MemoirEditingJobStatus,
    MemoirSectionMaterialAssignedBy,
    MemoirSectionStatus,
    MemoirSectionType,
)
from app.models.memoir import (
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirEditingJob,
    MemoirSection,
    MemoirSectionMaterial,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.services.ai_calls import generate_structured_with_client
from app.services.ai_factory import get_ai_service
from app.services.ai_references import ModelReferenceMap
from app.services.ai_timeouts import ai_text_timeout_seconds
from app.services.ai_types import AITextClient
from app.services.ai_workloads import AITextWorkload, ai_text_workload_policy
from app.services.memoir.editing_jobs import EditingJobResult
from app.services.memoir.material_roles import is_narrator_material
from app.services.memoir.project_ai_preferences import load_project_ai_model
from app.services.memoir.section_roles import (
    PENDING_ASSIGNMENT_SECTION_ROLE,
    PENDING_ASSIGNMENT_SECTION_TITLE,
    SECTION_SYSTEM_ROLE_KEY,
    requires_user_assignment,
)
from app.services.memoir.sections import assign_material_to_section
from app.services.memoir.transcript_projection import (
    current_transcript_version_id,
    current_utterance_text,
    source_text_hash,
)


SYSTEM_PROMPT = (
    "你是一位克制的中文回忆录素材编辑。"
    "请从整段讲述的主要叙事意图出发，判断它应归入哪个已有小章节，"
    "或者应暂时待归类；"
    "不要把一段话拆成多个事实或多个素材。"
)
ALLOWED_ACTIONS = {"assign_existing", "leave_unassigned"}
ALLOWED_SECTION_TYPES = {item.value for item in MemoirSectionType}


@dataclass(frozen=True)
class MaterialOrganizationDecision:
    action: str
    section_id: str | None
    chapter_id: str | None
    confidence: float | None
    reason: str


@dataclass(frozen=True)
class MaterialOrganizationChapterCandidate:
    id: str
    title: str


@dataclass
class MaterialOrganizationSectionCandidate:
    id: str
    chapter_id: str
    title: str
    section_type: str
    existing_material_examples: list[str]


class _MaterialOrganizationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str
    section_id: str | None = None
    chapter_id: str | None = None
    confidence: float | None = None
    reason: str = ""


def _compact(value: object, *, limit: int) -> str:
    text = value if isinstance(value, str) else ""
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _confidence(value: object) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    return min(1.0, max(0.0, float(value)))


def material_organization_excerpt(material_text: str) -> str:
    return _compact(material_text, limit=120)


def material_organization_candidates(
    *,
    chapters: list[MemoirBookPlanChapter],
    sections: list[MemoirSection],
) -> tuple[
    list[MaterialOrganizationChapterCandidate],
    list[MaterialOrganizationSectionCandidate],
]:
    chapter_candidates = [
        MaterialOrganizationChapterCandidate(
            id=chapter.id,
            title=chapter.title,
        )
        for chapter in chapters
    ]
    section_candidates = [
        MaterialOrganizationSectionCandidate(
            id=section.id,
            chapter_id=section.chapter_id,
            title=section.title,
            section_type=section.section_type,
            existing_material_examples=[
                text
                for text in (
                    material_organization_excerpt(
                        current_utterance_text(material.utterance)
                    )
                    for material in sorted(
                        section.materials,
                        key=lambda item: (item.sort_order, item.id),
                    )[-2:]
                )
                if text
            ],
        )
        for section in sections
        if (
            section.status == MemoirSectionStatus.ACTIVE.value
            and section.include_in_book
            and not requires_user_assignment(section.metadata_json)
        )
    ]
    return chapter_candidates, section_candidates


def build_material_organization_prompt(
    *,
    material_text: str,
    chapters: list[MaterialOrganizationChapterCandidate],
    sections: list[MaterialOrganizationSectionCandidate],
) -> str:
    chapter_refs = ModelReferenceMap("C", (chapter.id for chapter in chapters))
    section_refs = ModelReferenceMap("S", (section.id for section in sections))
    chapter_titles = {chapter.id: chapter.title for chapter in chapters}
    candidates = [
        {
            "section_id": section_refs.encode(section.id),
            "chapter_id": chapter_refs.encode(section.chapter_id),
            "chapter_title": chapter_titles.get(section.chapter_id, ""),
            "section_title": section.title,
            "section_type": section.section_type,
            "existing_material_examples": section.existing_material_examples,
        }
        for section in sections
    ]
    chapter_candidates = [
        {
            "chapter_id": chapter_refs.encode(chapter.id),
            "chapter_title": chapter.title,
        }
        for chapter in chapters
    ]
    return (
        "请判断下面这整段讲述应放在哪里。\n"
        "根据讲述本身的内容和主题判断，不按素材数量或固定关键词归类。"
        "如果它适合某个已有小章节，归入该小节；"
        "如果现有小章节都不合适，选择最相关的已有章节并保留为待归类素材，"
        "不要自动新建小节。\n\n"
        f"讲述：\n{material_text}\n\n"
        f"已有小章节：\n{json.dumps(candidates, ensure_ascii=False)}\n\n"
        f"可选章节：\n{json.dumps(chapter_candidates, ensure_ascii=False)}\n\n"
        "只返回 JSON："
        '{"action":"assign_existing|leave_unassigned",'
        '"section_id":null,"chapter_id":null,'
        '"confidence":0.0,"reason":""}'
        "\nassign_existing 时填写 section_id；"
        "leave_unassigned 时填写最相关的 chapter_id。"
    )


def normalize_material_organization_decision(
    payload: object,
    *,
    allowed_section_ids: set[str],
    allowed_chapter_ids: set[str],
) -> MaterialOrganizationDecision:
    data = payload if isinstance(payload, dict) else {}
    action = _compact(data.get("action"), limit=40)
    section_id = _compact(data.get("section_id"), limit=36) or None
    chapter_id = _compact(data.get("chapter_id"), limit=36) or None
    reason = _compact(data.get("reason"), limit=300)

    if action not in ALLOWED_ACTIONS:
        action = "leave_unassigned"
    if action == "assign_existing" and section_id not in allowed_section_ids:
        action = "leave_unassigned"
        section_id = None
    if action == "leave_unassigned" and chapter_id not in allowed_chapter_ids:
        chapter_id = None
    return MaterialOrganizationDecision(
        action=action,
        section_id=section_id,
        chapter_id=chapter_id,
        confidence=_confidence(data.get("confidence")),
        reason=reason,
    )


async def decide_material_organization(
    *,
    material_text: str,
    chapters: list[MaterialOrganizationChapterCandidate],
    sections: list[MaterialOrganizationSectionCandidate],
    ai_client: AITextClient,
) -> MaterialOrganizationDecision:
    chapter_refs = ModelReferenceMap("C", (chapter.id for chapter in chapters))
    section_refs = ModelReferenceMap("S", (section.id for section in sections))
    prompt = build_material_organization_prompt(
        material_text=material_text,
        chapters=chapters,
        sections=sections,
    )
    policy = ai_text_workload_policy(AITextWorkload.INTERACTIVE_STRUCTURED)
    generated = await generate_structured_with_client(
        ai_client,
        SYSTEM_PROMPT,
        prompt,
        output_model=_MaterialOrganizationOutput,
        requested_model=getattr(ai_client, "model_name", None),
        temperature=0.1,
        max_tokens=policy.max_tokens,
        timeout=ai_text_timeout_seconds(policy.timeout_profile),
        max_attempts=policy.max_attempts,
    )
    payload = generated.value.model_dump()
    if payload["section_id"] is not None:
        payload["section_id"] = section_refs.decode(payload["section_id"])
    if payload["chapter_id"] is not None:
        payload["chapter_id"] = chapter_refs.decode(payload["chapter_id"])
    return normalize_material_organization_decision(
        payload,
        allowed_section_ids={section.id for section in sections},
        allowed_chapter_ids={chapter.id for chapter in chapters},
    )


async def _load_context(
    db: AsyncSession,
    *,
    project_id: str,
    utterance_id: str,
) -> tuple[
    MemoirVoiceUtterance | None,
    list[MemoirBookPlanChapter],
    list[MemoirSection],
]:
    utterance = await db.scalar(
        select(MemoirVoiceUtterance)
        .join(
            MemoirVoiceSession,
            MemoirVoiceUtterance.voice_session_id == MemoirVoiceSession.id,
        )
        .where(
            MemoirVoiceSession.project_id == project_id,
            MemoirVoiceUtterance.id == utterance_id,
        )
        .options(selectinload(MemoirVoiceUtterance.chat_message))
    )
    plan = await db.scalar(
        select(MemoirBookPlan)
        .where(
            MemoirBookPlan.project_id == project_id,
            MemoirBookPlan.status == MemoirBookPlanStatus.CONFIRMED.value,
        )
        .options(selectinload(MemoirBookPlan.chapters))
    )
    if plan is None:
        return utterance, [], []
    chapters = sorted(
        plan.chapters,
        key=lambda item: (item.sort_order, item.id),
    )
    sections = list(
        (
            await db.execute(
                select(MemoirSection)
                .where(
                    MemoirSection.project_id == project_id,
                    MemoirSection.chapter_id.in_(
                        [chapter.id for chapter in chapters]
                    ),
                )
                .options(
                    selectinload(MemoirSection.materials).selectinload(
                        MemoirSectionMaterial.utterance
                    )
                )
                .order_by(
                    MemoirSection.chapter_id,
                    MemoirSection.sort_order,
                    MemoirSection.id,
                )
            )
        ).scalars().unique().all()
    )
    return utterance, chapters, sections


async def _pending_assignment_section(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> MemoirSection:
    sections = list(
        (
            await db.scalars(
                select(MemoirSection)
                .where(
                    MemoirSection.project_id == project_id,
                    MemoirSection.chapter_id == chapter_id,
                )
                .order_by(MemoirSection.sort_order, MemoirSection.id)
            )
        ).all()
    )
    existing = next(
        (
            section
            for section in sections
            if requires_user_assignment(section.metadata_json)
        ),
        None,
    )
    if existing is not None:
        return existing
    section = MemoirSection(
        project_id=project_id,
        chapter_id=chapter_id,
        title=PENDING_ASSIGNMENT_SECTION_TITLE,
        section_type="life_fragment",
        sort_order=max((item.sort_order for item in sections), default=-1) + 1,
        include_in_book=False,
        metadata_json={
            SECTION_SYSTEM_ROLE_KEY: PENDING_ASSIGNMENT_SECTION_ROLE,
        },
    )
    db.add(section)
    await db.flush()
    return section


async def organize_material(
    db: AsyncSession,
    *,
    project_id: str,
    utterance_id: str,
    expected_text_hash: str,
    expected_transcript_version_id: str | None,
    ai_model: str | None = None,
    ai_client: AITextClient | None = None,
) -> EditingJobResult:
    existing = await db.scalar(
        select(MemoirSectionMaterial).where(
            MemoirSectionMaterial.project_id == project_id,
            MemoirSectionMaterial.utterance_id == utterance_id,
        )
    )
    if existing is not None:
        return EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            result_json={"utterance_id": utterance_id, "reason": "already_organized"},
        )

    utterance, chapters, current_sections = await _load_context(
        db,
        project_id=project_id,
        utterance_id=utterance_id,
    )
    if utterance is None:
        return EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            result_json={"utterance_id": utterance_id, "reason": "material_missing"},
        )
    if not is_narrator_material(utterance):
        return EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            result_json={
                "utterance_id": utterance_id,
                "reason": "not_narrator_material",
            },
        )
    text = current_utterance_text(utterance)
    if (
        not text
        or source_text_hash(text) != expected_text_hash
        or current_transcript_version_id(utterance)
        != expected_transcript_version_id
    ):
        return EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            result_json={"utterance_id": utterance_id, "reason": "material_changed"},
        )
    if not chapters:
        return EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            result_json={"utterance_id": utterance_id, "reason": "book_plan_not_confirmed"},
        )

    chapter_candidates, section_candidates = material_organization_candidates(
        chapters=chapters,
        sections=current_sections,
    )
    client = ai_client or get_ai_service(ai_model)
    decision = await decide_material_organization(
        material_text=text,
        chapters=chapter_candidates,
        sections=section_candidates,
        ai_client=client,
    )
    if decision.action == "leave_unassigned" and decision.chapter_id is None:
        return EditingJobResult(
            result_json={
                "utterance_id": utterance_id,
                "action": decision.action,
                "reason": decision.reason,
            }
        )

    section_id = decision.section_id
    if decision.action == "leave_unassigned":
        section = await _pending_assignment_section(
            db,
            project_id=project_id,
            chapter_id=str(decision.chapter_id),
        )
        section_id = section.id

    if section_id is None:
        raise RuntimeError("素材整理结果缺少目标小章节")
    material = await assign_material_to_section(
        db,
        project_id=project_id,
        section_id=section_id,
        utterance_id=utterance_id,
        assigned_by=MemoirSectionMaterialAssignedBy.AI,
        assignment_reason=decision.reason or "llm_material_organization",
    )
    material.assignment_confidence = decision.confidence
    db.add(material)
    return EditingJobResult(
        result_json={
            "utterance_id": utterance_id,
            "action": decision.action,
            "section_id": section_id,
            "confidence": decision.confidence,
            "reason": decision.reason,
        }
    )


async def run_material_organization_job(
    db: AsyncSession,
    job: MemoirEditingJob,
) -> EditingJobResult:
    payload = job.payload_json or {}
    utterance_id = payload.get("utterance_id")
    expected_hash = payload.get("source_text_hash")
    if not isinstance(utterance_id, str) or not isinstance(expected_hash, str):
        return EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            error_message="素材整理任务缺少讲述或文本版本",
        )
    transcript_version_id = payload.get("transcript_version_id")
    ai_model = await load_project_ai_model(
        db,
        job.project_id,
        payload["ai_model"] if isinstance(payload.get("ai_model"), str) else None,
    )
    return await organize_material(
        db,
        project_id=job.project_id,
        utterance_id=utterance_id,
        expected_text_hash=expected_hash,
        expected_transcript_version_id=(
            transcript_version_id
            if isinstance(transcript_version_id, str)
            else None
        ),
        ai_model=ai_model,
    )
