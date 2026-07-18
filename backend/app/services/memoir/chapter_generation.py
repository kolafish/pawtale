from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirBookPlanStatus,
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
from app.services.ai_factory import get_ai_service
from app.services.ai_types import AITextClient
from app.services.json_utils import extract_json_payload
from app.services.memoir.book_output_state import invalidate_book_output
from app.services.memoir.semantic_state import (
    semantic_json_signature,
    semantic_text_hash,
)


CHAPTER_GENERATION_ENGINE = "llm_layered_chapter_draft_v1"
CHAPTER_GENERATION_SYSTEM_PROMPT = (
    "你是一位中文回忆录编辑。"
    "只使用允许的原始讲述整理成第一人称完整章节，不创造事实。"
)


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
class ChapterGenerationContext:
    project_id: str
    chapter_id: str
    chapter_title: str
    materials: tuple[ChapterMaterialInput, ...]
    input_signature: str


@dataclass(frozen=True)
class GeneratedChapter:
    content: str
    used_material_ids: tuple[str, ...]
    missing_source_candidates: tuple[str, ...]
    editor_notes: tuple[str, ...]
    model: str | None


def _material_text(material: MemoirSectionMaterial) -> str:
    if material.transcript_version is not None:
        text = " ".join(material.transcript_version.content.split()).strip()
        if text:
            return text
    return " ".join(
        (
            material.utterance.transcript_edited
            or material.utterance.transcript_raw
            or ""
        ).split()
    ).strip()


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
        for material in sorted(
            section.materials,
            key=lambda item: (item.sort_order, item.id),
        ):
            if not material.include_in_book:
                continue
            text = _material_text(material)
            if not text:
                continue
            materials.append(
                ChapterMaterialInput(
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
            )
    if not materials:
        raise HTTPException(status_code=422, detail="章节没有可用于生成正文的素材")
    signature = semantic_json_signature(
        {
            "chapter": {
                "id": chapter.id,
                "title": chapter.title,
                "sort_order": chapter.sort_order,
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
        materials=tuple(materials),
        input_signature=signature,
    )


def build_chapter_generation_prompt(
    context: ChapterGenerationContext,
    *,
    editorial_guidance: list[str],
) -> str:
    blocks = "\n\n".join(
        (
            f"material_id: {item.material_id}\n"
            f"小章节: {item.section_title}\n"
            f"原始讲述: {item.text}"
        )
        for item in context.materials
    )
    guidance = "\n".join(
        f"- {' '.join(item.split())[:240]}"
        for item in editorial_guidance[:8]
        if item.strip()
    ) or "（无额外编辑建议）"
    return f"""请把下面的原始讲述整理为回忆录章节“{context.chapter_title}”。

编辑建议：
{guidance}

按既定顺序排列的允许素材：
{blocks}

输出严格 JSON：
{{
  "content": "完整、连贯、第一人称的章节正文",
  "used_material_ids": ["正文实际使用的 material_id"],
  "missing_source_candidates": ["想表达但现有素材没有支持的内容，只列出，不写入正文"],
  "editor_notes": ["可选的简短编辑说明"]
}}

要求：
- 一次输出完整章节，不拆成正文块或局部补丁。
- 只能使用输入中的事实和感受，可以调整顺序、合并重复、补充自然过渡。
- 不要为了完整而虚构时间、地点、人物、因果或心理活动。
- 不在正文中出现 material_id、编辑指令、“叙述者”等后台术语。
- 同一次讲述保持连续，不把一条 Material 拆散后穿插到多处。
"""


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


def normalize_generated_chapter(
    payload: Any,
    *,
    allowed_material_ids: set[str],
    model: str | None,
) -> GeneratedChapter:
    if not isinstance(payload, dict):
        raise ValueError("chapter generation AI did not return a JSON object")
    content = payload.get("content")
    content = content.strip() if isinstance(content, str) else ""
    if not content:
        raise ValueError("chapter content is empty")
    used = _string_list(
        payload.get("used_material_ids"),
        limit=len(allowed_material_ids),
        item_limit=36,
    )
    if not used:
        raise ValueError("chapter generation returned no used material")
    unexpected = set(used) - allowed_material_ids
    if unexpected:
        raise ValueError("chapter generation used material outside allowlist")
    if any(material_id in content for material_id in allowed_material_ids):
        raise ValueError("chapter content exposed internal material id")
    return GeneratedChapter(
        content=content,
        used_material_ids=tuple(used),
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
) -> GeneratedChapter:
    prompt = build_chapter_generation_prompt(
        context,
        editorial_guidance=editorial_guidance,
    )
    client = ai_client or get_ai_service(ai_model)
    raw = await client._call_llm(
        CHAPTER_GENERATION_SYSTEM_PROMPT,
        prompt,
        temperature=0.3,
        max_tokens=8000,
        timeout=120.0,
    )
    return normalize_generated_chapter(
        extract_json_payload(str(raw)),
        allowed_material_ids={
            material.material_id for material in context.materials
        },
        model=getattr(client, "model_name", ai_model),
    )


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


async def generate_and_save_chapter_draft(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    editorial_guidance: list[str],
    ai_model: str | None = None,
    ai_client: AITextClient | None = None,
) -> tuple[MemoirBookChapterDraft, MemoirBookChapterDraftVersion]:
    before = await load_chapter_generation_context(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    generated = await generate_chapter(
        before,
        editorial_guidance=editorial_guidance,
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
    if (
        latest is not None
        and latest.input_signature == before.input_signature
        and latest.content.strip() == generated.content.strip()
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
    if latest.input_signature != context.input_signature:
        raise HTTPException(status_code=409, detail="章节素材已变化，请重新生成正文")
    blocking_issue = await db.scalar(
        select(MemoirCompletionIssue.id)
        .where(
            MemoirCompletionIssue.project_id == project_id,
            MemoirCompletionIssue.chapter_id == chapter_id,
            MemoirCompletionIssue.status
            != MemoirCompletionIssueStatus.CLOSED.value,
        )
        .limit(1)
    )
    if blocking_issue is not None:
        raise HTTPException(status_code=409, detail="本章还有一个补全问题待处理")
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
