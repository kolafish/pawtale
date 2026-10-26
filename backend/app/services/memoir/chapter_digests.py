from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import logging
import time
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.application_errors import InsufficientAICreditError
from app.schemas.memoir_editorial import StructureFinding, WritingGuidance
from app.domain.memoir.types import (
    MemoirSectionStatus,
    MemoirSemanticCacheStatus,
)
from app.models.memoir import (
    MemoirBookPlanChapter,
    MemoirChapterDigest,
    MemoirSection,
    MemoirSectionDigest,
)
from app.services.ai_factory import get_ai_service
from app.services.ai_references import ModelReferenceMap
from app.services.ai_calls import (
    AIInvalidStructuredOutputError,
    AIOutputTruncatedError,
    generate_structured_with_client,
)
from app.services.ai_timeouts import (
    ai_error_metadata,
    ai_text_timeout_seconds,
    ai_user_error_message,
)
from app.services.ai_types import AITextClient
from app.services.ai_workloads import (
    AITextWorkload,
    ai_text_workload_policy,
)
from app.services.memoir import completion_issues, section_digests
from app.services.memoir.prompt_budget import checked_review_prompt
from app.services.memoir.semantic_state import semantic_json_signature


CHAPTER_DIGEST_ENGINE = "llm_chapter_digest_v5"
CHAPTER_DIGEST_POLICY = ai_text_workload_policy(
    AITextWorkload.LONG_GENERATION_STRUCTURED
)
CHAPTER_DIGEST_MAX_TOKENS = CHAPTER_DIGEST_POLICY.max_tokens
CHAPTER_DIGEST_SYSTEM_PROMPT = (
    "你是一位克制的中文回忆录主编。"
    "你根据小章节摘要判断整章的阅读体验，不创造新事实。"
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


class _ChapterDigestIssueOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_type: str = ""
    channel: str = ""
    question: str = ""
    reason: str = ""
    priority: str = ""
    section_ids: list[str] = Field(default_factory=list)


class _ChapterDigestOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    focus: str = ""
    structure_findings: list[StructureFinding] = Field(default_factory=list)
    writing_guidance: list[WritingGuidance] = Field(default_factory=list)
    candidate_issues: list[_ChapterDigestIssueOutput] = Field(default_factory=list)


def _chapter_digest_input_signature(
    *,
    chapter_id: str,
    chapter_title: str,
    include_in_book: bool,
    sections: tuple[ChapterDigestSection, ...],
) -> str:
    return semantic_json_signature(
        {
            "chapter": {
                "id": chapter_id,
                "title": chapter_title,
                "include_in_book": include_in_book,
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
    )


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


def _sections_with_included_materials(
    chapter: MemoirBookPlanChapter,
) -> list[MemoirSection]:
    return sorted(
        (
            section
            for section in chapter.sections
            if section.status == MemoirSectionStatus.ACTIVE.value
            and section.include_in_book
            and any(material.include_in_book for material in section.materials)
        ),
        key=lambda item: (item.sort_order, item.id),
    )


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
    sections = _sections_with_included_materials(chapter)
    if not sections:
        raise HTTPException(status_code=422, detail="章节没有可收录的小章节")
    for index, section in enumerate(sections):
        if progress_callback is not None:
            await progress_callback(
                {
                    "stage": "checking_sections",
                    "total_section_count": len(sections),
                    "checked_section_count": index,
                    "provider_request_pending": True,
                    "current_section_id": section.id,
                    "current_section_title": section.title,
                }
            )
        try:
            await section_digests.generate_or_reuse_section_digest(
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
                    "provider_request_pending": False,
                    "current_section_id": section.id,
                    "current_section_title": section.title,
                }
            )


def chapter_digest_context(
    chapter: MemoirBookPlanChapter,
    *,
    section_digests_by_id: Mapping[str, MemoirSectionDigest],
) -> ChapterDigestContext:
    sections: list[ChapterDigestSection] = []
    material_ids: set[str] = set()
    for section in _sections_with_included_materials(chapter):
        digest = section_digests_by_id.get(section.id)
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
    section_tuple = tuple(sections)
    return ChapterDigestContext(
        project_id=chapter.project_id,
        chapter_id=chapter.id,
        chapter_title=chapter.title,
        chapter_sort_order=chapter.sort_order,
        include_in_book=chapter.include_in_book,
        sections=section_tuple,
        allowed_material_ids=frozenset(material_ids),
        input_signature=_chapter_digest_input_signature(
            chapter_id=chapter.id,
            chapter_title=chapter.title,
            include_in_book=chapter.include_in_book,
            sections=section_tuple,
        ),
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
    digests = {}
    for section in chapter.sections:
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
        if digest is not None:
            digests[section.id] = digest
    return chapter_digest_context(
        chapter,
        section_digests_by_id=digests,
    )


async def get_current_chapter_digest(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    context: ChapterDigestContext | None = None,
) -> MemoirChapterDigest | None:
    """Return the digest for the current section state without invoking AI."""
    if context is None:
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
        select(MemoirChapterDigest).where(
            MemoirChapterDigest.project_id == project_id,
            MemoirChapterDigest.chapter_id == chapter_id,
            MemoirChapterDigest.engine == CHAPTER_DIGEST_ENGINE,
            MemoirChapterDigest.input_signature == context.input_signature,
        )
    )


def _section_references(context: ChapterDigestContext) -> ModelReferenceMap:
    return ModelReferenceMap(
        "S",
        (section.section_id for section in context.sections),
        labels={
            section.section_id: f"第{index}小节《{section.title}》"
            for index, section in enumerate(context.sections, start=1)
        },
        fallback_label="相关小节",
    )


def build_chapter_digest_prompt(context: ChapterDigestContext) -> str:
    references = _section_references(context)
    sections = "\n\n".join(
        (
            f"section_id: {references.encode(item.section_id)}\n"
            f"标题: {item.title}\n"
            f"类型: {item.section_type}\n"
            f"摘要: {item.digest.get('summary', '')}\n"
            f"重点: {item.digest.get('focus', '')}\n"
            f"已讲清: {item.digest.get('covered_points', [])}\n"
            f"编辑建议: {item.digest.get('editorial_guidance', [])}\n"
            f"候选补充: {item.digest.get('candidate_issues', [])}"
        )
        for item in context.sections
    )
    prompt = f"""请通读回忆录章节“{context.chapter_title}”的小节摘要，并据此评估结构。你未收到原始讲述或完整正文。

有序小章节：
{sections}

请输出严格 JSON：
{{
  "summary": "整章内容的一到三句概括",
  "focus": "整章最主要的叙事重点",
  "structure_findings": [{{"kind":"chapter_structure|section_structure|title_scope","section_ids":["相关 section_id"],"reason":"目录、归属、拆合或标题范围问题及依据"}}],
  "writing_guidance": [{{"section_ids":["适用 section_id；整章适用时为空"],"instruction":"既定小节内的表达、去重、顺序或衔接建议"}}],
  "candidate_issues": [
    {{
      "issue_type": "context|sequence|relationship|motivation|impact|meaning|clarity|structure|conflict",
      "channel": "interview|manual_review",
      "question": "确实需要用户处理时的一句自然问题",
      "reason": "为什么不处理会明显影响理解",
      "priority": "high|medium|low",
      "section_ids": ["相关 section_id"]
    }}
  ]
}}

要求：
- 这是供自己和亲友阅读的私人回忆录，不按出版或公开传播标准审查隐私。
- 普通经历、婚姻家庭、个人感受、亲友姓名或称呼、爱好默认保留，不提出隐私追问或匿名化建议。
- 私人书稿不产生 privacy 事项；尊重用户已作出的保留决定和明确不收录的要求，不自行改变收录范围。
- 先判断整章是否自然成立，不按固定模板逐项找缺口。
- 汇总并筛选各小章节的候选补充；不要让小章节发现绕过整章判断直接要求用户处理。
- 已有素材足够的问题由编辑处理，不要求用户补讲：目录、归属、拆合、标题范围问题写入 structure_findings，由目录编辑核对素材后自动调整；既定小节内的表达、去重、顺序和衔接写入 writing_guidance，只关联实际适用的小节。
- candidate_issues 数量不设配额，只保留彼此独立且用户回答或确认后会明显改善结果的问题，并合并相似问题。
- interview 只用于可选补充：不补也不影响正文生成、通读和确认。
- manual_review 只用于输入明确支持、必须由用户决定的取舍，未处理时会阻止章节确认。仅由摘要差异推测的事实矛盾不能升级为阻塞事项，应在 writing_guidance 中说明需要核对的原素材，不自行选定事实。
- 先区分不同人物、事件、时间和当时/现在的看法；这些差异不自动构成矛盾。摘要保留原有不确定性、否定、转述归属与程度，不声称完成原始事实核验。
- 已答、明确不记得或不愿说明的不重复追问；不凭摘要未提及就断定素材缺失。无必要问题时 candidate_issues 为空。
- question 和 reason 会直接展示给用户；引用小章节时使用标题。
- section_ids 只能使用输入中出现的本次请求短编号 section_id。
- 不能创建输入摘要之外的新事实。
- 直接输出 JSON 对象，不要输出分析过程、解释或 JSON 之外的文字。
"""
    return checked_review_prompt(prompt, subject="章节")


def normalize_chapter_digest(
    payload: dict[str, Any],
    *,
    section_references: ModelReferenceMap,
) -> dict:
    visible = section_references.render_text
    summary = _compact(visible(payload.get("summary")), limit=800)
    if not summary:
        raise ValueError("chapter digest summary is empty")
    focus = _compact(visible(payload.get("focus")), limit=400)
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
        "conflict",
    }
    candidate_values = payload.get("candidate_issues")
    if not isinstance(candidate_values, list):
        candidate_values = []
    for candidate in candidate_values:
        if not isinstance(candidate, dict):
            continue
        question = _compact(visible(candidate.get("question")), limit=180)
        reason = _compact(visible(candidate.get("reason")), limit=300)
        if not question or not reason:
            continue
        section_ids = section_references.decode_many(
            candidate.get("section_ids"),
            limit=8,
        )
        issue_type = _compact(candidate.get("issue_type"), limit=24)
        if issue_type == "privacy":
            continue
        channel = _compact(candidate.get("channel"), limit=24)
        priority = _compact(candidate.get("priority"), limit=12)
        issue_type = issue_type if issue_type in allowed_types else "clarity"
        issues.append(
            {
                "issue_type": issue_type,
                "channel": (
                    "manual_review"
                    if channel == "manual_review"
                    and issue_type in {"conflict", "structure"}
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
                "material_ids": [],
            }
        )
    return {
        "summary": summary,
        "focus": focus,
        "structure_findings": [
            StructureFinding(
                kind=finding.kind,
                section_ids=list(dict.fromkeys(section_references.decode(ref) for ref in finding.section_ids)),
                reason=visible(finding.reason),
            ).model_dump()
            for raw in payload.get("structure_findings", [])
            for finding in [StructureFinding.model_validate(raw)]
        ],
        "writing_guidance": [
            WritingGuidance(
                section_ids=list(dict.fromkeys(section_references.decode(ref) for ref in guidance.section_ids)),
                instruction=visible(guidance.instruction),
            ).model_dump()
            for raw in payload.get("writing_guidance", [])
            for guidance in [WritingGuidance.model_validate(raw)]
        ],
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
    async with AsyncSession(bind=db.bind, expire_on_commit=False) as inputs_db:
        context = await load_chapter_digest_context(
            inputs_db,
            project_id=project_id,
            chapter_id=chapter_id,
        )
    await db.execute(
        delete(MemoirChapterDigest).where(
            MemoirChapterDigest.project_id == project_id,
            MemoirChapterDigest.chapter_id == chapter_id,
            MemoirChapterDigest.engine == CHAPTER_DIGEST_ENGINE,
            MemoirChapterDigest.input_signature != context.input_signature,
        )
    )
    existing = await db.scalar(
        select(MemoirChapterDigest).where(
            MemoirChapterDigest.project_id == project_id,
            MemoirChapterDigest.chapter_id == chapter_id,
            MemoirChapterDigest.engine == CHAPTER_DIGEST_ENGINE,
            MemoirChapterDigest.input_signature == context.input_signature,
        )
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
                "provider_request_pending": True,
                "provider": getattr(client, "provider_name", None),
                "resolved_ai_model": getattr(client, "model_name", ai_model),
                "total_section_count": len(context.sections),
                "checked_section_count": len(context.sections),
                "current_section_id": None,
                "current_section_title": "",
            }
        )
    # Stale-cache bookkeeping is complete. The context is a value snapshot and
    # is revalidated after generation, so no transaction needs to span the call.
    await db.commit()
    timeout_seconds = ai_text_timeout_seconds(
        CHAPTER_DIGEST_POLICY.timeout_profile
    )
    response_diagnostics: dict[str, object] = {}
    started_at = time.monotonic()
    try:
        generated = await generate_structured_with_client(
            client,
            CHAPTER_DIGEST_SYSTEM_PROMPT,
            prompt,
            output_model=_ChapterDigestOutput,
            requested_model=ai_model,
            temperature=0.2,
            max_tokens=CHAPTER_DIGEST_MAX_TOKENS,
            timeout=timeout_seconds,
            max_attempts=CHAPTER_DIGEST_POLICY.max_attempts,
        )
        response_diagnostics = generated.response.diagnostics()
        digest_json = normalize_chapter_digest(
            generated.value.model_dump(),
            section_references=_section_references(context),
        )
        # Validate committed inputs in a fresh identity map without expiring
        # unrelated task/session objects owned by the calling workflow.
        async with AsyncSession(bind=db.bind, expire_on_commit=False) as inputs_db:
            current_context = await load_chapter_digest_context(
                inputs_db,
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
    except (HTTPException, InsufficientAICreditError):
        raise
    except Exception as exc:
        logger.exception(
            "memoir chapter digest generation failed chapter_id=%s",
            chapter_id,
        )
        digest_json = {}
        status = MemoirSemanticCacheStatus.FAILED
        if isinstance(exc, AIOutputTruncatedError):
            error = "章节审阅输出被截断，请重新检查本章"
        elif isinstance(exc, AIInvalidStructuredOutputError):
            error = "章节审阅没有返回可识别的结构，请重新检查本章"
        else:
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
    if progress_callback is not None:
        await progress_callback({"provider_request_pending": False})
    return digest


def writing_guidance(digest: MemoirChapterDigest | None) -> list[WritingGuidance]:
    if digest is None or digest.status != MemoirSemanticCacheStatus.READY.value:
        return []
    return [WritingGuidance.model_validate(raw) for raw in digest.digest_json.get("writing_guidance", [])]
