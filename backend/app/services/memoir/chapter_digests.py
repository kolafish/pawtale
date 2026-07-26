from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
import logging
import time
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import (
    MemoirSectionStatus,
    MemoirSemanticCacheStatus,
)
from app.models.memoir import (
    MemoirBookPlanChapter,
    MemoirChapterDigest,
    MemoirSection,
)
from app.services.ai_factory import get_ai_service
from app.services.ai_timeouts import (
    AITextTimeoutProfile,
    ai_error_metadata,
    ai_text_timeout_seconds,
    ai_user_error_message,
)
from app.services.ai_types import AITextClient
from app.services.json_utils import extract_json_payload
from app.services.memoir import completion_issues, section_digests
from app.services.memoir.semantic_state import semantic_json_signature


CHAPTER_DIGEST_ENGINE = "llm_chapter_digest_v1"
CHAPTER_DIGEST_MAX_TOKENS = 8000
CHAPTER_DIGEST_SYSTEM_PROMPT = (
    "你是一位克制的中文回忆录主编。"
    "你根据小章节摘要判断整章的结构和阅读体验，不创造新事实。"
)
logger = logging.getLogger(__name__)
ChapterDigestProgressCallback = Callable[
    [dict[str, object]],
    Awaitable[None],
]


@dataclass(frozen=True)
class ChapterDigestSection:
    section_id: str
    title: str
    section_type: str
    sort_order: int
    digest_id: str
    digest_signature: str
    digest: dict


@dataclass(frozen=True)
class ChapterDigestContext:
    project_id: str
    chapter_id: str
    chapter_title: str
    chapter_sort_order: int
    include_in_book: bool
    sections: tuple[ChapterDigestSection, ...]
    allowed_material_ids: frozenset[str]
    input_signature: str


def _compact(value: object, *, limit: int) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _string_list(value: object, *, limit: int, item_limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = _compact(item, limit=item_limit)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


async def _load_chapter(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> MemoirBookPlanChapter:
    chapter = await db.scalar(
        select(MemoirBookPlanChapter)
        .where(
            MemoirBookPlanChapter.project_id == project_id,
            MemoirBookPlanChapter.id == chapter_id,
        )
        .options(
            selectinload(MemoirBookPlanChapter.sections).selectinload(
                MemoirSection.materials
            )
        )
    )
    if chapter is None:
        raise HTTPException(status_code=404, detail="章节不存在")
    return chapter


async def ensure_section_digests(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    ai_model: str | None = None,
    ai_client: AITextClient | None = None,
    progress_callback: ChapterDigestProgressCallback | None = None,
) -> None:
    chapter = await _load_chapter(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    sections = sorted(
        (
            section
            for section in chapter.sections
            if section.status == MemoirSectionStatus.ACTIVE.value
            and section.include_in_book
        ),
        key=lambda item: (item.sort_order, item.id),
    )
    if not sections:
        raise HTTPException(status_code=422, detail="章节没有可收录的小章节")
    for index, section in enumerate(sections):
        if progress_callback is not None:
            await progress_callback(
                {
                    "stage": "checking_sections",
                    "total_section_count": len(sections),
                    "checked_section_count": index,
                    "current_section_id": section.id,
                    "current_section_title": section.title,
                }
            )
        try:
            await completion_issues.refresh_section_issues(
                db,
                project_id=project_id,
                section_id=section.id,
                ai_model=ai_model,
                ai_client=ai_client,
            )
        except HTTPException:
            # Section digests are reusable semantic caches. Preserve completed
            # provider work even if a later section or the chapter digest fails.
            await db.commit()
            raise
        digest = await section_digests.get_current_section_digest(
            db,
            project_id=project_id,
            section_id=section.id,
        )
        if digest is None:
            raise HTTPException(
                status_code=422,
                detail=f"小章节“{section.title}”审阅失败",
            )
        if digest.status != MemoirSemanticCacheStatus.READY.value:
            raise HTTPException(
                status_code=422,
                detail=digest.error_message or f"小章节“{section.title}”审阅失败",
            )
        await db.commit()
        if progress_callback is not None:
            await progress_callback(
                {
                    "stage": "checking_sections",
                    "total_section_count": len(sections),
                    "checked_section_count": index + 1,
                    "current_section_id": section.id,
                    "current_section_title": section.title,
                }
            )


async def load_chapter_digest_context(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> ChapterDigestContext:
    chapter = await _load_chapter(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    sections: list[ChapterDigestSection] = []
    material_ids: set[str] = set()
    for section in sorted(
        chapter.sections,
        key=lambda item: (item.sort_order, item.id),
    ):
        if (
            section.status != MemoirSectionStatus.ACTIVE.value
            or not section.include_in_book
        ):
            continue
        digest = await section_digests.get_current_section_digest(
            db,
            project_id=project_id,
            section_id=section.id,
        )
        if digest is None:
            raise HTTPException(
                status_code=409,
                detail=f"小章节“{section.title}”摘要缺失或已过期",
            )
        sections.append(
            ChapterDigestSection(
                section_id=section.id,
                title=section.title,
                section_type=section.section_type,
                sort_order=section.sort_order,
                digest_id=digest.id,
                digest_signature=digest.input_signature,
                digest=digest.digest_json,
            )
        )
        material_ids.update(
            material.utterance_id
            for material in section.materials
            if material.include_in_book
        )
    payload = {
        "chapter": {
            "id": chapter.id,
            "title": chapter.title,
            "sort_order": chapter.sort_order,
            "include_in_book": chapter.include_in_book,
        },
        "sections": [
            {
                "id": item.section_id,
                "title": item.title,
                "type": item.section_type,
                "sort_order": item.sort_order,
                "digest_id": item.digest_id,
                "digest_signature": item.digest_signature,
                "digest_hash": semantic_json_signature(item.digest),
            }
            for item in sections
        ],
        "engine": CHAPTER_DIGEST_ENGINE,
    }
    return ChapterDigestContext(
        project_id=project_id,
        chapter_id=chapter.id,
        chapter_title=chapter.title,
        chapter_sort_order=chapter.sort_order,
        include_in_book=chapter.include_in_book,
        sections=tuple(sections),
        allowed_material_ids=frozenset(material_ids),
        input_signature=semantic_json_signature(payload),
    )


async def get_current_chapter_digest(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> MemoirChapterDigest | None:
    """Return the digest for the current section state without invoking AI."""
    try:
        context = await load_chapter_digest_context(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
        )
    except HTTPException as exc:
        if exc.status_code in {409, 422}:
            return None
        raise
    return await db.scalar(
        select(MemoirChapterDigest)
        .where(
            MemoirChapterDigest.project_id == project_id,
            MemoirChapterDigest.chapter_id == chapter_id,
            MemoirChapterDigest.engine == CHAPTER_DIGEST_ENGINE,
            MemoirChapterDigest.input_signature == context.input_signature,
        )
        .order_by(
            MemoirChapterDigest.updated_at.desc(),
            MemoirChapterDigest.id.desc(),
        )
        .limit(1)
    )


def build_chapter_digest_prompt(context: ChapterDigestContext) -> str:
    sections = "\n\n".join(
        (
            f"section_id: {item.section_id}\n"
            f"标题: {item.title}\n"
            f"类型: {item.section_type}\n"
            f"摘要: {item.digest.get('summary', '')}\n"
            f"重点: {item.digest.get('focus', '')}\n"
            f"已讲清: {item.digest.get('covered_points', [])}"
        )
        for item in context.sections
    )
    return f"""请审阅回忆录章节“{context.chapter_title}”的整体结构。

有序小章节：
{sections}

请输出严格 JSON：
{{
  "summary": "整章内容的一到三句概括",
  "focus": "整章最主要的叙事重点",
  "structure_guidance": ["只需编辑处理的顺序、去重、过渡、开头或结尾建议"],
  "candidate_issues": [
    {{
      "issue_type": "context|sequence|relationship|motivation|impact|meaning|clarity|structure",
      "channel": "interview|manual_review",
      "question": "确实需要用户处理时的一句自然问题",
      "reason": "为什么不处理会明显影响理解",
      "priority": "high|medium|low",
      "section_ids": ["相关 section_id"],
      "material_ids": ["相关 material_id，可为空"]
    }}
  ]
}}

要求：
- 先判断整章是否自然成立，不按固定模板逐项找缺口。
- 已有素材足够、只需重排或改写的问题写入 structure_guidance，不要求用户补讲。
- candidate_issues 最多三个；只保留用户回答或确认后会明显改善结果的问题。
- 不能创建输入摘要之外的新事实。
- 直接输出 JSON 对象，不要输出分析过程、解释或 JSON 之外的文字。
"""


def _llm_response_parts(
    response: str | tuple[str, dict],
) -> tuple[str, dict]:
    if isinstance(response, tuple):
        content, response_json = response
        return str(content), response_json if isinstance(response_json, dict) else {}
    return str(response), {}


def _llm_finish_reason(response_json: dict) -> str:
    choices = response_json.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        reason = choices[0].get("finish_reason")
        if isinstance(reason, str):
            return reason
    incomplete = response_json.get("incomplete_details")
    if isinstance(incomplete, dict):
        reason = incomplete.get("reason")
        if isinstance(reason, str):
            return reason
    status = response_json.get("status")
    return status if isinstance(status, str) else ""


def normalize_chapter_digest(
    payload: Any,
    *,
    allowed_section_ids: set[str],
    allowed_material_ids: set[str],
) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("chapter digest AI did not return a JSON object")
    summary = _compact(payload.get("summary"), limit=800)
    if not summary:
        raise ValueError("chapter digest summary is empty")
    focus = _compact(payload.get("focus"), limit=400)
    issues: list[dict] = []
    allowed_types = {
        "context",
        "sequence",
        "relationship",
        "motivation",
        "impact",
        "meaning",
        "clarity",
        "structure",
    }
    candidate_values = payload.get("candidate_issues")
    if not isinstance(candidate_values, list):
        candidate_values = []
    for candidate in candidate_values[:3]:
        if not isinstance(candidate, dict):
            continue
        question = _compact(candidate.get("question"), limit=180)
        reason = _compact(candidate.get("reason"), limit=300)
        if not question or not reason:
            continue
        section_ids = _string_list(
            candidate.get("section_ids"),
            limit=8,
            item_limit=36,
        )
        material_ids = _string_list(
            candidate.get("material_ids"),
            limit=12,
            item_limit=36,
        )
        if set(section_ids) - allowed_section_ids:
            raise ValueError("chapter digest referenced section outside allowlist")
        if set(material_ids) - allowed_material_ids:
            raise ValueError("chapter digest referenced material outside allowlist")
        issue_type = _compact(candidate.get("issue_type"), limit=24)
        channel = _compact(candidate.get("channel"), limit=24)
        priority = _compact(candidate.get("priority"), limit=12)
        issues.append(
            {
                "issue_type": (
                    issue_type if issue_type in allowed_types else "clarity"
                ),
                "channel": (
                    channel
                    if channel in {"interview", "manual_review"}
                    else "interview"
                ),
                "question": question,
                "reason": reason,
                "priority": (
                    priority
                    if priority in {"high", "medium", "low"}
                    else "medium"
                ),
                "section_ids": section_ids,
                "material_ids": material_ids,
            }
        )
    return {
        "summary": summary,
        "focus": focus,
        "structure_guidance": _string_list(
            payload.get("structure_guidance"),
            limit=8,
            item_limit=240,
        ),
        "candidate_issues": issues,
    }


async def generate_or_reuse_chapter_digest(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    ai_model: str | None = None,
    ai_client: AITextClient | None = None,
    force_regenerate: bool = False,
    progress_callback: ChapterDigestProgressCallback | None = None,
) -> MemoirChapterDigest:
    await ensure_section_digests(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        ai_model=ai_model,
        ai_client=ai_client,
        progress_callback=progress_callback,
    )
    context = await load_chapter_digest_context(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    previous = list(
        (
            await db.scalars(
                select(MemoirChapterDigest).where(
                    MemoirChapterDigest.project_id == project_id,
                    MemoirChapterDigest.chapter_id == chapter_id,
                    MemoirChapterDigest.engine == CHAPTER_DIGEST_ENGINE,
                )
            )
        ).all()
    )
    for item in previous:
        if (
            item.input_signature != context.input_signature
            and item.status == MemoirSemanticCacheStatus.READY.value
        ):
            item.status = MemoirSemanticCacheStatus.STALE.value
            db.add(item)
    existing = next(
        (
            item
            for item in previous
            if item.input_signature == context.input_signature
        ),
        None,
    )
    if (
        existing is not None
        and existing.status == MemoirSemanticCacheStatus.READY.value
        and not force_regenerate
    ):
        await completion_issues.sync_chapter_digest_issues(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
            input_signature=existing.input_signature,
            candidates=existing.digest_json.get("candidate_issues", []),
        )
        return existing
    prompt = build_chapter_digest_prompt(context)
    client = ai_client or get_ai_service(ai_model)
    if progress_callback is not None:
        await progress_callback(
            {
                "stage": "checking_chapter",
                "total_section_count": len(context.sections),
                "checked_section_count": len(context.sections),
                "current_section_id": None,
                "current_section_title": "",
            }
        )
    timeout_seconds = ai_text_timeout_seconds(AITextTimeoutProfile.GENERATION)
    response_diagnostics: dict[str, object] = {}
    started_at = time.monotonic()
    try:
        response = await client._call_llm(
            CHAPTER_DIGEST_SYSTEM_PROMPT,
            prompt,
            temperature=0.2,
            max_tokens=CHAPTER_DIGEST_MAX_TOKENS,
            timeout=timeout_seconds,
            return_raw=True,
        )
        content, response_json = _llm_response_parts(response)
        finish_reason = _llm_finish_reason(response_json)
        response_diagnostics = {
            "finish_reason": finish_reason,
            "response_char_count": len(content),
        }
        payload = extract_json_payload(content)
        if not isinstance(payload, dict):
            logger.warning(
                "memoir chapter digest returned invalid JSON chapter_id=%s "
                "provider=%s model=%s finish_reason=%s response_chars=%s",
                chapter_id,
                getattr(client, "provider_name", None),
                getattr(client, "model_name", ai_model),
                finish_reason or "<unknown>",
                len(content),
            )
            if finish_reason in {"length", "max_tokens", "incomplete"}:
                raise ValueError("章节审阅输出被截断，请重新检查本章")
            raise ValueError("章节审阅没有返回可识别的结构，请重新检查本章")
        digest_json = normalize_chapter_digest(
            payload,
            allowed_section_ids={
                item.section_id for item in context.sections
            },
            allowed_material_ids=set(context.allowed_material_ids),
        )
        db.expire_all()
        current_context = await load_chapter_digest_context(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
        )
        if current_context.input_signature != context.input_signature:
            raise HTTPException(
                status_code=409,
                detail="审阅期间章节内容已变化，请重试",
            )
        if existing is not None:
            await db.refresh(existing)
        status = MemoirSemanticCacheStatus.READY
        error = ""
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "memoir chapter digest generation failed chapter_id=%s",
            chapter_id,
        )
        digest_json = {}
        status = MemoirSemanticCacheStatus.FAILED
        error = ai_user_error_message(
            exc,
            "章节审阅服务暂时不可用，请稍后重试",
        )
        response_diagnostics = {
            **response_diagnostics,
            **ai_error_metadata(
                exc,
                requested_model=ai_model,
                provider=getattr(client, "provider_name", None),
                resolved_model=getattr(client, "model_name", ai_model),
                timeout_seconds=timeout_seconds,
                elapsed_ms=round((time.monotonic() - started_at) * 1000),
            ),
        }
    digest = existing or MemoirChapterDigest(
        project_id=project_id,
        chapter_id=chapter_id,
        input_signature=context.input_signature,
        engine=CHAPTER_DIGEST_ENGINE,
    )
    digest.status = status.value
    generation = {
        "requested_model": ai_model,
        "provider": getattr(client, "provider_name", None),
        "model": getattr(client, "model_name", ai_model),
        "engine": CHAPTER_DIGEST_ENGINE,
        "timeout_seconds": timeout_seconds,
        "elapsed_ms": round((time.monotonic() - started_at) * 1000),
        **response_diagnostics,
    }
    digest.digest_json = {
        **digest_json,
        "generation": generation,
    } if digest_json else {"generation": generation}
    digest.error_message = error
    digest.updated_at = datetime.now(UTC).replace(tzinfo=None)
    db.add(digest)
    await db.flush()
    if status == MemoirSemanticCacheStatus.READY:
        await completion_issues.sync_chapter_digest_issues(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
            input_signature=digest.input_signature,
            candidates=digest.digest_json.get("candidate_issues", []),
        )
    return digest
