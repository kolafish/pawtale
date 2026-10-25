from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
import logging

from fastapi import HTTPException
import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.domain.memoir.types import (
    MemoirAutomationEventType,
    MemoirBookChapterDraftStatus,
    MemoirBookPlanStatus,
    MemoirCreatedBy,
    MemoirSectionStatus,
    MemoirTranscriptPolishLevel,
)
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirBookOutput,
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirSection,
    MemoirSectionMaterial,
    MemoirTranscriptVersion,
    MemoirVoiceUtterance,
)
from app.services.ai_calls import generate_structured_with_client
from app.services.ai_factory import get_ai_service
from app.services.ai_references import ModelReferenceMap
from app.services.ai_timeouts import (
    AIProviderTimeoutError,
    ai_text_timeout_seconds,
)
from app.services.ai_types import AITextClient
from app.services.ai_workloads import AITextWorkload, ai_text_workload_policy
from app.services.memoir.book_output_state import invalidate_book_output
from app.services.memoir import automation_events
from app.services.memoir import narration_people
from app.services.memoir import fact_decisions
from app.services.memoir.material_roles import ANSWER_CONTEXT_GUIDANCE, interviewer_lines
from app.services.memoir.project_ai_preferences import (
    load_project_transcript_polish_level,
    text_polish_requirements,
)
from app.services.memoir.section_digests import (
    get_current_section_digest,
    mark_section_digests_stale,
)
from app.services.memoir.semantic_state import (
    semantic_json_signature,
    semantic_text_hash,
)
from app.services.memoir.transcript_projection import (
    TranscriptEdit, current_material_text, load_transcript_edits, normalized_material_text, transcript_text_changes,
)


CHAPTER_GENERATION_ENGINE = "llm_layered_sectioned_chapter_draft_v3"
CHAPTER_GENERATION_SYSTEM_PROMPT = (
    "你是一位中文回忆录编辑。"
    "只使用允许的素材版本及用户明确确认的事实更正整理成第一人称完整章节，不创造事实。"
)
CHAPTER_SECTION_REVISION_SYSTEM_PROMPT = (
    "你是一位谨慎的中文回忆录编辑。"
    "以当前小节正文为编辑基线，严格按用户已确认的修改思路局部修改，"
    "未涉及的现有内容必须逐字保留。"
    "新增或变更的事实只能依据允许的素材版本和用户明确给出的事实更正，不自行创造事实。"
)
CHAPTER_SECTION_GENERATION_ATTEMPTS = 2
CHAPTER_SECTION_BOUNDARY_OVERLAP_MIN_CHARS = 40
CHAPTER_SECTION_BOUNDARY_LOOKBACK_CHARS = 300
CHAPTER_PREVIOUS_SECTION_FULL_TEXT_LIMIT = 1600
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChapterMaterialInput:
    material_id: str
    section_id: str
    section_title: str
    section_type: str
    section_version: int
    section_order: int
    material_order: int
    transcript_version_id: str | None
    text: str
    interviewer_line: str = ""
    edits: tuple[TranscriptEdit, ...] = ()
    previous_text: str | None = None


@dataclass(frozen=True)
class ChapterSectionInput:
    section_id: str
    title: str
    section_type: str
    section_version: int
    sort_order: int
    materials: tuple[ChapterMaterialInput, ...]
    decisions: tuple[fact_decisions.FactDecision, ...] = ()


@dataclass(frozen=True)
class ChapterGenerationContext:
    project_id: str
    chapter_id: str
    chapter_title: str
    sections: tuple[ChapterSectionInput, ...]
    materials: tuple[ChapterMaterialInput, ...]
    input_signature: str
    polish_level: MemoirTranscriptPolishLevel = MemoirTranscriptPolishLevel.HIGH
    # People cards for those mentioned in this chapter's narration; not part of the input signature.
    people: tuple[str, ...] = ()
    previous_draft_at: datetime | None = None


@dataclass(frozen=True)
class ChapterSectionContinuity:
    summary: str
    focus: str
    key_points: tuple[str, ...]


@dataclass(frozen=True)
class GeneratedChapterSection:
    section_id: str
    title: str
    content: str
    used_material_ids: tuple[str, ...]
    user_edited: bool = False
    user_edited_at: datetime | None = None


@dataclass(frozen=True)
class GeneratedChapter:
    sections: tuple[GeneratedChapterSection, ...]
    content: str
    used_material_ids: tuple[str, ...]
    missing_source_candidates: tuple[str, ...]
    editor_notes: tuple[str, ...]
    model: str | None


@dataclass(frozen=True)
class ChapterDraftGenerationPlan:
    target_context: ChapterGenerationContext
    preserved_sections: tuple[GeneratedChapterSection, ...]
    previous_sections: tuple[GeneratedChapterSection, ...]
    protected_target_section_ids: tuple[str, ...]
    has_unmapped_manual_content: bool
    previous_model: str | None


class _GeneratedChapterSectionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str
    used_material_ids: list[str]
    missing_source_candidates: list[str] = Field(default_factory=list, description="仅列无法继续成文且必须新求证的事实；已回答、拒答、明确记不清的不列；可保留疑义成文则为空")
    editor_notes: list[str] = Field(default_factory=list)


class _RevisedChapterSectionOutput(_GeneratedChapterSectionOutput):
    title: str = Field(min_length=1, max_length=200)


class _RelatedSectionTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_id: str
    new_evidence: str
    existing_evidence: str
    reason: str


class _RelatedSectionSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sections: list[_RelatedSectionTarget] = Field(default_factory=list)


ChapterGenerationProgressCallback = Callable[
    [dict[str, object]],
    Awaitable[None],
]


def _partial_generated_chapter(
    sections: list[GeneratedChapterSection],
    *,
    missing_source_candidates: list[str],
    editor_notes: list[str],
    model: str | None,
) -> GeneratedChapter:
    return GeneratedChapter(
        sections=tuple(sections),
        content="\n\n".join(section.content for section in sections),
        used_material_ids=tuple(
            material_id
            for section in sections
            for material_id in section.used_material_ids
        ),
        missing_source_candidates=tuple(
            _deduplicated_strings(missing_source_candidates)
        ),
        editor_notes=tuple(_deduplicated_strings(editor_notes)),
        model=model,
    )


def _generated_chapter_checkpoint(
    context: ChapterGenerationContext,
    generated: GeneratedChapter,
    *,
    current_section: ChapterSectionInput | None,
) -> dict[str, object]:
    inputs = {section.section_id: section for section in context.sections}
    return {
        "stage": "generating_sections",
        "total_section_count": len(context.sections),
        "completed_section_count": len(generated.sections),
        "current_section_id": (
            current_section.section_id if current_section is not None else None
        ),
        "current_section_title": (
            current_section.title if current_section is not None else ""
        ),
        "checkpoint_input_signature": context.input_signature,
        "checkpoint_sections": [
            {
                "section_id": section.section_id,
                "title": inputs[section.section_id].title,
                "section_version": inputs[section.section_id].section_version,
                "content": section.content,
                "used_material_ids": list(section.used_material_ids),
                **({"user_edited": True} if section.user_edited else {}),
                **({"user_edited_at": section.user_edited_at.isoformat()} if section.user_edited_at else {}),
            }
            for section in generated.sections
        ],
        "checkpoint_missing_source_candidates": list(
            generated.missing_source_candidates
        ),
        "checkpoint_editor_notes": list(generated.editor_notes),
        "checkpoint_model": generated.model,
        "resolved_ai_model": generated.model,
        "provider_request_pending": False,
    }


def _generated_chapter_from_checkpoint(
    context: ChapterGenerationContext,
    checkpoint: dict[str, object] | None,
) -> GeneratedChapter | None:
    if not checkpoint:
        return None
    if checkpoint.get("checkpoint_input_signature") != context.input_signature:
        return None
    raw_sections = checkpoint.get("checkpoint_sections")
    if not isinstance(raw_sections, list) or len(raw_sections) > len(context.sections):
        return None
    generated_sections: list[GeneratedChapterSection] = []
    used_material_ids: set[str] = set()
    for index, raw_section in enumerate(raw_sections):
        if not isinstance(raw_section, dict):
            return None
        expected = context.sections[index]
        if (
            raw_section.get("section_id") != expected.section_id
            or raw_section.get("section_version") != expected.section_version
        ):
            return None
        content = str(raw_section.get("content") or "").strip()
        raw_used = raw_section.get("used_material_ids")
        if not content or not isinstance(raw_used, list):
            return None
        used = tuple(
            material_id
            for material_id in raw_used
            if isinstance(material_id, str) and material_id
        )
        allowed = {material.material_id for material in expected.materials}
        if (
            not used
            or len(used) != len(raw_used)
            or set(used) != allowed
            or used_material_ids.intersection(used)
        ):
            return None
        generated_sections.append(
            GeneratedChapterSection(
                section_id=expected.section_id,
                title=expected.title,
                content=content,
                used_material_ids=used,
                user_edited=raw_section.get("user_edited") is True,
                user_edited_at=datetime.fromisoformat(raw_section["user_edited_at"]) if raw_section.get("user_edited_at") else None,
            )
        )
        used_material_ids.update(used)
    return _partial_generated_chapter(
        generated_sections,
        missing_source_candidates=_string_list(
            checkpoint.get("checkpoint_missing_source_candidates"),
            limit=8,
            item_limit=240,
        ),
        editor_notes=_string_list(
            checkpoint.get("checkpoint_editor_notes"),
            limit=8,
            item_limit=240,
        ),
        model=(
            checkpoint["checkpoint_model"]
            if isinstance(checkpoint.get("checkpoint_model"), str)
            else None
        ),
    )


def chapter_generation_checkpoint_section_count(
    context: ChapterGenerationContext,
    checkpoint: dict[str, object] | None,
) -> int:
    generated = _generated_chapter_from_checkpoint(context, checkpoint)
    return len(generated.sections) if generated is not None else 0


def _previous_draft_section(
    section: ChapterSectionInput,
    snapshot: object,
) -> GeneratedChapterSection | None:
    if not isinstance(snapshot, dict) or snapshot.get("section_id") != section.section_id:
        return None
    content = str(snapshot.get("content") or "").strip()
    raw_used = snapshot.get("used_material_ids")
    if not content or not isinstance(raw_used, list):
        return None
    used = tuple(value for value in raw_used if isinstance(value, str) and value)
    if not used or len(used) != len(raw_used):
        return None
    if set(used) - {material.material_id for material in section.materials}:
        return None
    return GeneratedChapterSection(
        section_id=section.section_id,
        title=section.title,
        content=content,
        used_material_ids=used,
        user_edited=snapshot.get("user_edited") is True,
        user_edited_at=datetime.fromisoformat(snapshot["user_edited_at"]) if snapshot.get("user_edited_at") else None,
    )


def _preserved_draft_section(
    context: ChapterGenerationContext,
    section: ChapterSectionInput,
    snapshot: object,
) -> GeneratedChapterSection | None:
    if not isinstance(snapshot, dict):
        return None
    if (
        snapshot.get("section_id") != section.section_id
        or snapshot.get("title") != section.title
        or snapshot.get("section_type") != section.section_type
    ):
        return None
    previous = _previous_draft_section(section, snapshot)
    if previous is None:
        return None
    if {material.material_id for material in section.materials} - set(previous.used_material_ids):
        return None
    if not _section_facts_current(context, section, snapshot):
        return None
    signature = snapshot.get("input_signature")
    if signature != _section_input_signature(context, section):
        return None
    return previous


def chapter_draft_generation_plan(
    context: ChapterGenerationContext,
    latest: MemoirBookChapterDraftVersion | None,
    *,
    force_refresh: bool = False,
    refresh_unedited: bool = False,
    additional_target_section_ids: set[str] | None = None,
) -> ChapterDraftGenerationPlan:
    snapshots = (
        list(latest.section_snapshot_json)
        if latest is not None and isinstance(latest.section_snapshot_json, list)
        else []
    )
    snapshots_by_id: dict[str, object] = {}
    duplicate_ids: set[str] = set()
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        section_id = str(snapshot.get("section_id") or "")
        if not section_id:
            continue
        if section_id in snapshots_by_id:
            duplicate_ids.add(section_id)
            continue
        snapshots_by_id[section_id] = snapshot

    preserved_sections: list[GeneratedChapterSection] = []
    target_sections: list[ChapterSectionInput] = []
    previous_sections: list[GeneratedChapterSection] = []
    protected_target_section_ids: list[str] = []
    legacy_user_edit = bool(
        latest is not None
        and getattr(latest, "created_by", None) == MemoirCreatedBy.USER.value
        and not any(
            isinstance(snapshot, dict) and snapshot.get("user_edited") is True
            for snapshot in snapshots
        )
    )
    unmapped_manual_content = bool(
        latest is not None
        and getattr(latest, "created_by", None) == MemoirCreatedBy.USER.value
        and str(getattr(latest, "content", "")).strip() != "\n\n".join(
            str(snapshot.get("content") or "").strip()
            for snapshot in snapshots if isinstance(snapshot, dict)
        ).strip()
    )
    for section in context.sections:
        snapshot = snapshots_by_id.get(section.section_id)
        safe_style_refresh = bool(
            refresh_unedited
            and not legacy_user_edit
            and isinstance(snapshot, dict)
            and snapshot.get("user_edited") is not True
        )
        preserved = (
            None if (
                force_refresh
                or safe_style_refresh
                or section.section_id in duplicate_ids
                or section.section_id in (additional_target_section_ids or set())
            )
            else _preserved_draft_section(
                context,
                section,
                snapshots_by_id.get(section.section_id),
            )
        )
        if preserved is None:
            target_sections.append(section)
            previous = _previous_draft_section(
                section, snapshots_by_id.get(section.section_id)
            )
            if previous is not None:
                previous_sections.append(previous)
            if isinstance(snapshot, dict) and (
                snapshot.get("user_edited") is True or legacy_user_edit
            ):
                protected_target_section_ids.append(section.section_id)
        else:
            preserved_sections.append(
                replace(preserved, user_edited=True)
                if legacy_user_edit else preserved
            )
    if unmapped_manual_content:
        protected_target_section_ids = [section.section_id for section in target_sections]

    target_materials = tuple(
        material
        for section in target_sections
        for material in section.materials
    )
    return ChapterDraftGenerationPlan(
        target_context=replace(context, sections=tuple(target_sections), materials=target_materials),
        preserved_sections=tuple(preserved_sections),
        previous_sections=tuple(previous_sections),
        protected_target_section_ids=tuple(protected_target_section_ids),
        has_unmapped_manual_content=unmapped_manual_content,
        previous_model=latest.model if latest is not None else None,
    )


def _changed_material_ids(
    context: ChapterGenerationContext,
    latest: MemoirBookChapterDraftVersion | None,
) -> set[str]:
    saved = {
        str(item.get("material_id")): item
        for item in (getattr(latest, "material_snapshot_json", None) or [])
        if isinstance(item, dict) and item.get("material_id")
    }
    changed: set[str] = set()
    for material in context.materials:
        prior = saved.get(material.material_id)
        if (
            prior is None
            or prior.get("section_id") != material.section_id
            or prior.get("transcript_version_id") != material.transcript_version_id
            or prior.get("text_hash") != semantic_text_hash(material.text)
            or prior.get("interviewer_line", "") != material.interviewer_line
        ):
            changed.add(material.material_id)
    return changed


async def _with_previous_material_text(
    db: AsyncSession, context: ChapterGenerationContext, latest: MemoirBookChapterDraftVersion,
) -> ChapterGenerationContext:
    saved = {item["material_id"]: item for item in (latest.material_snapshot_json or [])
             if isinstance(item, dict) and item.get("material_id")}
    version_ids = {item.get("transcript_version_id") for item in saved.values()} - {None}
    unversioned = {source_id for source_id, item in saved.items() if not item.get("transcript_version_id")}
    versions = (await db.scalars(select(MemoirTranscriptVersion).where(or_(
        MemoirTranscriptVersion.id.in_(version_ids),
        and_(MemoirTranscriptVersion.source_artifact_id.in_(unversioned),
             MemoirTranscriptVersion.created_at <= latest.created_at),
    )))).all() if version_ids or unversioned else []
    texts = {version.id: normalized_material_text(version.content) for version in versions}
    history = {(version.source_artifact_id, semantic_text_hash(texts[version.id])): texts[version.id]
               for version in versions if version.source_artifact_id in unversioned}
    materials = []
    for material in context.materials:
        prior = saved.get(material.material_id, {})
        text = texts.get(prior.get("transcript_version_id")) if prior.get("transcript_version_id") else history.get(
            (material.material_id, prior.get("text_hash")),
        )
        if text is not None and semantic_text_hash(text) != prior.get("text_hash"):
            text = None
        materials.append(replace(material, previous_text=text))
    by_id = {material.material_id: material for material in materials}
    return replace(context, materials=tuple(materials), sections=tuple(
        replace(section, materials=tuple(by_id[item.material_id] for item in section.materials))
        for section in context.sections
    ))


def _confirmed_material_update(material: ChapterMaterialInput, changed: bool) -> str:
    message = "已有素材：以用户确认正文为准，不从旧转写全文恢复旧说法。\n"
    if not changed:
        return message
    if material.previous_text is None:
        return message + "历史转写版本不可用：本条机器更新不能可靠比较，保留已确认正文，等待用户核对素材。\n"
    changes = transcript_text_changes(material.previous_text, material.text, label="转写版本变化")
    if not changes:
        return message
    return (message + "相对上次正文所用转写版本的变化（可能来自机器重识别，不是用户确认）：\n"
            + changes + "\n仅处理变化片段；未变片段只是定位上下文，不能恢复旧事实。"
            "与用户确认正文或明确更正冲突时保留用户说法，在 missing_source_candidates 中说明待核对处。\n")


async def select_related_sections(
    context: ChapterGenerationContext,
    plan: ChapterDraftGenerationPlan,
    *,
    changed_material_ids: set[str],
    ai_model: str | None,
    ai_client: AITextClient | None,
) -> dict[str, str]:
    """Find existing text requiring a concrete transition or duplicate fix.

    This is a bounded editorial judgment, not a new persisted job state. A
    failed or vague selection leaves the source-affected sections as the only
    targets; it never makes the whole chapter an implicit rewrite.
    """
    candidates = [section for section in plan.preserved_sections if not section.user_edited]
    new_materials = [
        material for material in plan.target_context.materials
        if material.material_id in changed_material_ids
    ]
    if not candidates or not new_materials:
        return {}
    references = ModelReferenceMap("S", (section.section_id for section in candidates))
    changed_text = "\n".join((f"对应提问：{material.interviewer_line}\n" if material.interviewer_line else "") + material.text for material in new_materials)
    candidates_by_id = {section.section_id: section for section in candidates}
    candidate_blocks = "\n\n".join(
        f"section_id: {references.encode(section.section_id)}\n"
        f"标题：{section.title}\n"
        f"现有正文节选：{section.content[:280]}"
        f"{' …… ' + section.content[-280:] if len(section.content) > 560 else ''}"
        for section in candidates
    )
    prompt = f"""新讲述已经归入《{context.chapter_title}》的相关小节。请判断其他现有小节是否确实需要局部调整，以避免具体事实重复或修正明确的前后衔接。普通主题相似、风格统一、整体润色都不足以选择旧小节；可以返回空数组。

新讲述原文：
{changed_text[:4800]}

其他小节：
{candidate_blocks}

严格返回 JSON：{{"sections":[{{"section_id":"S1","new_evidence":"新讲述中连续引用的具体短语","existing_evidence":"该小节节选中连续引用的具体短语","reason":"说明两处事实的关系以及应如何局部调整"}}]}}
每条证据必须从对应文字原样摘取至少 4 个字符；只选有两处具体证据的小节，不必选择相邻节，也不要默认选择全部小节。"""
    policy = ai_text_workload_policy(AITextWorkload.LONG_GENERATION_STRUCTURED)
    try:
        response = await generate_structured_with_client(
            ai_client or get_ai_service(ai_model),
            "你是一位谨慎的回忆录编辑，只判断新素材与现有小节的具体内容关系。",
            prompt,
            output_model=_RelatedSectionSelection,
            requested_model=ai_model,
            temperature=0.1,
            max_tokens=min(policy.max_tokens, 1200),
            timeout=ai_text_timeout_seconds(policy.timeout_profile),
            max_attempts=1,
        )
    except (AIProviderTimeoutError, ValueError, RuntimeError, httpx.HTTPError) as exc:
        logger.warning(
            "memoir related section selection failed chapter_id=%s error=%s",
            context.chapter_id,
            exc,
        )
        return {}
    selected: dict[str, str] = {}
    for item in response.value.sections:
        try:
            section_id = references.decode(item.section_id)
        except ValueError:
            continue
        previous = candidates_by_id.get(section_id)
        new_evidence = item.new_evidence.strip()
        existing_evidence = item.existing_evidence.strip()
        reason = " ".join(item.reason.split())[:240]
        if (
            previous is None
            or len(new_evidence) < 4
            or len(existing_evidence) < 4
            or len(reason) < 12
            or new_evidence not in changed_text
            or existing_evidence not in previous.content
        ):
            continue
        selected[section_id] = reason
    return selected


def chapter_draft_is_current(
    context: ChapterGenerationContext,
    latest: MemoirBookChapterDraftVersion | None,
) -> bool:
    if latest is None or not isinstance(latest.section_snapshot_json, list):
        return False
    snapshot_section_ids = [
        str(snapshot.get("section_id") or "")
        for snapshot in latest.section_snapshot_json
        if isinstance(snapshot, dict)
    ]
    if snapshot_section_ids != [
        section.section_id for section in context.sections
    ]:
        return False
    plan = chapter_draft_generation_plan(context, latest)
    return (
        not plan.target_context.sections
        and len(plan.preserved_sections) == len(context.sections)
    )


def _render_policy_tag(context: ChapterGenerationContext) -> str:
    return f"{CHAPTER_GENERATION_ENGINE}:polish={context.polish_level.value}"


def chapter_draft_render_policy_current(
    context: ChapterGenerationContext,
    latest: MemoirBookChapterDraftVersion | None,
) -> bool:
    if latest is None:
        return False
    prompt_snapshot = str(getattr(latest, "prompt_snapshot", "") or "")
    if prompt_snapshot.startswith(f"{CHAPTER_GENERATION_ENGINE}:polish="):
        recorded = prompt_snapshot.split(":polish=", 1)[1].split(":", 1)[0]
        return recorded == context.polish_level.value
    return False


def chapter_draft_sources_still_included(
    context: ChapterGenerationContext,
    version: MemoirBookChapterDraftVersion | None,
) -> bool:
    """Keep an older readable draft while adding material, never after excluding its sources."""
    if version is None or not version.content.strip() or not version.section_snapshot_json:
        return False
    allowed = {
        section.section_id: {material.material_id for material in section.materials}
        for section in context.sections
    }
    if not chapter_draft_facts_current(context, version):
        return False
    return all(
        section.get("section_id") in allowed
        and set(section.get("used_material_ids", [])).issubset(allowed[section["section_id"]])
        for section in version.section_snapshot_json
    )


def chapter_draft_facts_current(
    context: ChapterGenerationContext, version: MemoirBookChapterDraftVersion | None,
) -> bool:
    snapshots = {
        snapshot["section_id"]: snapshot
        for snapshot in (version.section_snapshot_json if version else [])
    }
    return all(
        _section_facts_current(context, section, snapshots.get(section.section_id, {}))
        for section in context.sections
    )


def _section_facts_current(
    context: ChapterGenerationContext, section: ChapterSectionInput, snapshot: dict,
) -> bool:
    if snapshot.get("fact_decision_signature", "") == fact_decisions.signature(section.decisions):
        return True
    # #1387 omitted the fact stamp, but its input signature includes the exact
    # source AND facts. This proof recovers that version without rewriting it.
    return (
        "fact_decision_signature" not in snapshot
        and snapshot.get("input_signature") == _section_input_signature(context, section)
    )


async def pending_fact_chapter_ids(
    db: AsyncSession, *, project_id: str,
    chapter_versions: list[tuple[MemoirBookPlanChapter, MemoirBookChapterDraftVersion]],
) -> set[str]:
    decisions = await fact_decisions.load_fact_decisions(db, project_id=project_id)
    if not decisions or not chapter_versions:
        return set()
    # Facts depend on source membership, not the expensive writing context
    # (speaker history, people, transcript edits or polish preferences).
    rows = (await db.execute(select(
        MemoirSectionMaterial, MemoirSection.chapter_id, MemoirBookPlan.status,
    ).join(MemoirSection, MemoirSection.id == MemoirSectionMaterial.section_id)
      .join(MemoirBookPlanChapter, MemoirBookPlanChapter.id == MemoirSection.chapter_id)
      .join(MemoirBookPlan, MemoirBookPlan.id == MemoirBookPlanChapter.plan_id).where(
        MemoirSection.project_id == project_id,
        MemoirSection.chapter_id.in_([chapter.id for chapter, _ in chapter_versions]),
        MemoirSection.status == MemoirSectionStatus.ACTIVE.value,
        MemoirSection.include_in_book.is_(True),
        MemoirSectionMaterial.include_in_book.is_(True),
    ).options(joinedload(MemoirSectionMaterial.utterance),
              joinedload(MemoirSectionMaterial.transcript_version)))).all()
    sources: dict[str, dict[str, set[str]]] = {}
    unconfirmed = set()
    for material, chapter_id, plan_status in rows:
        if plan_status != MemoirBookPlanStatus.CONFIRMED.value:
            unconfirmed.add(chapter_id)
        if current_material_text(material):
            sources.setdefault(chapter_id, {}).setdefault(material.section_id, set()).add(material.utterance_id)
    pending = set()
    for chapter, version in chapter_versions:
        snapshots = {item["section_id"]: item for item in version.section_snapshot_json}
        if chapter.id in unconfirmed or not sources.get(chapter.id):
            if fact_decisions.for_sources(
                decisions, chapter_id=chapter.id, material_ids=version.used_material_ids_json,
            ) or any(chapter.id in decision.chapter_ids for decision in decisions):
                pending.add(chapter.id)
            continue
        mismatches = [section_id for section_id, material_ids in sources.get(chapter.id, {}).items()
            if snapshots.get(section_id, {}).get("fact_decision_signature", "") != fact_decisions.signature(
                fact_decisions.for_sources(decisions, chapter_id=chapter.id, material_ids=material_ids)
            )]
        if not mismatches:
            continue
        if any("fact_decision_signature" in snapshots.get(section_id, {}) for section_id in mismatches):
            pending.add(chapter.id)
            continue
        # Only old workbench snapshots need the source-signature recovery path.
        try:
            context = await load_chapter_generation_context(db, project_id=project_id, chapter_id=chapter.id)
        except HTTPException as exc:
            if exc.status_code not in {409, 422}:
                raise
            pending.add(chapter.id)
            continue
        if not chapter_draft_facts_current(context, version):
            pending.add(chapter.id)
    return pending


async def load_chapter_draft_generation_plan(
    db: AsyncSession,
    *,
    context: ChapterGenerationContext,
) -> ChapterDraftGenerationPlan:
    latest = await latest_chapter_draft_version(
        db,
        project_id=context.project_id,
        chapter_id=context.chapter_id,
    )
    return chapter_draft_generation_plan(
        context,
        latest,
        refresh_unedited=not chapter_draft_render_policy_current(context, latest),
    )


class ChapterGenerationNeedsMaterial(HTTPException):
    def __init__(self) -> None:
        super().__init__(status_code=422, detail="章节没有可用于生成正文的素材")


def chapter_generation_context(
    chapter: MemoirBookPlanChapter,
    *,
    polish_level: MemoirTranscriptPolishLevel,
    interviewer_lines: dict[str, str] | None = None,
    decisions: tuple[fact_decisions.FactDecision, ...] = (),
    transcript_edits: dict[str, tuple[TranscriptEdit, ...]] | None = None,
) -> ChapterGenerationContext:
    if chapter.plan.status != MemoirBookPlanStatus.CONFIRMED.value:
        raise HTTPException(status_code=409, detail="请先确认章节规划")
    sections: list[ChapterSectionInput] = []
    materials: list[ChapterMaterialInput] = []
    for section in sorted(
        chapter.sections,
        key=lambda item: (item.sort_order, item.id),
    ):
        if (
            section.status != MemoirSectionStatus.ACTIVE.value
            or not section.include_in_book
        ):
            continue
        section_materials: list[ChapterMaterialInput] = []
        for material in sorted(
            section.materials,
            key=lambda item: (item.sort_order, item.id),
        ):
            if not material.include_in_book:
                continue
            text = normalized_material_text(current_material_text(material))
            if not text:
                continue
            item = ChapterMaterialInput(
                material_id=material.utterance_id,
                section_id=section.id,
                section_title=section.title,
                section_type=section.section_type,
                section_version=section.version,
                section_order=section.sort_order,
                material_order=material.sort_order,
                transcript_version_id=material.transcript_version_id,
                text=text,
                edits=(transcript_edits or {}).get(material.transcript_version_id, ()),
                interviewer_line=(interviewer_lines or {}).get(material.utterance.chat_message_id or "", ""),
            )
            section_materials.append(item)
            materials.append(item)
        if section_materials:
            sections.append(
                ChapterSectionInput(
                    section_id=section.id,
                    title=section.title,
                    section_type=section.section_type,
                    section_version=section.version,
                    sort_order=section.sort_order,
                    materials=tuple(section_materials),
                    decisions=fact_decisions.for_sources(
                        decisions, chapter_id=chapter.id,
                        material_ids=(item.material_id for item in section_materials),
                    ),
                )
            )
    if not materials:
        raise ChapterGenerationNeedsMaterial()
    signature = semantic_json_signature(
        {
            "chapter": {
                "id": chapter.id,
                "title": chapter.title,
                "include_in_book": chapter.include_in_book,
            },
            "materials": [
                {
                    "material_id": item.material_id,
                    "section_id": item.section_id,
                    "section_title": item.section_title,
                    "section_type": item.section_type,
                    "section_version": item.section_version,
                    "section_order": item.section_order,
                    "material_order": item.material_order,
                    "transcript_version_id": item.transcript_version_id,
                    "text_hash": semantic_text_hash(item.text),
                    **({"interviewer_line": item.interviewer_line} if item.interviewer_line else {}),
                }
                for item in materials
            ],
            "engine": CHAPTER_GENERATION_ENGINE,
            "polish_level": polish_level.value,
            **({"fact_decisions": [
                [section.section_id, fact_decisions.signature(section.decisions)]
                for section in sections
            ]} if any(section.decisions for section in sections) else {}),
        }
    )
    return ChapterGenerationContext(
        project_id=chapter.project_id,
        chapter_id=chapter.id,
        chapter_title=chapter.title,
        sections=tuple(sections),
        materials=tuple(materials),
        input_signature=signature,
        polish_level=polish_level,
    )


async def load_chapter_generation_context(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> ChapterGenerationContext:
    chapter = await db.scalar(
        select(MemoirBookPlanChapter)
        .join(MemoirBookPlan, MemoirBookPlanChapter.plan_id == MemoirBookPlan.id)
        .where(
            MemoirBookPlanChapter.project_id == project_id,
            MemoirBookPlanChapter.id == chapter_id,
        )
        .execution_options(populate_existing=True)
        .options(
            selectinload(MemoirBookPlanChapter.plan),
            selectinload(MemoirBookPlanChapter.sections)
            .selectinload(MemoirSection.materials)
            .selectinload(MemoirSectionMaterial.utterance),
            selectinload(MemoirBookPlanChapter.sections)
            .selectinload(MemoirSection.materials)
            .selectinload(MemoirSectionMaterial.transcript_version),
        )
    )
    if chapter is None:
        raise HTTPException(status_code=404, detail="章节不存在")
    message_ids = [material.utterance.chat_message_id for section in chapter.sections for material in section.materials]
    decisions = await fact_decisions.load_fact_decisions(db, project_id=project_id)
    context = chapter_generation_context(
        chapter,
        polish_level=await load_project_transcript_polish_level(db, project_id),
        interviewer_lines=await interviewer_lines(db, message_ids),
        decisions=decisions,
        transcript_edits=await load_transcript_edits(
            db, [material for section in chapter.sections for material in section.materials],
        ),
    )
    return replace(context, people=await narration_people.people_lines_for(db, project_id, message_ids))


def build_chapter_generation_prompt(
    context: ChapterGenerationContext,
    *,
    editorial_guidance: list[str],
    chapter_sections: tuple[ChapterSectionInput, ...] | None = None,
    current_section_content: str = "",
    current_section_user_edited: bool = False,
    current_section_user_edited_at: datetime | None = None,
    revision_instruction: str = "",
    source_update_reason: str = "",
    previous_section_context: str = "",
    changed_material_ids: set[str] | None = None,
    previous_material_ids: set[str] | None = None,
) -> str:
    section, = context.sections
    material_refs = ModelReferenceMap(
        "M",
        (material.material_id for material in context.materials),
    )
    required_refs = "、".join(
        material_refs.encode(material.material_id)
        for material in context.materials
        if material.material_id in (changed_material_ids or set())
    )
    required_materials = (
        f"本次必须融入的讲述：{required_refs}。这些讲述已归属当前小节，尚未写入正文或已更正。"
        "请逐条将其中的真实经历、事实和感受融入正文，used_material_ids 是归属核对清单，并非事实核验结论；"
        "不能只列编号而省略内容。重复内容可以合并，访谈过渡语无需写入。\n\n"
        if required_refs else ""
    )
    outline_sections = chapter_sections or context.sections
    outline = "\n".join(
        (
            f"{index + 1}. {item.title}"
            f"{'（本次生成）' if item.section_id == section.section_id else ''}"
        )
        for index, item in enumerate(outline_sections)
    )
    edit_cutoff = current_section_user_edited_at or context.previous_draft_at
    blocks = "\n".join(
        [
            f"小节标题: {section.title}",
            f"小节类型: {section.section_type}",
            fact_decisions.prompt(
                tuple(decision for decision in section.decisions if not (
                    current_section_user_edited and edit_cutoff
                    and (decision.confirmed_at is None or decision.confirmed_at <= edit_cutoff)
                )),
                edits=tuple(edit for material in section.materials for edit in material.edits if not (
                    current_section_user_edited and edit_cutoff
                    and edit.edited_at <= edit_cutoff
                )),
            ),
            "允许素材：",
            *[
                (
                    f"material_id: {material_refs.encode(material.material_id)}\n"
                    + (f"对应提问: {material.interviewer_line}\n" if material.interviewer_line else "")
                    + (
                        _confirmed_material_update(material, material.material_id in (changed_material_ids or set()))
                        if current_section_user_edited and current_section_content
                        and previous_material_ids is not None and material.material_id in previous_material_ids
                        else f"素材版本{'（本次新增或更正）' if changed_material_ids and material.material_id in changed_material_ids else ''}: {material.text}\n"
                    )
                )
                for material in section.materials
            ],
        ]
    )
    people = (
        "书中人物（讲述者讲过的人，只用来统一称呼、看懂“他”“她”指谁，不据此增加情节）：\n"
        + "\n".join(f"- {line}" for line in context.people)
        + "\n\n"
        if context.people
        else ""
    )
    guidance = "\n".join(
        f"- {' '.join(item.split())[:240]}"
        for item in editorial_guidance[:8]
        if item.strip()
    ) or "（无额外编辑建议）"
    polish_requirements = (
        "本次仅按已确认修改思路局部编辑，不执行通用润色。"
        if revision_instruction
        else f"文字润色强度：{context.polish_level.value}\n{text_polish_requirements(context.polish_level)}"
    )
    writing_rules = "" if revision_instruction else (
        "- 结合完整小节顺序安排本节开头和结尾；只在本节素材支持时自然承接，不强行添加过渡，也不要复述前一小节。\n"
        "- 只能使用输入中的事实和感受，可以调整顺序、合并重复。\n"
        "- 正文根据内容自然分段，段落之间用换行分隔。\n"
    )
    task = (
        f"请按已确认修改思路局部修改回忆录章节“{context.chapter_title}”中的当前小节。"
        if revision_instruction
        else f"请把下面的素材版本整理为回忆录章节“{context.chapter_title}”中的当前小节。"
    )
    revision = ""
    if revision_instruction:
        revision = f"""
本次是按用户要求修改现有小节，不是脱离原稿重新创作。
当前小节标题：
{context.sections[0].title}

当前小节正文：
{current_section_content}

用户修改要求：
{revision_instruction}

修改规则：
- 以当前小节正文为编辑基线，只修改已确认思路明确涉及的对象和范围，再输出修改后的标题和完整正文。
- 如果修改要求涉及标题中的错字或表达，必须同步修改标题；否则标题保持不变。
- 同一个错字或名称同时出现在标题和正文时，两处必须同步修正。
- 未涉及的文字、用户已写内容、段落及换行必须逐字保留，不删除、不改写。
- 只有已确认思路明确要求时，才能在指定范围内调整措辞、语气、详略、顺序或段落结构；通用整理、编辑建议和润色要求不得扩大修改范围。
- 下方素材版本仅用于校对本次新增或变更事实的依据，不能因其中没有对应内容而删除当前正文中本次未涉及的说明。
- 用户在指令中明确给出的事实更正可以采用；其他新增或变更事实必须有下方素材版本支持。
- 用户只要求“补充更多细节”却没有说出具体事实、且素材版本也不支持时，不得自行虚构，应放入 missing_source_candidates。
- 不要把本次修改指令或修改过程的解释新增到正文。
"""
    elif current_section_content:
        reason = source_update_reason or "本小节的素材版本发生了变化。"
        ownership = (
            "现有正文已经由用户修改或确认。其与未变化的旧素材不同之处属于已采纳的表达，"
            "不能仅因旧素材不支持就撤销。只按本次新增素材或明确更正更新实际涉及的事实，保留其余内容。"
            if current_section_user_edited else
            "保留仍受素材支持的事实；若素材不支持原有表述，不要延续该表述。"
        )
        revision = f"""
现有小节正文：
{current_section_content}

本次局部更新原因：{reason}
请在现有正文基础上融入下方素材或调整必要的衔接。{ownership}只改与本次变化有关的文字。标题保持输入标题。
"""
    title_output = (
        '  "title": "小节完整标题，修改要求未涉及标题时保持输入标题",\n'
        if revision_instruction else ""
    )
    return f"""{task}

本章完整小节顺序：
{outline}
{revision}
编辑建议：
{guidance}

{polish_requirements}

前一小节衔接参考：
{previous_section_context or "（这是本章第一个小节）"}

{people}按既定顺序排列的允许素材：
{blocks}

{required_materials}输出严格 JSON：
{{
{title_output}  "content": "当前小节完整、连贯、第一人称的正文",
  "used_material_ids": ["本小节全部已归属素材的 material_id，含保留来源"],
  "missing_source_candidates": ["无法继续成文且必须新求证的事实，或用户明确要求但尚未提供的事实；可保留疑义成文则为空数组"],
  "editor_notes": ["可选的简短编辑说明"]
}}

要求：
- 本次只生成标记为“本次生成”的一个小节，不得复述其他小节的内容。
- material_id 是本次请求内的短编号，必须原样返回；used_material_ids 完整列出本小节全部已归属素材，包括当前正文保留的来源，不能只列变化项。
- 只有本次按用户要求修改且要求涉及标题时，才可修改输入标题。
- 只能使用当前小节的允许素材，不能把素材移动到其他小节。
- 前一小节内容只用于自然衔接和避免重复，不是本小节可新增事实的来源。
{writing_rules}- 不要为了完整而虚构时间、地点、人物、因果或心理活动。
- {ANSWER_CONTEXT_GUIDANCE}
- 用回答明确支持的主语和对象补足省略，使正文脱离问答也能读懂；不能把提问原样当作讲述者经历。保留否定、转述来源、过去与现在的观点差异及局部不确定程度。
- 编辑建议和修改计划仅决定编辑范围，不提供人生事实；确认计划不等于确认计划中新增的事实。只有素材与单独列出的用户原话中的明确事实可用于新增或更正。
- missing_source_candidates 只列必要疑义，已回答、拒答或明确记不清的内容不得列入。能保留不确定程度写清楚时必须为空，不为丰富情节制造缺失项。
- 不在正文中{"新增" if revision_instruction else "出现"} material_id、编辑指令、“叙述者”等后台术语。
"""


def _single_section_context(
    context: ChapterGenerationContext,
    section: ChapterSectionInput,
) -> ChapterGenerationContext:
    return replace(context, sections=(section,), materials=section.materials)


def _deduplicated_strings(
    values: list[str],
    *,
    limit: int = 8,
) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))[:limit]


def _reject_repeated_section_boundary(
    previous_content: str,
    current_content: str,
) -> None:
    previous = "".join(previous_content.split())[
        -CHAPTER_SECTION_BOUNDARY_LOOKBACK_CHARS:
    ]
    current = "".join(current_content.split())
    for size in range(min(len(previous), len(current)), 0, -1):
        if size < CHAPTER_SECTION_BOUNDARY_OVERLAP_MIN_CHARS:
            return
        if previous.endswith(current[:size]):
            raise ValueError("小节开头大段重复了上一小节结尾")


def _string_list(value: object, *, limit: int, item_limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = " ".join(item.split())[:item_limit] if isinstance(item, str) else ""
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


async def load_section_continuity(
    db: AsyncSession,
    *,
    context: ChapterGenerationContext,
) -> dict[str, ChapterSectionContinuity]:
    result: dict[str, ChapterSectionContinuity] = {}
    for section in context.sections:
        digest = await get_current_section_digest(
            db,
            project_id=context.project_id,
            section_id=section.section_id,
        )
        if digest is None or not isinstance(digest.digest_json, dict):
            continue
        result[section.section_id] = ChapterSectionContinuity(
            summary=" ".join(
                str(digest.digest_json.get("summary") or "").split()
            )[:600],
            focus=" ".join(
                str(digest.digest_json.get("focus") or "").split()
            )[:300],
            key_points=tuple(
                _string_list(
                    digest.digest_json.get("covered_points"),
                    limit=8,
                    item_limit=120,
                )
            ),
        )
    return result


def previous_section_context(
    *,
    section: ChapterSectionInput | None,
    content: str,
    continuity: ChapterSectionContinuity | None,
) -> str:
    body = content.strip()
    if section is None or not body:
        return ""
    if len("".join(body.split())) <= CHAPTER_PREVIOUS_SECTION_FULL_TEXT_LIMIT:
        return f"标题：{section.title}\n完整正文：\n{body}"
    if continuity is not None and (
        continuity.summary or continuity.focus or continuity.key_points
    ):
        points = "；".join(continuity.key_points) or "（无单独要点）"
        return (
            f"标题：{section.title}\n"
            f"摘要：{continuity.summary or '（无）'}\n"
            f"重点：{continuity.focus or '（无）'}\n"
            f"关键要素（人物、地点、事件等）：{points}"
        )
    compact = " ".join(body.split())
    return (
        f"标题：{section.title}\n"
        f"正文开头节选：{compact[:240]}\n"
        f"正文结尾节选：{compact[-240:]}"
    )


def normalize_generated_section(
    payload: _GeneratedChapterSectionOutput,
    *,
    section: ChapterSectionInput,
    model: str | None,
) -> GeneratedChapter:
    title = (
        payload.title.strip()
        if isinstance(payload, _RevisedChapterSectionOutput)
        else section.title
    )
    if not title:
        raise ValueError("chapter section title is empty")
    content = payload.content.strip()
    if not content:
        raise ValueError("chapter section content is empty")
    allowed_material_ids = {material.material_id for material in section.materials}
    material_refs = ModelReferenceMap(
        "M", (material.material_id for material in section.materials),
    )
    used = material_refs.decode_many(
        payload.used_material_ids, limit=len(allowed_material_ids),
    )
    if not used:
        raise ValueError("chapter section returned no used material")
    internal_ids = allowed_material_ids | {section.section_id}
    if any(
        internal_id in value
        for internal_id in internal_ids
        for value in (title, content)
    ):
        raise ValueError("chapter section exposed internal id")
    generated = GeneratedChapterSection(
        section_id=section.section_id,
        title=title,
        content=content,
        used_material_ids=tuple(used),
    )
    return GeneratedChapter(
        sections=(generated,),
        content=content,
        used_material_ids=generated.used_material_ids,
        missing_source_candidates=tuple(
            _string_list(payload.missing_source_candidates, limit=8, item_limit=240)
        ),
        editor_notes=tuple(
            _string_list(payload.editor_notes, limit=8, item_limit=240)
        ),
        model=model,
    )


async def generate_chapter(
    context: ChapterGenerationContext,
    *,
    editorial_guidance: list[str],
    ai_model: str | None = None,
    ai_client: AITextClient | None = None,
    checkpoint: dict[str, object] | None = None,
    progress_callback: ChapterGenerationProgressCallback | None = None,
    chapter_sections: tuple[ChapterSectionInput, ...] | None = None,
    preserved_sections: tuple[GeneratedChapterSection, ...] = (),
    previous_sections: tuple[GeneratedChapterSection, ...] = (),
    preserved_model: str | None = None,
    section_continuity: dict[str, ChapterSectionContinuity] | None = None,
    related_section_reasons: dict[str, str] | None = None,
    changed_material_ids: set[str] | None = None,
) -> GeneratedChapter:
    client = (
        ai_client or get_ai_service(ai_model)
    ) if context.sections else None
    full_sections = chapter_sections or context.sections
    target_section_ids = {section.section_id for section in context.sections}
    preserved_by_id = {
        section.section_id: section for section in preserved_sections
    }
    previous_by_id = {
        section.section_id: section for section in previous_sections
    }
    full_section_ids = [section.section_id for section in full_sections]
    if (
        len(target_section_ids) != len(context.sections)
        or len(preserved_by_id) != len(preserved_sections)
        or target_section_ids.intersection(preserved_by_id)
        or target_section_ids | set(preserved_by_id) != set(full_section_ids)
        or len(set(full_section_ids)) != len(full_section_ids)
    ):
        raise ValueError("chapter generation plan does not cover current sections")
    resumed = _generated_chapter_from_checkpoint(context, checkpoint)
    generated_target_sections = (
        list(resumed.sections) if resumed is not None else []
    )
    generated_targets_by_id = {
        section.section_id: section
        for section in generated_target_sections
    }
    missing_source_candidates = (
        list(resumed.missing_source_candidates) if resumed is not None else []
    )
    editor_notes = list(resumed.editor_notes) if resumed is not None else []
    resolved_model = (
        getattr(client, "model_name", ai_model)
        if client is not None
        else preserved_model
    )
    pending_sections = [
        section
        for section in context.sections
        if section.section_id not in generated_targets_by_id
    ]
    if progress_callback is not None:
        await progress_callback(
            _generated_chapter_checkpoint(
                context,
                _partial_generated_chapter(
                    generated_target_sections,
                    missing_source_candidates=missing_source_candidates,
                    editor_notes=editor_notes,
                    model=resolved_model,
                ),
                current_section=(
                    pending_sections[0] if pending_sections else None
                ),
            )
        )
    completed_sections: list[GeneratedChapterSection] = []
    for section_index, section in enumerate(full_sections):
        current = preserved_by_id.get(section.section_id)
        if current is None:
            current = generated_targets_by_id.get(section.section_id)
        if current is None:
            if client is None:
                raise ValueError("chapter generation client is missing")
            previous = (
                full_sections[section_index - 1]
                if section_index > 0
                else None
            )
            previous_content = (
                completed_sections[-1].content if completed_sections else ""
            )
            if progress_callback is not None:
                await progress_callback({
                    **_generated_chapter_checkpoint(context, _partial_generated_chapter(
                        generated_target_sections, missing_source_candidates=missing_source_candidates,
                        editor_notes=editor_notes, model=resolved_model,
                    ), current_section=section),
                    "provider_request_pending": True,
                })
            generated_section = await generate_chapter_section(
                context,
                section=section,
                editorial_guidance=editorial_guidance,
                chapter_sections=full_sections,
                previous_section_content=previous_content,
                preceding_section_context=previous_section_context(
                    section=previous,
                    content=previous_content,
                    continuity=(
                        (section_continuity or {}).get(previous.section_id)
                        if previous is not None
                        else None
                    ),
                ),
                current_section_content=(
                    previous_by_id[section.section_id].content
                    if section.section_id in previous_by_id else ""
                ),
                previous_material_ids=set(previous_by_id[section.section_id].used_material_ids)
                    if section.section_id in previous_by_id else None,
                current_section_user_edited=bool(
                    section.section_id in previous_by_id and previous_by_id[section.section_id].user_edited
                ),
                current_section_user_edited_at=(
                    previous_by_id[section.section_id].user_edited_at
                    if section.section_id in previous_by_id else None
                ),
                source_update_reason=(related_section_reasons or {}).get(
                    section.section_id, ""
                ),
                required_material_ids={
                    material.material_id for material in section.materials
                    if material.material_id in (changed_material_ids or set())
                },
                ai_model=ai_model,
                ai_client=client,
            )
            current = generated_section.sections[0]
            if section.section_id in previous_by_id:
                current = replace(
                    current,
                    user_edited=previous_by_id[section.section_id].user_edited,
                    user_edited_at=previous_by_id[section.section_id].user_edited_at,
                )
            generated_target_sections.append(current)
            generated_targets_by_id[current.section_id] = current
            missing_source_candidates.extend(
                generated_section.missing_source_candidates
            )
            editor_notes.extend(generated_section.editor_notes)
            partial = _partial_generated_chapter(
                generated_target_sections,
                missing_source_candidates=missing_source_candidates,
                editor_notes=editor_notes,
                model=resolved_model,
            )
            if progress_callback is not None:
                await progress_callback(
                    _generated_chapter_checkpoint(
                        context,
                        partial,
                        current_section=section,
                    )
                )
        completed_sections.append(current)
    for section in full_sections:
        previous = previous_by_id.get(section.section_id)
        if previous is not None and previous.user_edited and any(
            material.material_id in previous.used_material_ids
            and material.material_id in (changed_material_ids or set())
            and material.previous_text is None for material in section.materials
        ):
            missing_source_candidates.append(
                f"《{section.title}》的部分素材缺少历史转写版本，机器更新未合并；请核对素材后确认候选稿。"
            )
    return _partial_generated_chapter(
        completed_sections,
        missing_source_candidates=missing_source_candidates,
        editor_notes=editor_notes,
        model=resolved_model,
    )


async def generate_chapter_section(
    context: ChapterGenerationContext,
    *,
    section: ChapterSectionInput,
    editorial_guidance: list[str],
    chapter_sections: tuple[ChapterSectionInput, ...],
    previous_section_content: str = "",
    preceding_section_context: str = "",
    current_section_content: str = "",
    current_section_user_edited: bool = False,
    current_section_user_edited_at: datetime | None = None,
    revision_instruction: str = "",
    source_update_reason: str = "",
    required_material_ids: set[str] | None = None,
    previous_material_ids: set[str] | None = None,
    ai_model: str | None = None,
    ai_client: AITextClient | None = None,
) -> GeneratedChapter:
    client = ai_client or get_ai_service(ai_model)
    section_context = _single_section_context(context, section)
    expected_material_ids = (
        set(required_material_ids or ())
        if revision_instruction
        else {material.material_id for material in section.materials}
    )
    prompt = build_chapter_generation_prompt(
        section_context,
        editorial_guidance=editorial_guidance,
        chapter_sections=chapter_sections,
        current_section_content=current_section_content,
        current_section_user_edited=current_section_user_edited,
        current_section_user_edited_at=current_section_user_edited_at,
        revision_instruction=revision_instruction,
        source_update_reason=source_update_reason,
        previous_section_context=preceding_section_context,
        changed_material_ids=required_material_ids,
        previous_material_ids=previous_material_ids,
    )
    last_error: Exception | None = None
    policy = ai_text_workload_policy(
        AITextWorkload.LONG_GENERATION_STRUCTURED
    )
    for attempt in range(CHAPTER_SECTION_GENERATION_ATTEMPTS):
        try:
            attempt_prompt = prompt
            if last_error is not None:
                attempt_prompt += (
                    "\n上次输出未通过校验："
                    f"{last_error}。请补齐所有 JSON 字段并严格遵守输出格式。"
                )
            generated_result = await generate_structured_with_client(
                client,
                (
                    CHAPTER_SECTION_REVISION_SYSTEM_PROMPT
                    if revision_instruction
                    else CHAPTER_GENERATION_SYSTEM_PROMPT
                ),
                attempt_prompt,
                output_model=(
                    _RevisedChapterSectionOutput
                    if revision_instruction else _GeneratedChapterSectionOutput
                ),
                requested_model=ai_model,
                temperature=0.2 if revision_instruction else 0.3,
                max_tokens=policy.max_tokens,
                timeout=ai_text_timeout_seconds(policy.timeout_profile),
                max_attempts=1,
            )
            generated = normalize_generated_section(
                generated_result.value,
                section=section,
                model=generated_result.response.resolved_model,
            )
            if not expected_material_ids.issubset(
                generated.sections[0].used_material_ids
            ):
                raise ValueError("小节正文的采用来源不完整，必须同时记录保留正文和本次增改内容使用的全部讲述")
            _reject_repeated_section_boundary(
                previous_section_content,
                generated.sections[0].content,
            )
            return generated
        except AIProviderTimeoutError as exc:
            raise ValueError(
                f"小节“{section.title}”正文生成超时：{exc}"
            ) from exc
        except (ValueError, httpx.HTTPError) as exc:
            last_error = exc
            logger.warning(
                "memoir chapter section generation attempt failed "
                "chapter_id=%s section_id=%s attempt=%s/%s error=%s",
                context.chapter_id,
                section.section_id,
                attempt + 1,
                CHAPTER_SECTION_GENERATION_ATTEMPTS,
                exc,
            )
    raise ValueError(
        f"小节“{section.title}”正文生成失败：{last_error}"
    ) from last_error


async def _next_version(db: AsyncSession, *, draft_id: str) -> int:
    latest = await db.scalar(
        select(func.max(MemoirBookChapterDraftVersion.version)).where(
            MemoirBookChapterDraftVersion.draft_id == draft_id
        )
    )
    return int(latest or 0) + 1


async def latest_chapter_draft_version(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> MemoirBookChapterDraftVersion | None:
    return await db.scalar(
        select(MemoirBookChapterDraftVersion)
        .where(
            MemoirBookChapterDraftVersion.project_id == project_id,
            MemoirBookChapterDraftVersion.chapter_id == chapter_id,
        )
        .order_by(
            MemoirBookChapterDraftVersion.version.desc(),
            MemoirBookChapterDraftVersion.created_at.desc(),
            MemoirBookChapterDraftVersion.id.desc(),
        )
        .limit(1)
    )


def _snapshot(
    context: ChapterGenerationContext,
    *,
    used_material_ids: set[str],
) -> list[dict]:
    return [
        {
            "material_id": item.material_id,
            "section_id": item.section_id,
            "transcript_version_id": item.transcript_version_id,
            "text_hash": semantic_text_hash(item.text),
            **({"interviewer_line": item.interviewer_line} if item.interviewer_line else {}),
            "section_order": item.section_order,
            "material_order": item.material_order,
        }
        for item in context.materials
        if item.material_id in used_material_ids
    ]


def _section_input_signature(
    context: ChapterGenerationContext,
    section: ChapterSectionInput,
) -> str:
    # This identifies the source of one section, not the rendering policy for
    # an entire chapter. Engine/polish changes and chapter/section reordering
    # must not make unchanged narration look like newly supplied material.
    return semantic_json_signature(
        {
            "section": {
                "id": section.section_id,
                "title": section.title,
                "type": section.section_type,
            },
            "materials": [
                {
                    "material_id": material.material_id,
                    "transcript_version_id": material.transcript_version_id,
                    "text_hash": semantic_text_hash(material.text),
                    **({"interviewer_line": material.interviewer_line} if material.interviewer_line else {}),
                    "material_order": material.material_order,
                }
                for material in section.materials
            ],
            **({"fact_decisions": fact_decisions.signature(section.decisions)} if section.decisions else {}),
        }
    )


def rebased_chapter_draft_version(
    *,
    before: ChapterGenerationContext | None,
    after: ChapterGenerationContext | None,
    latest: MemoirBookChapterDraftVersion,
    patch_text: Callable[[str, str], str],
) -> MemoirBookChapterDraftVersion:
    """Copy the latest draft with deterministic text patches applied, without an AI rewrite.

    `patch_text(key, text)` receives "content" for the whole chapter and a section id
    for each section. `before`/`after` are this chapter's sources around the same edit
    (for example a corrected writing). A section that was current before stays current
    afterwards; a section already waiting for an update keeps its old signature, so the
    existing pipeline still rewrites it. Authorship, manual-section marks and the
    draft time are copied unchanged, so answers and materials added after the
    original draft are still treated as newer. Without contexts only the text changes.
    """
    before_sections = {section.section_id: section for section in before.sections} if before else {}
    after_sections = {section.section_id: section for section in after.sections} if after else {}
    sections: list[object] = []
    for snapshot in latest.section_snapshot_json or []:
        if not isinstance(snapshot, dict):
            sections.append(snapshot)
            continue
        item = dict(snapshot)
        section_id = str(item.get("section_id") or "")
        item["content"] = patch_text(section_id, str(item.get("content") or ""))
        prior = before_sections.get(section_id)
        current = after_sections.get(section_id)
        if (
            before is not None and after is not None
            and prior is not None and current is not None
            and _preserved_draft_section(before, prior, snapshot) is not None
            and fact_decisions.signature(prior.decisions) == fact_decisions.signature(current.decisions)
        ):
            item["title"] = current.title
            item["section_version"] = current.section_version
            item["input_signature"] = _section_input_signature(after, current)
        sections.append(item)
    before_materials = {material.material_id: material for material in before.materials} if before else {}
    after_materials = {material.material_id: material for material in after.materials} if after else {}
    material_snapshot: list[object] = []
    for entry in latest.material_snapshot_json or []:
        if not isinstance(entry, dict):
            material_snapshot.append(entry)
            continue
        item = dict(entry)
        prior = before_materials.get(str(item.get("material_id") or ""))
        current = after_materials.get(str(item.get("material_id") or ""))
        if (
            prior is not None and current is not None
            and item.get("transcript_version_id") == prior.transcript_version_id
            and item.get("text_hash") == semantic_text_hash(prior.text)
            and item.get("interviewer_line", "") == prior.interviewer_line == current.interviewer_line
        ):
            item["transcript_version_id"] = current.transcript_version_id
            item["text_hash"] = semantic_text_hash(current.text)
        material_snapshot.append(item)
    return MemoirBookChapterDraftVersion(
        draft_id=latest.draft_id,
        project_id=latest.project_id,
        chapter_id=latest.chapter_id,
        version=latest.version + 1,
        content=patch_text("content", latest.content),
        created_by=latest.created_by,
        model=latest.model,
        prompt_snapshot=latest.prompt_snapshot,
        feedback=latest.feedback,
        input_signature=(
            after.input_signature
            if before is not None and after is not None and latest.input_signature == before.input_signature
            else latest.input_signature
        ),
        used_material_ids_json=list(latest.used_material_ids_json or []),
        material_snapshot_json=material_snapshot,
        section_snapshot_json=sections,
        created_at=latest.created_at,
    )


def _section_snapshot(
    context: ChapterGenerationContext,
    *,
    sections: tuple[GeneratedChapterSection, ...],
) -> list[dict]:
    """One snapshot contract for automatic writing and user-approved edits."""
    inputs = {section.section_id: section for section in context.sections}
    return [
        {
            "section_id": section.section_id,
            "title": inputs[section.section_id].title,
            "section_type": inputs[section.section_id].section_type,
            "section_version": inputs[section.section_id].section_version,
            "sort_order": inputs[section.section_id].sort_order,
            "input_signature": _section_input_signature(context, inputs[section.section_id]),
            "content": section.content,
            **({"fact_decision_signature": fact_decisions.signature(inputs[section.section_id].decisions),
                "fact_decision_ids": [decision.issue_id for decision in inputs[section.section_id].decisions]}
               if inputs[section.section_id].decisions else {}),
            "used_material_ids": list(section.used_material_ids),
            **({"user_edited": True} if section.user_edited else {}),
            **({"user_edited_at": section.user_edited_at.isoformat()} if section.user_edited_at else {}),
        }
        for section in sections
    ]


async def generate_and_save_chapter_draft(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    editorial_guidance: list[str],
    ai_model: str | None = None,
    ai_client: AITextClient | None = None,
    checkpoint: dict[str, object] | None = None,
    progress_callback: ChapterGenerationProgressCallback | None = None,
    automatic: bool = False,
    force_refresh: bool = False,
) -> tuple[MemoirBookChapterDraft, MemoirBookChapterDraftVersion]:
    before = await load_chapter_generation_context(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    latest_before = await latest_chapter_draft_version(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    base_version_id = latest_before.id if latest_before else None
    if latest_before is not None:
        before = replace(before, previous_draft_at=latest_before.created_at)
        if latest_before.created_by == MemoirCreatedBy.USER.value or any(
            section.get("user_edited") for section in latest_before.section_snapshot_json
        ):
            before = await _with_previous_material_text(db, before, latest_before)
    style_refresh = automatic and not chapter_draft_render_policy_current(
        before, latest_before
    ) and latest_before is not None
    plan = chapter_draft_generation_plan(
        before,
        latest_before,
        force_refresh=force_refresh,
        refresh_unedited=style_refresh,
    )
    changed_material_ids = _changed_material_ids(before, latest_before)
    continuity = await load_section_continuity(db, context=before)
    # Generation uses immutable context snapshots. Release all read locks and
    # the connection before the slow provider phase; the write phase below
    # locks the chapter and revalidates the input signature.
    await db.commit()
    related_reasons: dict[str, str] = (
        {
            section.section_id: (
                f"润色等级已调整为 {before.polish_level.value}；"
                "请按新强度整理本节语言，保留事实、细节和个人表达。"
            )
            for section in plan.target_context.sections
        }
        if style_refresh else {}
    )
    if automatic and not force_refresh and not style_refresh and plan.target_context.sections:
        related_reasons = await select_related_sections(
            before,
            plan,
            changed_material_ids=changed_material_ids,
            ai_model=ai_model,
            ai_client=ai_client,
        )
        if related_reasons:
            plan = chapter_draft_generation_plan(
                before,
                latest_before,
                additional_target_section_ids=set(related_reasons),
            )
    generated = await generate_chapter(
        plan.target_context,
        editorial_guidance=editorial_guidance,
        ai_model=ai_model,
        ai_client=ai_client,
        checkpoint=checkpoint,
        progress_callback=progress_callback,
        chapter_sections=before.sections,
        preserved_sections=plan.preserved_sections,
        previous_sections=() if force_refresh else plan.previous_sections,
        preserved_model=plan.previous_model,
        section_continuity=continuity,
        related_section_reasons=related_reasons,
        changed_material_ids=changed_material_ids,
    )
    db.expire_all()
    await lock_chapter_draft_write(db, project_id=project_id, chapter_id=chapter_id)
    after = await load_chapter_generation_context(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    if after.input_signature != before.input_signature:
        if automatic:
            raise ChapterGenerationStale("生成期间章节素材已变化")
        raise HTTPException(status_code=409, detail="生成期间章节素材已变化，请重试")
    latest = await latest_chapter_draft_version(db, project_id=project_id, chapter_id=chapter_id)
    changed = (latest.id if latest else None) != base_version_id
    from app.services.memoir import completion_issues
    blocking_issue = await completion_issues.open_blocking_book_chapter_ids(
        db, project_id=project_id, all_chapter_ids=[chapter_id],
    )
    if blocking_issue and not automatic:
        raise HTTPException(status_code=409, detail="本章还有事实冲突需要先确认")
    if changed:
        if automatic:
            raise ChapterGenerationStale("生成期间章节正文已变化")
        raise HTTPException(status_code=409, detail="生成期间正文已被修改，原稿已保留，请刷新")
    if automatic and (
        plan.protected_target_section_ids
        or plan.has_unmapped_manual_content
        or blocking_issue
    ):
        candidate = {
            **_generated_chapter_checkpoint(before, generated, current_section=None),
            "candidate_content": generated.content,
            "candidate_base_version_id": latest.id if latest else None,
        }
        raise ChapterDraftCandidateReady(candidate)
    return await _save_generated_draft(db, context=before, generated=generated)


class ChapterGenerationStale(Exception):
    """The pending output is obsolete; rerun the existing preparation job."""


class ChapterDraftCandidateReady(Exception):
    def __init__(self, candidate: dict[str, object]):
        self.candidate = candidate
        super().__init__("新稿已生成，请确认是否替换人工修订")


async def lock_chapter_draft_write(db: AsyncSession, *, project_id: str, chapter_id: str) -> None:
    await db.execute(select(MemoirBookPlanChapter.id).where(
        MemoirBookPlanChapter.project_id == project_id, MemoirBookPlanChapter.id == chapter_id,
    ).with_for_update())
    message_ids = (await db.scalars(select(MemoirVoiceUtterance.chat_message_id)
        .join(MemoirSectionMaterial, MemoirSectionMaterial.utterance_id == MemoirVoiceUtterance.id)
        .join(MemoirSection, MemoirSection.id == MemoirSectionMaterial.section_id)
        .where(MemoirSection.chapter_id == chapter_id, MemoirSection.project_id == project_id)
        .order_by(MemoirVoiceUtterance.id).with_for_update(of=MemoirVoiceUtterance))).all()
    await interviewer_lines(db, message_ids, lock=True)


async def _save_generated_draft(
    db: AsyncSession, *, context: ChapterGenerationContext, generated: GeneratedChapter,
) -> tuple[MemoirBookChapterDraft, MemoirBookChapterDraftVersion]:
    before = context
    project_id, chapter_id = context.project_id, context.chapter_id
    draft = await db.scalar(
        select(MemoirBookChapterDraft).where(
            MemoirBookChapterDraft.project_id == project_id,
            MemoirBookChapterDraft.chapter_id == chapter_id,
        )
    )
    if draft is None:
        draft = MemoirBookChapterDraft(
            project_id=project_id,
            chapter_id=chapter_id,
            status=MemoirBookChapterDraftStatus.DRAFT.value,
            metadata_json={"source": CHAPTER_GENERATION_ENGINE},
        )
        db.add(draft)
        await db.flush()
    latest = await latest_chapter_draft_version(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    section_snapshot = _section_snapshot(before, sections=generated.sections)
    if (
        latest is not None
        and latest.input_signature == before.input_signature
        and latest.prompt_snapshot == _render_policy_tag(before)
        and latest.content.strip() == generated.content.strip()
        and list(latest.section_snapshot_json) == section_snapshot
    ):
        return draft, latest
    version = MemoirBookChapterDraftVersion(
        draft_id=draft.id,
        project_id=project_id,
        chapter_id=chapter_id,
        version=await _next_version(db, draft_id=draft.id),
        content=generated.content,
        created_by=MemoirCreatedBy.AI.value,
        model=generated.model,
        prompt_snapshot=_render_policy_tag(context),
        feedback=None,
        input_signature=before.input_signature,
        used_material_ids_json=list(generated.used_material_ids),
        material_snapshot_json=_snapshot(
            before,
            used_material_ids=set(generated.used_material_ids),
        ),
        section_snapshot_json=section_snapshot,
    )
    db.add(version)
    draft.status = MemoirBookChapterDraftStatus.DRAFT.value
    draft.updated_at = datetime.now(UTC).replace(tzinfo=None)
    draft.metadata_json = {
        "source": CHAPTER_GENERATION_ENGINE,
        "missing_source_candidates": list(
            generated.missing_source_candidates
        ),
        "editor_notes": list(generated.editor_notes),
    }
    db.add(draft)
    output = await db.scalar(
        select(MemoirBookOutput).where(
            MemoirBookOutput.project_id == project_id
        )
    )
    if output is not None:
        invalidate_book_output(output)
        db.add(output)
    await db.flush()
    previous_char_count = len(latest.content.strip()) if latest else 0
    await automation_events.record_automation_event(
        db,
        project_id=project_id,
        event_type=MemoirAutomationEventType.CHAPTER_DRAFT_GENERATED,
        chapter_id=chapter_id,
        summary_text=f"《{context.chapter_title}》正文已更新，可以阅读了。",
        metadata_json={
            "draft_version_id": version.id,
            "book_char_count_delta": len(generated.content.strip()) - previous_char_count,
        },
    )
    return draft, version


async def save_chapter_draft_candidate(
    db: AsyncSession, *, project_id: str, chapter_id: str, checkpoint: dict[str, object],
) -> tuple[MemoirBookChapterDraft, MemoirBookChapterDraftVersion]:
    await lock_chapter_draft_write(db, project_id=project_id, chapter_id=chapter_id)
    context = await load_chapter_generation_context(db, project_id=project_id, chapter_id=chapter_id)
    latest = await latest_chapter_draft_version(db, project_id=project_id, chapter_id=chapter_id)
    if (latest.id if latest else None) != checkpoint.get("candidate_base_version_id"):
        raise HTTPException(status_code=409, detail="候选稿生成后正文已修改，请重新整理")
    generated = _generated_chapter_from_checkpoint(context, checkpoint)
    if generated is None or len(generated.sections) != len(context.sections):
        raise HTTPException(status_code=409, detail="候选稿对应的素材已变化，请重新整理")
    draft, version = await _save_generated_draft(db, context=context, generated=generated)
    version.feedback = "用户采纳后台候选稿"
    return draft, version


async def apply_chapter_section_candidate(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    section_id: str,
    expected_version_id: str,
    expected_input_signature: str,
    candidate_title: str,
    candidate_content: str,
    feedback: str,
    requested_ai_model: str | None,
    resolved_ai_model: str | None,
) -> tuple[MemoirBookChapterDraft, MemoirBookChapterDraftVersion]:
    await lock_chapter_draft_write(db, project_id=project_id, chapter_id=chapter_id)
    context = await load_chapter_generation_context(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    target_index = next(
        (
            index
            for index, section in enumerate(context.sections)
            if section.section_id == section_id
        ),
        -1,
    )
    if target_index < 0:
        raise HTTPException(status_code=404, detail="小节不存在")
    draft = await db.scalar(
        select(MemoirBookChapterDraft).where(
            MemoirBookChapterDraft.project_id == project_id,
            MemoirBookChapterDraft.chapter_id == chapter_id,
        )
    )
    latest = await latest_chapter_draft_version(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    if draft is None or latest is None:
        raise HTTPException(status_code=404, detail="章节稿不存在")
    if latest.id != expected_version_id:
        raise HTTPException(status_code=409, detail="正文已更新，请刷新后再修改")
    if (
        context.input_signature != expected_input_signature
        or not chapter_draft_is_current(context, latest)
    ):
        raise HTTPException(status_code=409, detail="本章素材已变化，请先更新相关小节正文")
    latest_sections = [
        dict(section)
        for section in latest.section_snapshot_json
        if isinstance(section, dict)
    ]
    expected_section_ids = [section.section_id for section in context.sections]
    if (
        len(latest_sections) != len(context.sections)
        or [
            str(section.get("section_id") or "")
            for section in latest_sections
        ] != expected_section_ids
    ):
        raise HTTPException(status_code=409, detail="本章小节已经变化，请刷新后再修改")
    title = candidate_title.strip()
    if not title:
        raise ValueError("修改稿标题不能为空")
    content = candidate_content.strip()
    if not content:
        raise ValueError("修改稿正文不能为空")
    target_input = context.sections[target_index]
    if title != target_input.title:
        section = await db.scalar(
            select(MemoirSection)
            .where(
                MemoirSection.project_id == project_id,
                MemoirSection.chapter_id == chapter_id,
                MemoirSection.id == section_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if section is None:
            raise HTTPException(status_code=404, detail="小节不存在")
        if section.version != target_input.section_version:
            raise HTTPException(status_code=409, detail="小节已经变化，请刷新后再修改")
        section.title = title
        section.version += 1
        section.updated_at = datetime.now(UTC).replace(tzinfo=None)
        db.add(section)
        await mark_section_digests_stale(
            db,
            project_id=project_id,
            section_ids={section_id},
        )
        await db.flush()
        context = await load_chapter_generation_context(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
        )
        target_index = next(
            index
            for index, item in enumerate(context.sections)
            if item.section_id == section_id
        )
        target_input = context.sections[target_index]
    replacement = _section_snapshot(context, sections=(GeneratedChapterSection(
        section_id=target_input.section_id,
        title=target_input.title,
        content=content,
        used_material_ids=tuple(latest_sections[target_index].get("used_material_ids", [])),
        user_edited=True,
        user_edited_at=(
            datetime.now(UTC).replace(tzinfo=None)
            if content != latest_sections[target_index].get("content")
            else datetime.fromisoformat(latest_sections[target_index]["user_edited_at"])
            if latest_sections[target_index].get("user_edited_at") else None
        ),
    ),))[0]
    revised_sections = [
        replacement if index == target_index else section
        for index, section in enumerate(latest_sections)
    ]
    revised_content = "\n\n".join(
        str(section.get("content") or "").strip()
        for section in revised_sections
    )
    if revised_sections == latest_sections:
        return draft, latest
    used_material_ids = [
        material_id
        for section in revised_sections
        for material_id in (
            section.get("used_material_ids", [])
            if isinstance(section.get("used_material_ids"), list)
            else []
        )
        if isinstance(material_id, str)
    ]
    version = MemoirBookChapterDraftVersion(
        draft_id=draft.id,
        project_id=project_id,
        chapter_id=chapter_id,
        version=await _next_version(db, draft_id=draft.id),
        content=revised_content,
        created_by=MemoirCreatedBy.AI.value,
        model=resolved_ai_model,
        prompt_snapshot=f"{_render_policy_tag(context)}:section_revision_workbench",
        feedback=feedback.strip() or f"AI 修改小节《{target_input.title}》",
        input_signature=context.input_signature,
        used_material_ids_json=used_material_ids,
        material_snapshot_json=_snapshot(
            context,
            used_material_ids=set(used_material_ids),
        ),
        section_snapshot_json=revised_sections,
    )
    db.add(version)
    draft.status = MemoirBookChapterDraftStatus.DRAFT.value
    draft.updated_at = datetime.now(UTC).replace(tzinfo=None)
    draft.metadata_json = {
        **(draft.metadata_json or {}),
        "source": CHAPTER_GENERATION_ENGINE,
        "last_ai_revised_section_id": section_id,
        "last_requested_ai_model": requested_ai_model,
        "last_resolved_ai_model": resolved_ai_model,
    }
    db.add(draft)
    output = await db.scalar(
        select(MemoirBookOutput).where(
            MemoirBookOutput.project_id == project_id
        )
    )
    if output is not None:
        invalidate_book_output(output)
        db.add(output)
    await db.flush()
    return draft, version


async def confirm_chapter_draft(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> tuple[MemoirBookChapterDraft, MemoirBookChapterDraftVersion]:
    await lock_chapter_draft_write(db, project_id=project_id, chapter_id=chapter_id)
    context = await load_chapter_generation_context(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    draft = await db.scalar(
        select(MemoirBookChapterDraft).where(
            MemoirBookChapterDraft.project_id == project_id,
            MemoirBookChapterDraft.chapter_id == chapter_id,
        )
    )
    latest = await latest_chapter_draft_version(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    if draft is None or latest is None:
        raise HTTPException(status_code=409, detail="章节正文尚未生成")
    if not latest.section_snapshot_json:
        raise HTTPException(
            status_code=409,
            detail="当前正文还没有小节结构，请重新生成正文",
        )
    if not chapter_draft_is_current(context, latest):
        raise HTTPException(status_code=409, detail="章节素材已变化，请重新生成正文")
    from app.services.memoir import completion_issues
    if await completion_issues.open_blocking_book_chapter_ids(
        db, project_id=project_id, all_chapter_ids=[chapter_id],
    ):
        raise HTTPException(status_code=409, detail="本章还有一项需要确认")
    draft.status = MemoirBookChapterDraftStatus.CONFIRMED.value
    draft.metadata_json = {**(draft.metadata_json or {}), "confirmed_version_id": latest.id}
    draft.updated_at = datetime.now(UTC).replace(tzinfo=None)
    db.add(draft)
    await db.flush()
    return draft, latest
