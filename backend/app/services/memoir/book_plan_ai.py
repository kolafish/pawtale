from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.memoir.limits import MAX_MEMOIR_BOOK_PLAN_CHAPTERS
from app.schemas.memoir_book import MemoirBookChapterSourceRef, MemoirBookPlanChapterInput
from app.services.ai_calls import AIInvalidStructuredOutputError, AIOutputTruncatedError, generate_structured_with_client
from app.services.ai_factory import get_ai_service
from app.services.ai_timeouts import ai_text_timeout_seconds
from app.services.ai_types import AITextClient
from app.services.ai_workloads import AITextWorkload, ai_text_workload_policy
from app.services.memoir.title_guidance import title_naming_guidance
from app.services.memoir.book_plan_sources import SourceSegment
from app.services.memoir.material_roles import STORY_CONTENT_GUIDANCE, STORY_THEME_GUIDANCE


SYSTEM_PROMPT = "你是一位谨慎的中文回忆录章节规划编辑，只能根据给定口述内容整理章节。"
CHAPTER_TITLE_SYSTEM_PROMPT = "你是一位谨慎的中文回忆录目录编辑，只为已有章节重新拟标题。"
AI_BOOK_PLAN_UNAVAILABLE_MESSAGE = "AI 服务暂时不可用，请稍后重试。"
GENERIC_CHAPTER_TITLES = {"新收录转写", "新增转写", "转写记录", "新收录讲述", "新增讲述", "讲述记录", "新章节", "未命名章节"}
logger = logging.getLogger(__name__)


class _BookPlanChapterOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = ""
    source_indices: list[int] = Field(default_factory=list)
    evidence_quote: str = ""


class _BookPlanOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = ""
    not_story_source_indices: list[int] = Field(default_factory=list)
    chapters: list[_BookPlanChapterOutput] = Field(
        max_length=MAX_MEMOIR_BOOK_PLAN_CHAPTERS,
    )


class _ChapterTitleOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str


@dataclass(frozen=True)
class ChapterTitleSourceDigest:
    source_number: int
    utterance_id: str
    created_at: str
    summary: str
    representative_quote: str


@dataclass(frozen=True)
class GeneratedBookPlan:
    title: str
    chapters: list[MemoirBookPlanChapterInput]
    model: str | None
    prompt: str
    not_story_sources: tuple[SourceSegment, ...] = ()


@dataclass(frozen=True)
class GeneratedChapterTitle:
    title: str
    model: str | None
    prompt: str


def _compact_text(value: str, max_chars: int) -> str:
    text = " ".join(value.split())
    if len(text) <= max_chars:
        return text
    for marker in ("。", "！", "？", "；", ";", "!", "?"):
        index = text.find(marker)
        if 0 < index + 1 <= max_chars:
            return text[: index + 1].strip()
    return f"{text[: max_chars - 3].rstrip()}..."


def build_plan_prompt(
    project_title: str, sources: list[SourceSegment], interviewer_lines: dict[str, str] | None = None
) -> str:
    lines = interviewer_lines or {}
    source_text = "\n\n".join(
        (
            f"source_index: {source.source_index}\n"
            + (f"interviewer_line: {line}\n" if (line := lines.get(source.message_id or "")) else "")
            + f"text: {source.text}"
        )
        for source in sources
    )
    return f"""请把下面的口述内容整理成回忆录章节规划。

当前回忆录：{project_title}

请输出严格 JSON，不要 Markdown，不要解释。格式：
{{
  "title": "章节规划标题",
  "not_story_source_indices": [],
  "chapters": [
    {{
      "title": "章节标题",
      "source_indices": [1, 2],
      "evidence_quote": "必须来自 source text 的短句"
    }}
  ]
}}

要求：
- 只根据 source text 已经讲过的事实划分章节，不新增人物、时间、地点、事件或情绪。
- {STORY_THEME_GUIDANCE}
{title_naming_guidance("chapter")}
- 根据素材量和主题密度自主决定章节数量，不要为了凑数拆分，也不要为了减少章节强行合并。
- source_indices 必须来自给定 source_index，且每章至少包含一个来源。
- 每条来源是一段完整讲述，只能归入一个章节，不得拆分或跨章重复引用。
- evidence_quote 必须逐字来自对应 source text。
- 如果素材很少，只返回必要的少量章节。
- {STORY_CONTENT_GUIDANCE}
- 非故事的 source_index 明确放入 not_story_source_indices，不放进章节；其余来源全部放进章节，两者不能重叠或遗漏。全部是非故事时 chapters 返回空列表。

口述内容：
{source_text}
"""


NO_STORY_YET = "讲述里还没有可以写进书的故事"


def string_value(item: dict[str, Any], key: str) -> str:
    value = item.get(key)
    return value.strip() if isinstance(value, str) else ""


def source_indices(item: dict[str, Any]) -> list[int]:
    raw = item.get("source_indices", [])
    if not isinstance(raw, list):
        return []
    indices: list[int] = []
    for value in raw:
        if isinstance(value, int):
            indices.append(value)
        elif isinstance(value, str) and value.isdigit():
            indices.append(int(value))
    return indices


def chapters_from_payload(
    payload: Any,
    *,
    sources: list[SourceSegment],
) -> tuple[str, list[MemoirBookPlanChapterInput]]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="AI 未返回有效章节规划 JSON")
    source_by_index = {source.source_index: source for source in sources}
    raw_chapters = payload.get("chapters", [])
    if not isinstance(raw_chapters, list):
        raise HTTPException(status_code=502, detail="AI 未返回有效章节列表")
    if not raw_chapters:
        # Every source only answered the interviewer (a name, a greeting): there is no story to plan yet.
        raise HTTPException(status_code=422, detail=NO_STORY_YET)
    if len(raw_chapters) > MAX_MEMOIR_BOOK_PLAN_CHAPTERS:
        raise HTTPException(
            status_code=502,
            detail=f"AI 返回的章节数量超过 {MAX_MEMOIR_BOOK_PLAN_CHAPTERS} 章上限",
        )

    chapters: list[MemoirBookPlanChapterInput] = []
    seen_utterances: set[str] = set()
    for item in raw_chapters:
        if not isinstance(item, dict):
            continue
        title = string_value(item, "title")
        if not title:
            continue
        quote = string_value(item, "evidence_quote")
        refs: list[MemoirBookChapterSourceRef] = []
        for source_index in source_indices(item):
            source = source_by_index.get(source_index)
            if not source or source.utterance_id in seen_utterances:
                continue
            refs.append(source.ref(quote))
            seen_utterances.add(source.utterance_id)
        if not refs:
            continue
        chapters.append(
            MemoirBookPlanChapterInput(
                title=title,
                source_refs=refs,
            )
        )

    if not chapters:
        raise HTTPException(status_code=502, detail="AI 未返回可用章节规划")
    title = string_value(payload, "title") or "章节规划"
    return title, chapters


def build_chapter_title_prompt(sources: list[ChapterTitleSourceDigest]) -> str:
    source_text = "\n\n".join(
        (
            f"source {source.source_number}\n"
            f"created_at: {source.created_at}\n"
            f"record_summary: {_compact_text(source.summary, 140)}\n"
            f"representative_quote: {_compact_text(source.representative_quote or source.summary, 90)}"
        )
        for source in sources
    )
    return f"""请基于下面这个回忆录章节的全部来源记录摘要，重新拟一个章节名。

请输出严格 JSON，不要 Markdown，不要解释。格式：
{{
  "title": "章节标题"
}}

章节来源记录摘要：
{source_text or "无"}

要求：
- 只根据 record_summary 和 representative_quote；它们是摘要和摘录，不代表完整转写。created_at 是记录时间，不是事件发生时间。
- 必须阅读全部来源记录后拟题；来源很多时，不要只看前几条，不把局部事实扩大为整章事实。
{title_naming_guidance("chapter")}
- 不要使用“转写”、“讲述”、“新收录”、“记录”、“素材”、“章节”等占位词。
- 只输出 JSON 对象，不要解释、引号包裹全文或编号。
"""


def clean_generated_chapter_title(title: str) -> str:
    title = title.strip().strip("“”\"'「」《》")
    for prefix in ("章节标题：", "标题：", "章名：", "章节名："):
        if title.startswith(prefix):
            title = title[len(prefix):].strip()
    title = title.strip("。；;，, ")
    if title in GENERIC_CHAPTER_TITLES or "转写" in title or "讲述" in title or "新收录" in title:
        raise HTTPException(status_code=502, detail="AI 未返回可用章节标题")
    if not title:
        raise HTTPException(status_code=502, detail="AI 未返回可用章节标题")
    return title[:80]


def _source_counts(output: _BookPlanOutput) -> Counter[int]:
    # Repetition within one destination is unambiguous; conflicts across
    # chapters or between a chapter and non-story must remain unresolved.
    return Counter([index for chapter in output.chapters for index in set(chapter.source_indices)]
                   + list(set(output.not_story_source_indices)))


def _recover_partial_plan(output: _BookPlanOutput | None, expected: set[int]) -> _BookPlanOutput | None:
    if output is None:
        return None
    counts = _source_counts(output)
    unique = {index for index, count in counts.items() if count == 1 and index in expected}
    chapters = [chapter.model_copy(update={"source_indices": indices}) for chapter in output.chapters
                if chapter.title.strip() and (indices := list(dict.fromkeys(index for index in chapter.source_indices if index in unique)))]
    if not chapters:
        return None
    # Partial recovery can rescue positive assignments, but cannot authorize
    # durable exclusions. Non-story checkpoints require a fully valid response.
    return _BookPlanOutput(title=output.title, chapters=chapters, not_story_source_indices=[])


async def generate_book_plan_from_sources(
    *,
    project_title: str,
    sources: list[SourceSegment],
    interviewer_lines: dict[str, str],
    ai_model: str | None,
    ai_client: AITextClient | None = None,
) -> GeneratedBookPlan:
    prompt = build_plan_prompt(project_title, sources, interviewer_lines)
    expected = {source.source_index for source in sources}
    best_candidate: _BookPlanOutput | None = None
    best_coverage = -1
    class ValidatedBookPlanOutput(_BookPlanOutput):
        @model_validator(mode="after")
        def validate_source_coverage(self):
            nonlocal best_candidate, best_coverage
            # Capture only complete, schema-valid responses; keep one coherent
            # classification rather than merging contradictory attempts.
            candidate = _recover_partial_plan(self, expected)
            if candidate is not None and (coverage := sum(len(chapter.source_indices) for chapter in candidate.chapters)) > best_coverage:
                best_candidate, best_coverage = candidate, coverage
            counts = _source_counts(self)
            if (set(counts) != expected or any(count != 1 for count in counts.values())
                    or any(not chapter.title.strip() or not chapter.source_indices for chapter in self.chapters)):
                raise ValueError("每条来源必须且只能进入一个有效章节或明确列为非故事，不能遗漏、重复或添加来源")
            return self

    client = ai_client
    try:
        if client is None:
            client = get_ai_service(ai_model)
        policy = ai_text_workload_policy(AITextWorkload.GENERATION_STRUCTURED)
        generated = await generate_structured_with_client(
            client,
            SYSTEM_PROMPT,
            prompt,
            output_model=ValidatedBookPlanOutput,
            requested_model=ai_model,
            temperature=0.2,
            max_tokens=policy.max_tokens,
            timeout=ai_text_timeout_seconds(policy.timeout_profile),
            max_attempts=policy.max_attempts,
        )
        value, model = generated.value, generated.response.resolved_model
    except (AIInvalidStructuredOutputError, AIOutputTruncatedError) as exc:
        value = best_candidate
        if value is None:
            raise HTTPException(status_code=503, detail=AI_BOOK_PLAN_UNAVAILABLE_MESSAGE) from exc
        model = exc.response.resolved_model if exc.response else getattr(client, "model_name", ai_model)
        logger.warning("Partial memoir plan retained %s chapters; unresolved sources remain unassigned", len(value.chapters))
    except HTTPException as exc:
        if exc.status_code < 500:
            raise
        logger.exception("AI memoir book plan failed provider=%s", getattr(client, "provider_name", "unknown"))
        raise HTTPException(status_code=503, detail=AI_BOOK_PLAN_UNAVAILABLE_MESSAGE) from exc
    except Exception as exc:
        logger.exception("AI memoir book plan failed provider=%s", getattr(client, "provider_name", "unknown"))
        raise HTTPException(status_code=503, detail=AI_BOOK_PLAN_UNAVAILABLE_MESSAGE) from exc

    title, chapters = chapters_from_payload(
        value.model_dump(), sources=sources,
    ) if value.chapters else (value.title, [])
    return GeneratedBookPlan(
        title=title,
        chapters=chapters,
        model=model,
        prompt=prompt,
        not_story_sources=tuple(source for source in sources if source.source_index in value.not_story_source_indices),
    )


async def generate_chapter_title_from_sources(
    *,
    sources: list[ChapterTitleSourceDigest],
    ai_model: str | None,
    ai_client: AITextClient | None = None,
) -> GeneratedChapterTitle:
    prompt = build_chapter_title_prompt(sources)
    client = ai_client
    try:
        if client is None:
            client = get_ai_service(ai_model)
        policy = ai_text_workload_policy(AITextWorkload.INTERACTIVE_STRUCTURED)
        generated = await generate_structured_with_client(
            client,
            CHAPTER_TITLE_SYSTEM_PROMPT,
            prompt,
            output_model=_ChapterTitleOutput,
            requested_model=ai_model,
            temperature=0.25,
            max_tokens=policy.max_tokens,
            timeout=ai_text_timeout_seconds(policy.timeout_profile),
            max_attempts=policy.max_attempts,
        )
    except HTTPException as exc:
        if exc.status_code < 500:
            raise
        logger.exception("AI memoir chapter title failed provider=%s", getattr(client, "provider_name", "unknown"))
        raise HTTPException(status_code=503, detail=AI_BOOK_PLAN_UNAVAILABLE_MESSAGE) from exc
    except Exception as exc:
        logger.exception("AI memoir chapter title failed provider=%s", getattr(client, "provider_name", "unknown"))
        raise HTTPException(status_code=503, detail=AI_BOOK_PLAN_UNAVAILABLE_MESSAGE) from exc

    return GeneratedChapterTitle(
        title=clean_generated_chapter_title(generated.value.title),
        model=generated.response.resolved_model,
        prompt=prompt,
    )
