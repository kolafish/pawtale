from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from app.config import get_settings
from app.services.ai_factory import (
    DEEPSEEK_V4_FLASH_MODEL,
    DEEPSEEK_V4_PRO_MODEL,
    VOLC_DOUBAO_PRO_MODEL,
    get_ai_service,
)
from app.services.ai_types import AITextClient
from app.services.json_utils import extract_json_payload


SYSTEM_PROMPT = "你是一位谨慎的中文家庭回忆录编辑，只把已有口述材料整理成适合成书阅读的前置文案。"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GeneratedChapterIntro:
    chapter_id: str
    intro: str


@dataclass(frozen=True)
class GeneratedManuscriptCopy:
    book_title: str
    cover_subtitle: str
    preface: str
    chapter_intros: list[GeneratedChapterIntro]
    back_cover_quote: str
    model: str | None
    prompt: str


@dataclass(frozen=True)
class GeneratedTitleSuggestions:
    suggestions: list[str]
    model: str | None
    prompt: str


def _compact_text(value: str, max_chars: int) -> str:
    text = " ".join(value.replace("\r\n", "\n").replace("\r", "\n").split())
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip("，。；、,. ") + "..."


def _string(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(value, 0)
    return 0


def _source_quote(raw_refs: object, fallback_content: str) -> str:
    if isinstance(raw_refs, list):
        for raw_ref in raw_refs:
            if not isinstance(raw_ref, dict):
                continue
            quote = _compact_text(_string(raw_ref.get("quote")), 90)
            if quote:
                return quote
    return _compact_text(fallback_content, 90)


def _chapter_context(snapshot: list[dict[str, object]]) -> str:
    parts: list[str] = []
    for fallback_index, item in enumerate(snapshot, start=1):
        chapter_id = _string(item.get("chapter_id"))
        title = _string(item.get("chapter_title")) or _string(item.get("title")) or f"第{fallback_index}章"
        content = _string(item.get("content"))
        if not chapter_id or not content:
            continue
        parts.append(
            "\n".join(
                [
                    f"chapter_id: {chapter_id}",
                    f"chapter_index: {_int(item.get('chapter_index')) or fallback_index}",
                    f"title: {title}",
                    f"source_count: {_int(item.get('source_count'))}",
                    f"representative_quote: {_source_quote(item.get('source_refs'), content)}",
                    f"content_excerpt: {_compact_text(content, 420)}",
                ]
            )
        )
    return "\n\n".join(parts)


def build_manuscript_copy_prompt(*, title: str, snapshot: list[dict[str, object]]) -> str:
    chapters = _chapter_context(snapshot)
    return f"""请根据下面已经确认的回忆录章节，生成适合 PDF 成书的前置文案。

当前书名：{title}

请输出严格 JSON，不要 Markdown，不要解释。格式：
{{
  "book_title": "正式书名",
  "cover_subtitle": "封面短副标题",
  "preface": "前言正文",
  "chapter_intros": [
    {{"chapter_id": "对应 chapter_id", "intro": "章节导语"}}
  ],
  "back_cover_quote": "封底短句"
}}

要求：
- 只能基于给定章节标题、代表性口述和正文摘录，不新增未出现的人物、地点、事件或评价。
- book_title 要像家人会保存的一本书名，避免“主题回忆录”“章节规划”等内部词；如果原书名已经自然，可以保留。
- cover_subtitle 控制在 12 到 28 个汉字，可概括内容；素材不足时用“根据口述访谈整理而成”。
- preface 控制在 120 到 220 个汉字，语气安静、可信、像书，不要出现“这本书记录的是「书名」”“后台”“系统”“AI生成”等模板词。
- 每个 chapter_intro 控制在 35 到 90 个汉字，像书籍导语，不要写“这一章主要记录”“从口述内容看”“相关的回忆”。
- back_cover_quote 控制在 15 到 45 个汉字，可以使用口述中的短句或克制的总结，不要夸张文学化。

章节材料：
{chapters}
"""


def build_title_suggestion_prompt(*, title: str, snapshot: list[dict[str, object]], count: int) -> str:
    chapters = _chapter_context(snapshot)
    return f"""请根据下面已经确认的回忆录章节，为这本家庭回忆录拟 {count} 个候选书名。

当前暂定书名：{title}

请输出严格 JSON，不要 Markdown，不要解释。格式：
{{
  "suggestions": ["书名一", "书名二"]
}}

要求：
- 只能基于给定章节标题、代表性口述和正文摘录，不新增未出现的人物、地点或事件。
- 候选名要像家人会保存、下载、转发和打印的一本书，不要像系统项目名。
- 可以包含朴素正式型、温和口语型、略有书感型，但都要克制。
- 不要出现“主题回忆录”“章节规划”“AI”“生成”等内部词。
- 每个候选 4 到 14 个汉字，最多一个书名号内容，不要副标题。
- 至少给出 {count} 个不同候选。

章节材料：
{chapters}
"""


def _text_ai_configured(ai_model: str | None) -> bool:
    settings = get_settings()
    model = (ai_model or settings.default_ai_model or "").lower()
    if model in {"deepseek", DEEPSEEK_V4_FLASH_MODEL, DEEPSEEK_V4_PRO_MODEL}:
        return bool(settings.deepseek_api_key.strip())
    if model in {"volc-doubao-pro", VOLC_DOUBAO_PRO_MODEL}:
        return bool(settings.volc_ark_api_key.strip())
    if model.startswith("claude") or not model:
        return bool(settings.anthropic_auth_token.strip())
    return bool(
        settings.deepseek_api_key.strip()
        or settings.volc_ark_api_key.strip()
        or settings.anthropic_auth_token.strip()
    )


def _clean_copy_text(value: object, *, max_chars: int) -> str:
    text = _compact_text(_string(value), max_chars)
    forbidden = ("后台", "系统", "AI生成", "AI 生成", "这一章主要记录", "从口述内容看", "相关的回忆")
    if any(term in text for term in forbidden):
        return ""
    return text


def _clean_title(value: object) -> str:
    title = _compact_text(_string(value), 32).strip("《》「」“”\"' ")
    forbidden = ("主题回忆录", "章节规划", "AI", "生成", "系统", "后台")
    if not title or any(term in title for term in forbidden):
        return ""
    return title[:32]


def _copy_from_payload(
    payload: Any,
    *,
    prompt: str,
    model: str | None,
) -> GeneratedManuscriptCopy | None:
    if not isinstance(payload, dict):
        return None
    raw_intros = payload.get("chapter_intros")
    chapter_intros: list[GeneratedChapterIntro] = []
    if isinstance(raw_intros, list):
        for raw_intro in raw_intros:
            if not isinstance(raw_intro, dict):
                continue
            chapter_id = _string(raw_intro.get("chapter_id"))
            intro = _clean_copy_text(raw_intro.get("intro"), max_chars=120)
            if chapter_id and intro:
                chapter_intros.append(GeneratedChapterIntro(chapter_id=chapter_id, intro=intro))
    copy = GeneratedManuscriptCopy(
        book_title=_clean_copy_text(payload.get("book_title"), max_chars=32),
        cover_subtitle=_clean_copy_text(payload.get("cover_subtitle"), max_chars=40),
        preface=_clean_copy_text(payload.get("preface"), max_chars=260),
        chapter_intros=chapter_intros,
        back_cover_quote=_clean_copy_text(payload.get("back_cover_quote"), max_chars=60),
        model=model,
        prompt=prompt,
    )
    if copy.book_title or copy.cover_subtitle or copy.preface or copy.chapter_intros or copy.back_cover_quote:
        return copy
    return None


async def generate_manuscript_editorial_copy(
    *,
    title: str,
    snapshot: list[dict[str, object]],
    ai_model: str | None,
    ai_client: AITextClient | None = None,
) -> GeneratedManuscriptCopy | None:
    prompt = build_manuscript_copy_prompt(title=title, snapshot=snapshot)
    client = ai_client
    if client is None and not _text_ai_configured(ai_model):
        return None
    try:
        if client is None:
            client = get_ai_service(ai_model)
        content = await client._call_llm(
            SYSTEM_PROMPT,
            prompt,
            temperature=0.35,
            max_tokens=1800,
            timeout=90.0,
        )
    except HTTPException as exc:
        if exc.status_code < 500:
            raise
        logger.exception("AI memoir manuscript copy failed provider=%s", getattr(client, "provider_name", "unknown"))
        return None
    except Exception:
        logger.exception("AI memoir manuscript copy failed provider=%s", getattr(client, "provider_name", "unknown"))
        return None

    payload = extract_json_payload(str(content))
    return _copy_from_payload(
        payload,
        prompt=prompt,
        model=getattr(client, "model_name", ai_model),
    )


async def generate_title_suggestions(
    *,
    title: str,
    snapshot: list[dict[str, object]],
    count: int,
    ai_model: str | None,
    ai_client: AITextClient | None = None,
) -> GeneratedTitleSuggestions | None:
    prompt = build_title_suggestion_prompt(title=title, snapshot=snapshot, count=count)
    client = ai_client
    if client is None and not _text_ai_configured(ai_model):
        return None
    try:
        if client is None:
            client = get_ai_service(ai_model)
        content = await client._call_llm(
            SYSTEM_PROMPT,
            prompt,
            temperature=0.55,
            max_tokens=800,
            timeout=60.0,
        )
    except HTTPException as exc:
        if exc.status_code < 500:
            raise
        logger.exception("AI memoir title suggestions failed provider=%s", getattr(client, "provider_name", "unknown"))
        return None
    except Exception:
        logger.exception("AI memoir title suggestions failed provider=%s", getattr(client, "provider_name", "unknown"))
        return None

    payload = extract_json_payload(str(content))
    raw_suggestions = payload.get("suggestions") if isinstance(payload, dict) else []
    suggestions: list[str] = []
    if isinstance(raw_suggestions, list):
        for item in raw_suggestions:
            suggestion = _clean_title(item)
            if suggestion and suggestion not in suggestions:
                suggestions.append(suggestion)
    if not suggestions:
        return None
    return GeneratedTitleSuggestions(
        suggestions=suggestions[:count],
        model=getattr(client, "model_name", ai_model),
        prompt=prompt,
    )
