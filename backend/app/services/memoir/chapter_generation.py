from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
import logging
from typing import Any

from fastapi import HTTPException
import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirBookPlanStatus,
    MemoirCompletionIssueChannel,
    MemoirCompletionIssueStatus,
    MemoirCreatedBy,
    MemoirSectionStatus,
)
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirBookOutput,
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirCompletionIssue,
    MemoirSection,
    MemoirSectionMaterial,
)
from app.schemas.memoir_chapter_pipeline import (
    MemoirLayeredChapterDraftResponse,
    MemoirLayeredChapterDraftVersionResponse,
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
from app.services.memoir.section_digests import (
    get_current_section_digest,
    mark_section_digests_stale,
)
from app.services.memoir.semantic_state import (
    semantic_json_signature,
    semantic_text_hash,
)
from app.services.memoir.transcript_projection import current_material_text


CHAPTER_GENERATION_ENGINE = "llm_layered_sectioned_chapter_draft_v2"
CHAPTER_GENERATION_SYSTEM_PROMPT = (
    "你是一位中文回忆录编辑。"
    "只使用允许的原始讲述整理成第一人称完整章节，不创造事实。"
)
CHAPTER_SECTION_REVISION_SYSTEM_PROMPT = (
    "你是一位谨慎的中文回忆录编辑。"
    "只使用允许的原始讲述和用户明确给出的事实更正修改当前小节，"
    "不自行创造事实。"
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


@dataclass(frozen=True)
class ChapterSectionInput:
    section_id: str
    title: str
    section_type: str
    section_version: int
    sort_order: int
    materials: tuple[ChapterMaterialInput, ...]


@dataclass(frozen=True)
class ChapterGenerationContext:
    project_id: str
    chapter_id: str
    chapter_title: str
    sections: tuple[ChapterSectionInput, ...]
    materials: tuple[ChapterMaterialInput, ...]
    input_signature: str


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
    previous_model: str | None


class _GeneratedChapterSectionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_id: str
    title: str | None = Field(default=None, max_length=200)
    content: str
    used_material_ids: list[str] = Field(default_factory=list)


class _GeneratedChapterOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sections: list[_GeneratedChapterSectionOutput] = Field(min_length=1)
    missing_source_candidates: list[str] = Field(default_factory=list)
    editor_notes: list[str] = Field(default_factory=list)


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
            }
            for section in generated.sections
        ],
        "checkpoint_missing_source_candidates": list(
            generated.missing_source_candidates
        ),
        "checkpoint_editor_notes": list(generated.editor_notes),
        "checkpoint_model": generated.model,
        "resolved_ai_model": generated.model,
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
            or set(used) - allowed
            or used_material_ids.intersection(used)
        ):
            return None
        generated_sections.append(
            GeneratedChapterSection(
                section_id=expected.section_id,
                title=expected.title,
                content=content,
                used_material_ids=used,
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


def _preserved_draft_section(
    context: ChapterGenerationContext,
    section: ChapterSectionInput,
    snapshot: object,
    *,
    legacy_material_snapshot: list[dict[str, object]],
) -> GeneratedChapterSection | None:
    if not isinstance(snapshot, dict):
        return None
    if (
        snapshot.get("section_id") != section.section_id
        or snapshot.get("title") != section.title
        or snapshot.get("section_type") != section.section_type
        or snapshot.get("section_version") != section.section_version
        or snapshot.get("sort_order") != section.sort_order
    ):
        return None
    content = str(snapshot.get("content") or "").strip()
    raw_used = snapshot.get("used_material_ids")
    if not content or not isinstance(raw_used, list):
        return None
    used_material_ids = tuple(
        value
        for value in raw_used
        if isinstance(value, str) and value
    )
    current_material_ids = tuple(
        material.material_id for material in section.materials
    )
    allowed_material_ids = {
        *current_material_ids,
    }
    if (
        not used_material_ids
        or len(used_material_ids) != len(raw_used)
        or set(used_material_ids) - allowed_material_ids
    ):
        return None
    stored_input_signature = str(
        snapshot.get("input_signature") or ""
    )
    if stored_input_signature:
        if stored_input_signature != _section_input_signature(
            context,
            section,
        ):
            return None
    elif (
        len(used_material_ids) != len(current_material_ids)
        or set(used_material_ids) != set(current_material_ids)
        or legacy_material_snapshot != _section_source_snapshot(section)
    ):
        # Legacy snapshots did not store a per-section source signature.
        # Reuse them only when they prove complete coverage of the current
        # material set; otherwise regenerate conservatively.
        return None
    return GeneratedChapterSection(
        section_id=section.section_id,
        title=section.title,
        content=content,
        used_material_ids=used_material_ids,
    )


def chapter_draft_generation_plan(
    context: ChapterGenerationContext,
    latest: MemoirBookChapterDraftVersion | None,
) -> ChapterDraftGenerationPlan:
    snapshots = (
        list(latest.section_snapshot_json)
        if latest is not None and isinstance(latest.section_snapshot_json, list)
        else []
    )
    snapshots_by_id: dict[str, object] = {}
    material_snapshots_by_section: dict[str, list[dict[str, object]]] = {}
    if latest is not None:
        for item in getattr(latest, "material_snapshot_json", []):
            if not isinstance(item, dict):
                continue
            section_id = str(item.get("section_id") or "")
            if section_id:
                material_snapshots_by_section.setdefault(
                    section_id,
                    [],
                ).append(dict(item))
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
    for section in context.sections:
        preserved = (
            None
            if section.section_id in duplicate_ids
            else _preserved_draft_section(
                context,
                section,
                snapshots_by_id.get(section.section_id),
                legacy_material_snapshot=material_snapshots_by_section.get(
                    section.section_id,
                    [],
                ),
            )
        )
        if preserved is None:
            target_sections.append(section)
        else:
            preserved_sections.append(preserved)

    target_materials = tuple(
        material
        for section in target_sections
        for material in section.materials
    )
    return ChapterDraftGenerationPlan(
        target_context=ChapterGenerationContext(
            project_id=context.project_id,
            chapter_id=context.chapter_id,
            chapter_title=context.chapter_title,
            sections=tuple(target_sections),
            materials=target_materials,
            input_signature=context.input_signature,
        ),
        preserved_sections=tuple(preserved_sections),
        previous_model=latest.model if latest is not None else None,
    )


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


async def load_chapter_draft_generation_plan(
    db: AsyncSession,
    *,
    context: ChapterGenerationContext,
) -> ChapterDraftGenerationPlan:
    latest = await _latest_version(
        db,
        project_id=context.project_id,
        chapter_id=context.chapter_id,
    )
    return chapter_draft_generation_plan(context, latest)


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
            text = " ".join(current_material_text(material).split())
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
                )
            )
    if not materials:
        raise HTTPException(status_code=422, detail="章节没有可用于生成正文的素材")
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
                }
                for item in materials
            ],
            "engine": CHAPTER_GENERATION_ENGINE,
        }
    )
    return ChapterGenerationContext(
        project_id=project_id,
        chapter_id=chapter.id,
        chapter_title=chapter.title,
        sections=tuple(sections),
        materials=tuple(materials),
        input_signature=signature,
    )


def build_chapter_generation_prompt(
    context: ChapterGenerationContext,
    *,
    editorial_guidance: list[str],
    chapter_sections: tuple[ChapterSectionInput, ...] | None = None,
    current_section_content: str = "",
    revision_instruction: str = "",
    previous_section_context: str = "",
) -> str:
    section_refs = ModelReferenceMap(
        "S",
        (section.section_id for section in context.sections),
    )
    material_refs = ModelReferenceMap(
        "M",
        (material.material_id for material in context.materials),
    )
    outline_sections = chapter_sections or context.sections
    target_section_id = (
        context.sections[0].section_id
        if len(context.sections) == 1
        else ""
    )
    outline = "\n".join(
        (
            f"{index + 1}. {section.title}"
            f"{'（本次生成）' if section.section_id == target_section_id else ''}"
        )
        for index, section in enumerate(outline_sections)
    )
    blocks = "\n\n".join(
        "\n".join(
            [
                f"section_id: {section_refs.encode(section.section_id)}",
                f"小节标题: {section.title}",
                f"小节类型: {section.section_type}",
                "允许素材：",
                *[
                    (
                        f"material_id: {material_refs.encode(material.material_id)}\n"
                        f"原始讲述: {material.text}"
                    )
                    for material in section.materials
                ],
            ]
        )
        for section in context.sections
    )
    guidance = "\n".join(
        f"- {' '.join(item.split())[:240]}"
        for item in editorial_guidance[:8]
        if item.strip()
    ) or "（无额外编辑建议）"
    generation_scope = (
        "- 本次只生成标记为“本次生成”的一个小节，sections 数组只能包含这一项；"
        "不得复述其他小节的内容。\n"
        if target_section_id
        else "- 一次输出完整章节，并严格按输入顺序返回每个小节。\n"
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
- 先理解用户要调整的对象、范围和表达效果，再输出修改后的标题和完整正文。
- 如果修改要求涉及标题中的错字或表达，必须同步修改标题；否则标题保持不变。
- 同一个错字或名称同时出现在标题和正文时，两处必须同步修正。
- 指令未涉及的事实和段落尽量保持不变。
- 可以按要求调整措辞、语气、详略、顺序和段落结构。
- 用户在指令中明确给出的事实更正可以采用；除此之外仍只能使用下方原始讲述中的事实。
- 用户只要求“补充更多细节”却没有说出具体事实、且原始讲述也不支持时，不得自行虚构，应放入 missing_source_candidates。
- 不要在正文里复述用户指令，也不要解释修改过程。
"""
    return f"""请把下面的原始讲述整理为回忆录章节“{context.chapter_title}”。

本章完整小节顺序：
{outline}
{revision}
编辑建议：
{guidance}

前一小节衔接参考：
{previous_section_context or "（这是本章第一个小节）"}

按既定顺序排列的允许素材：
{blocks}

输出严格 JSON：
{{
  "sections": [
    {{
      "section_id": "输入中的 section_id",
      "title": "小节完整标题，修改要求未涉及标题时保持输入标题",
      "content": "这一小节完整、连贯、第一人称的正文",
      "used_material_ids": ["这一小节正文实际使用的 material_id"]
    }}
  ],
  "missing_source_candidates": ["想表达但现有素材没有支持的内容，只列出，不写入正文"],
  "editor_notes": ["可选的简短编辑说明"]
}}

要求：
{generation_scope}- 严格按输入顺序返回小节。
- section_id 和 material_id 都是本次请求内的短编号，必须原样返回。
- 每个输入 section_id 必须出现且只能出现一次，不新增、删除或合并小节。
- 只有本次按用户要求修改且要求涉及标题时，才可修改输入标题。
- 每个小节只能使用列在该小节下的素材，不能把素材移动到其他小节。
- 前一小节内容只用于自然衔接和避免重复，不是本小节可新增事实的来源。
- 结合完整小节顺序安排本节开头和结尾；只在本节素材支持时自然承接，不强行添加过渡，也不要复述前一小节。
- 只能使用输入中的事实和感受，可以调整顺序、合并重复。
- 不要为了完整而虚构时间、地点、人物、因果或心理活动。
- 不在正文中出现 section_id、material_id、编辑指令、“叙述者”等后台术语。
- 同一次讲述保持连续，不把一条 Material 拆散后穿插到多处。
"""


def _single_section_context(
    context: ChapterGenerationContext,
    section: ChapterSectionInput,
) -> ChapterGenerationContext:
    return ChapterGenerationContext(
        project_id=context.project_id,
        chapter_id=context.chapter_id,
        chapter_title=context.chapter_title,
        sections=(section,),
        materials=section.materials,
        input_signature=context.input_signature,
    )


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


def normalize_generated_chapter(
    payload: Any,
    *,
    context: ChapterGenerationContext,
    model: str | None,
    allow_title_revision: bool = False,
) -> GeneratedChapter:
    if not isinstance(payload, dict):
        raise ValueError("chapter generation AI did not return a JSON object")
    raw_sections = payload.get("sections")
    if not isinstance(raw_sections, list):
        raise ValueError("chapter generation returned no sections")
    allowed_sections = {
        section.section_id: section for section in context.sections
    }
    section_refs = ModelReferenceMap("S", allowed_sections)
    material_refs = ModelReferenceMap(
        "M",
        (material.material_id for material in context.materials),
    )
    seen_sections: set[str] = set()
    seen_materials: set[str] = set()
    generated_sections: list[GeneratedChapterSection] = []
    for raw_section in raw_sections:
        if not isinstance(raw_section, dict):
            raise ValueError("chapter generation returned invalid section")
        section_id = section_refs.decode(raw_section.get("section_id"))
        section = allowed_sections.get(section_id)
        if section is None:
            raise ValueError("chapter generation used section outside allowlist")
        if section_id in seen_sections:
            raise ValueError("chapter generation returned duplicate section")
        title_value = raw_section.get("title")
        if allow_title_revision and not isinstance(title_value, str):
            raise ValueError("chapter section title is missing")
        title = (
            title_value.strip()
            if allow_title_revision and isinstance(title_value, str)
            else section.title
        )
        if allow_title_revision and not title:
            raise ValueError("chapter section title is empty")
        if len(title) > 200:
            raise ValueError("chapter section title is too long")
        content_value = raw_section.get("content")
        content = (
            content_value.strip() if isinstance(content_value, str) else ""
        )
        if not content:
            raise ValueError("chapter section content is empty")
        allowed_material_ids = {
            material.material_id for material in section.materials
        }
        used = material_refs.decode_many(
            raw_section.get("used_material_ids"),
            limit=len(allowed_material_ids),
        )
        if not used:
            raise ValueError("chapter section returned no used material")
        if set(used) - allowed_material_ids:
            raise ValueError(
                "chapter generation used material outside allowlist for section"
            )
        if seen_materials.intersection(used):
            raise ValueError(
                "chapter generation reused material across sections"
            )
        internal_ids = set(allowed_sections) | {
            material.material_id for material in context.materials
        }
        if any(
            internal_id in value
            for internal_id in internal_ids
            for value in (title, content)
        ):
            raise ValueError("chapter section exposed internal id")
        seen_sections.add(section_id)
        seen_materials.update(used)
        generated_sections.append(
            GeneratedChapterSection(
                section_id=section_id,
                title=title,
                content=content,
                used_material_ids=tuple(used),
            )
        )
    expected_section_ids = [
        section.section_id for section in context.sections
    ]
    if [section.section_id for section in generated_sections] != (
        expected_section_ids
    ):
        raise ValueError(
            "chapter generation did not preserve complete section order"
        )
    content = "\n\n".join(
        section.content for section in generated_sections
    )
    return GeneratedChapter(
        sections=tuple(generated_sections),
        content=content,
        used_material_ids=tuple(
            material_id
            for section in generated_sections
            for material_id in section.used_material_ids
        ),
        missing_source_candidates=tuple(
            _string_list(
                payload.get("missing_source_candidates"),
                limit=8,
                item_limit=240,
            )
        ),
        editor_notes=tuple(
            _string_list(
                payload.get("editor_notes"),
                limit=8,
                item_limit=240,
            )
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
    preserved_model: str | None = None,
    section_continuity: dict[str, ChapterSectionContinuity] | None = None,
) -> GeneratedChapter:
    client = (
        ai_client or get_ai_service(ai_model)
    ) if context.sections else None
    full_sections = chapter_sections or context.sections
    target_section_ids = {section.section_id for section in context.sections}
    preserved_by_id = {
        section.section_id: section for section in preserved_sections
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
            generated_section = await generate_chapter_section(
                context,
                section=section,
                editorial_guidance=editorial_guidance,
                chapter_sections=full_sections,
                previous_section_content=(
                    completed_sections[-1].content
                    if completed_sections
                    else ""
                ),
                previous_section=(
                    full_sections[section_index - 1]
                    if section_index > 0
                    else None
                ),
                previous_section_continuity=(
                    (section_continuity or {}).get(
                        full_sections[section_index - 1].section_id
                    )
                    if section_index > 0
                    else None
                ),
                ai_model=ai_model,
                ai_client=client,
            )
            current = generated_section.sections[0]
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
    previous_section: ChapterSectionInput | None = None,
    previous_section_continuity: ChapterSectionContinuity | None = None,
    current_section_content: str = "",
    revision_instruction: str = "",
    ai_model: str | None = None,
    ai_client: AITextClient | None = None,
) -> GeneratedChapter:
    client = ai_client or get_ai_service(ai_model)
    section_context = _single_section_context(context, section)
    prompt = build_chapter_generation_prompt(
        section_context,
        editorial_guidance=editorial_guidance,
        chapter_sections=chapter_sections,
        current_section_content=current_section_content,
        revision_instruction=revision_instruction,
        previous_section_context=previous_section_context(
            section=previous_section,
            content=previous_section_content,
            continuity=previous_section_continuity,
        ),
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
                output_model=_GeneratedChapterOutput,
                requested_model=ai_model,
                temperature=0.2 if revision_instruction else 0.3,
                max_tokens=policy.max_tokens,
                timeout=ai_text_timeout_seconds(policy.timeout_profile),
                max_attempts=1,
            )
            generated = normalize_generated_chapter(
                generated_result.value.model_dump(),
                context=section_context,
                model=generated_result.response.resolved_model,
                allow_title_revision=bool(revision_instruction),
            )
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


async def _latest_version(
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
            "section_order": item.section_order,
            "material_order": item.material_order,
        }
        for item in context.materials
        if item.material_id in used_material_ids
    ]


def _section_source_snapshot(
    section: ChapterSectionInput,
) -> list[dict[str, object]]:
    return [
        {
            "material_id": material.material_id,
            "section_id": material.section_id,
            "transcript_version_id": material.transcript_version_id,
            "text_hash": semantic_text_hash(material.text),
            "section_order": material.section_order,
            "material_order": material.material_order,
        }
        for material in section.materials
    ]


def _section_input_signature(
    context: ChapterGenerationContext,
    section: ChapterSectionInput,
) -> str:
    return semantic_json_signature(
        {
            "engine": CHAPTER_GENERATION_ENGINE,
            "chapter": {
                "id": context.chapter_id,
                "title": context.chapter_title,
            },
            "section": {
                "id": section.section_id,
                "title": section.title,
                "type": section.section_type,
                "version": section.section_version,
                "sort_order": section.sort_order,
            },
            "materials": [
                {
                    "material_id": material.material_id,
                    "transcript_version_id": material.transcript_version_id,
                    "text_hash": semantic_text_hash(material.text),
                    "material_order": material.material_order,
                }
                for material in section.materials
            ],
        }
    )


def _section_snapshot(
    context: ChapterGenerationContext,
    *,
    generated: GeneratedChapter,
) -> list[dict]:
    inputs = {
        section.section_id: section for section in context.sections
    }
    return [
        {
            "section_id": section.section_id,
            "title": inputs[section.section_id].title,
            "section_type": inputs[section.section_id].section_type,
            "section_version": inputs[section.section_id].section_version,
            "sort_order": inputs[section.section_id].sort_order,
            "input_signature": _section_input_signature(
                context,
                inputs[section.section_id]
            ),
            "content": section.content,
            "used_material_ids": list(section.used_material_ids),
        }
        for section in generated.sections
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
) -> tuple[MemoirBookChapterDraft, MemoirBookChapterDraftVersion]:
    before = await load_chapter_generation_context(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    latest_before = await _latest_version(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    plan = chapter_draft_generation_plan(before, latest_before)
    continuity = await load_section_continuity(db, context=before)
    generated = await generate_chapter(
        plan.target_context,
        editorial_guidance=editorial_guidance,
        ai_model=ai_model,
        ai_client=ai_client,
        checkpoint=checkpoint,
        progress_callback=progress_callback,
        chapter_sections=before.sections,
        preserved_sections=plan.preserved_sections,
        preserved_model=plan.previous_model,
        section_continuity=continuity,
    )
    db.expire_all()
    after = await load_chapter_generation_context(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    if after.input_signature != before.input_signature:
        raise HTTPException(status_code=409, detail="生成期间章节素材已变化，请重试")
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
    latest = await _latest_version(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    section_snapshot = _section_snapshot(before, generated=generated)
    if (
        latest is not None
        and latest.input_signature == before.input_signature
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
        prompt_snapshot=CHAPTER_GENERATION_ENGINE,
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
    return draft, version


async def revise_and_save_chapter_draft_section_with_ai(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    section_id: str,
    expected_version_id: str,
    instruction_text: str,
    editorial_guidance: list[str],
    ai_model: str | None = None,
    ai_client: AITextClient | None = None,
) -> tuple[MemoirBookChapterDraft, MemoirBookChapterDraftVersion]:
    before = await load_chapter_generation_context(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    target_index = next(
        (
            index
            for index, section in enumerate(before.sections)
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
    latest = await _latest_version(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    if draft is None or latest is None:
        raise HTTPException(status_code=404, detail="章节稿不存在")
    if latest.id != expected_version_id:
        raise HTTPException(status_code=409, detail="正文已更新，请刷新后再修改")
    if not chapter_draft_is_current(before, latest):
        raise HTTPException(status_code=409, detail="本章素材已变化，请先更新相关小节正文")
    latest_sections = [
        dict(section)
        for section in latest.section_snapshot_json
        if isinstance(section, dict)
    ]
    expected_section_ids = [section.section_id for section in before.sections]
    if (
        len(latest_sections) != len(before.sections)
        or [
            str(section.get("section_id") or "")
            for section in latest_sections
        ] != expected_section_ids
    ):
        raise HTTPException(status_code=409, detail="本章小节已经变化，请刷新后再修改")
    previous_section_content = (
        str(latest_sections[target_index - 1].get("content") or "").strip()
        if target_index > 0
        else ""
    )
    target = before.sections[target_index]
    revision_instruction = instruction_text.strip()
    continuity = await load_section_continuity(db, context=before)
    await db.commit()
    generated = await generate_chapter_section(
        before,
        section=target,
        editorial_guidance=editorial_guidance,
        chapter_sections=before.sections,
        previous_section_content=previous_section_content,
        previous_section=(
            before.sections[target_index - 1]
            if target_index > 0
            else None
        ),
        previous_section_continuity=(
            continuity.get(before.sections[target_index - 1].section_id)
            if target_index > 0
            else None
        ),
        current_section_content=str(
            latest_sections[target_index].get("content") or ""
        ).strip(),
        revision_instruction=revision_instruction,
        ai_model=ai_model,
        ai_client=ai_client,
    )
    db.expire_all()
    after = await load_chapter_generation_context(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    if after.input_signature != before.input_signature:
        raise HTTPException(status_code=409, detail="生成期间章节素材已变化，请重试")
    current_latest = await _latest_version(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    if current_latest is None or current_latest.id != expected_version_id:
        raise HTTPException(status_code=409, detail="生成期间正文已更新，请刷新后重试")
    draft = await db.scalar(
        select(MemoirBookChapterDraft).where(
            MemoirBookChapterDraft.id == current_latest.draft_id
        )
    )
    if draft is None:
        raise HTTPException(status_code=404, detail="章节稿不存在")
    current = generated.sections[0]
    target_input = after.sections[target_index]
    if current.title != target_input.title:
        target_section = await db.get(
            MemoirSection,
            section_id,
            populate_existing=True,
        )
        if (
            target_section is None
            or target_section.version != target_input.section_version
        ):
            raise HTTPException(
                status_code=409,
                detail="生成期间小节标题已更新，请刷新后重试",
            )
        target_section.title = current.title
        target_section.version += 1
        target_section.updated_at = datetime.now(UTC).replace(tzinfo=None)
        db.add(target_section)
        await mark_section_digests_stale(
            db,
            project_id=project_id,
            section_ids={section_id},
        )
        await db.flush()
        after = await load_chapter_generation_context(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
        )
        target_input = after.sections[target_index]
    replacement = {
        "section_id": target_input.section_id,
        "title": current.title,
        "section_type": target_input.section_type,
        "section_version": target_input.section_version,
        "sort_order": target_input.sort_order,
        "content": current.content,
        "used_material_ids": list(current.used_material_ids),
    }
    revised_sections = [
        replacement if index == target_index else section
        for index, section in enumerate(latest_sections)
    ]
    revised_content = "\n\n".join(
        str(section.get("content") or "").strip()
        for section in revised_sections
    )
    if revised_sections == latest_sections:
        return draft, current_latest
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
        model=generated.model,
        prompt_snapshot=f"{CHAPTER_GENERATION_ENGINE}:section_ai_revision",
        feedback=(
            f"AI 按要求修改小节《{target_input.title}》："
            f"{revision_instruction}"
        ),
        input_signature=after.input_signature,
        used_material_ids_json=used_material_ids,
        material_snapshot_json=_snapshot(
            after,
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
        "last_requested_ai_model": ai_model,
        "last_resolved_ai_model": generated.model,
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
    latest = await _latest_version(
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
    blocking_issue = await db.scalar(
        select(MemoirCompletionIssue.id)
        .where(
            MemoirCompletionIssue.project_id == project_id,
            MemoirCompletionIssue.chapter_id == chapter_id,
            MemoirCompletionIssue.channel
            == MemoirCompletionIssueChannel.MANUAL_REVIEW.value,
            MemoirCompletionIssue.status
            != MemoirCompletionIssueStatus.CLOSED.value,
        )
        .limit(1)
    )
    if blocking_issue is not None:
        raise HTTPException(status_code=409, detail="本章还有一项需要确认")
    draft.status = MemoirBookChapterDraftStatus.CONFIRMED.value
    draft.updated_at = datetime.now(UTC).replace(tzinfo=None)
    db.add(draft)
    await db.flush()
    return draft, latest


async def get_chapter_draft(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> tuple[MemoirBookChapterDraft, MemoirBookChapterDraftVersion]:
    draft = await db.scalar(
        select(MemoirBookChapterDraft).where(
            MemoirBookChapterDraft.project_id == project_id,
            MemoirBookChapterDraft.chapter_id == chapter_id,
        )
    )
    latest = await _latest_version(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    if draft is None or latest is None:
        raise HTTPException(status_code=404, detail="章节正文尚未生成")
    return draft, latest


def draft_response(
    draft: MemoirBookChapterDraft,
    version: MemoirBookChapterDraftVersion,
) -> MemoirLayeredChapterDraftResponse:
    return MemoirLayeredChapterDraftResponse(
        id=draft.id,
        project_id=draft.project_id,
        chapter_id=draft.chapter_id,
        status=draft.status,
        latest_version=MemoirLayeredChapterDraftVersionResponse(
            id=version.id,
            draft_id=version.draft_id,
            project_id=version.project_id,
            chapter_id=version.chapter_id,
            version=version.version,
            content=version.content,
            sections=list(version.section_snapshot_json),
            input_signature=version.input_signature,
            used_material_ids=list(version.used_material_ids_json),
            material_snapshot=list(version.material_snapshot_json),
            created_by=version.created_by,
            model=version.model,
            created_at=version.created_at,
        ),
        created_at=draft.created_at,
        updated_at=draft.updated_at,
    )
