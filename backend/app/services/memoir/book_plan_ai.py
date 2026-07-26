from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.schemas.memoir_book import MemoirBookChapterSourceRef, MemoirBookPlanChapterInput
from app.services.ai_calls import generate_structured_with_client
from app.services.ai_factory import get_ai_service
from app.services.ai_timeouts import ai_text_timeout_seconds
from app.services.ai_types import AITextClient
from app.services.json_utils import extract_json_payload
from app.services.ai_workloads import AITextWorkload, ai_text_workload_policy
from app.services.memoir.book_plan_sources import SourceSegment


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
    chapters: list[_BookPlanChapterOutput] = Field(min_length=1)


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


def build_plan_prompt(project_title: str, sources: list[SourceSegment], max_chapters: int) -> str:
    source_text = "\n\n".join(
        (
            f"source_index: {source.source_index}\n"
            f"utterance_id: {source.utterance_id}\n"
            f"message_id: {source.message_id}\n"
            f"text: {source.text}"
        )
        for source in sources
    )
    return f"""请把下面的口述内容整理成回忆录章节规划。

当前回忆录：{project_title}
最多章节数：{max_chapters}

请输出严格 JSON，不要 Markdown，不要解释。格式：
{{
  "title": "章节规划标题",
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
- 章节标题要短，像书稿目录，不要像 AI 总结标题。
- source_indices 必须来自给定 source_index，且每章至少包含一个来源。
- evidence_quote 必须逐字来自对应 source text。
- 如果素材很少，可以只返回 1 到 2 章。

口述内容：
{source_text}
"""


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
    max_chapters: int,
) -> tuple[str, list[MemoirBookPlanChapterInput]]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="AI 未返回有效章节规划 JSON")
    source_by_index = {source.source_index: source for source in sources}
    raw_chapters = payload.get("chapters", [])
    if not isinstance(raw_chapters, list):
        raise HTTPException(status_code=502, detail="AI 未返回有效章节列表")

    chapters: list[MemoirBookPlanChapterInput] = []
    for item in raw_chapters[:max_chapters]:
        if not isinstance(item, dict):
            continue
        title = string_value(item, "title")
        if not title:
            continue
        quote = string_value(item, "evidence_quote")
        refs: list[MemoirBookChapterSourceRef] = []
        seen_utterances: set[str] = set()
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
            f"utterance_id: {source.utterance_id}\n"
            f"created_at: {source.created_at}\n"
            f"record_summary: {_compact_text(source.summary, 140)}\n"
            f"representative_quote: {_compact_text(source.representative_quote or source.summary, 90)}"
        )
        for source in sources
    )
    return f"""请基于下面这个回忆录章节的全部来源记录摘要，重新拟一个正式章节名。

请输出严格 JSON，不要 Markdown，不要解释。格式：
{{
  "title": "章节标题"
}}

章节来源记录摘要：
{source_text or "无"}

要求：
- 只根据 record_summary 和 representative_quote，不新增事实、人物、地点或情绪。
- 必须综合全部来源记录；来源很多时，不要只看前几条，优先概括反复出现或最能代表本章的主题。
- 标题要像书稿目录，短而正式，建议 6 到 14 个汉字。
- 不要使用“转写”、“讲述”、“新收录”、“记录”、“素材”、“章节”等占位词。
- 只输出 JSON 对象，不要解释、引号包裹全文或编号。
"""


def clean_generated_chapter_title(content: str) -> str:
    payload = extract_json_payload(content)
    title = ""
    if isinstance(payload, dict):
        value = payload.get("title")
        title = value.strip() if isinstance(value, str) else ""
    if not title:
        lines = [line.strip() for line in str(content).splitlines() if line.strip()]
        title = lines[0] if lines else ""
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


async def generate_book_plan_from_sources(
    *,
    project_title: str,
    sources: list[SourceSegment],
    max_chapters: int,
    ai_model: str | None,
    ai_client: AITextClient | None = None,
) -> GeneratedBookPlan:
    prompt = build_plan_prompt(project_title, sources, max_chapters)
    client = ai_client
    try:
        if client is None:
            client = get_ai_service(ai_model)
        policy = ai_text_workload_policy(AITextWorkload.GENERATION_STRUCTURED)
        generated = await generate_structured_with_client(
            client,
            SYSTEM_PROMPT,
            prompt,
            output_model=_BookPlanOutput,
            requested_model=ai_model,
            temperature=0.2,
            max_tokens=policy.max_tokens,
            timeout=ai_text_timeout_seconds(policy.timeout_profile),
            max_attempts=policy.max_attempts,
        )
    except HTTPException as exc:
        if exc.status_code < 500:
            raise
        logger.exception("AI memoir book plan failed provider=%s", getattr(client, "provider_name", "unknown"))
        raise HTTPException(status_code=503, detail=AI_BOOK_PLAN_UNAVAILABLE_MESSAGE) from exc
    except Exception as exc:
        logger.exception("AI memoir book plan failed provider=%s", getattr(client, "provider_name", "unknown"))
        raise HTTPException(status_code=503, detail=AI_BOOK_PLAN_UNAVAILABLE_MESSAGE) from exc

    title, chapters = chapters_from_payload(
        generated.value.model_dump(),
        sources=sources,
        max_chapters=max_chapters,
    )
    return GeneratedBookPlan(
        title=title,
        chapters=chapters,
        model=generated.response.resolved_model,
        prompt=prompt,
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
        title=clean_generated_chapter_title(generated.response.content),
        model=generated.response.resolved_model,
        prompt=prompt,
    )
