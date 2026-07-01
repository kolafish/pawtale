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
    pull_quote: str = ""
    pull_quote_source_ref_id: str = ""


@dataclass(frozen=True)
class GeneratedManuscriptCopy:
    book_title: str
    cover_subtitle: str
    preface: str
    afterword: str
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


def _string_list(value: object, *, max_items: int, max_chars: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = _compact_text(_string(item), max_chars)
        if text and text not in result:
            result.append(text)
        if len(result) >= max_items:
            break
    return result


def _chapter_summary(item: dict[str, object]) -> str:
    metadata = item.get("metadata_json")
    summary = _string(item.get("chapter_summary")) or _string(item.get("summary"))
    if not summary and isinstance(metadata, dict):
        summary = (
            _string(metadata.get("chapter_summary"))
            or _string(metadata.get("summary"))
            or _string(metadata.get("editorial_summary"))
        )
    return _compact_text(summary, 240)


def _source_summaries(item: dict[str, object]) -> list[str]:
    summaries = _string_list(item.get("source_summaries"), max_items=4, max_chars=120)
    if summaries:
        return summaries
    raw_refs = item.get("source_refs")
    if not isinstance(raw_refs, list):
        return []
    result: list[str] = []
    for raw_ref in raw_refs:
        if not isinstance(raw_ref, dict):
            continue
        summary = (
            _string(raw_ref.get("summary"))
            or _string(raw_ref.get("source_summary"))
            or _string(raw_ref.get("record_summary"))
        )
        summary = _compact_text(summary, 120)
        if summary and summary not in result:
            result.append(summary)
        if len(result) >= 4:
            break
    return result


def _representative_quotes(item: dict[str, object], content: str) -> list[str]:
    quotes = _string_list(item.get("representative_quotes"), max_items=5, max_chars=90)
    raw_refs = item.get("source_refs")
    if isinstance(raw_refs, list):
        for raw_ref in raw_refs:
            if not isinstance(raw_ref, dict):
                continue
            quote = (
                _string(raw_ref.get("representative_quote"))
                or _string(raw_ref.get("quote"))
            )
            quote = _compact_text(quote, 90)
            if quote and quote not in quotes:
                quotes.append(quote)
            if len(quotes) >= 5:
                break
    if not quotes:
        fallback = _compact_text(content, 90)
        if fallback:
            quotes.append(fallback)
    return quotes[:5]


def _source_quote_lines(raw_refs: object) -> list[str]:
    if not isinstance(raw_refs, list):
        return []
    lines: list[str] = []
    for index, raw_ref in enumerate(raw_refs, start=1):
        if not isinstance(raw_ref, dict):
            continue
        utterance_id = _string(raw_ref.get("utterance_id"))
        quote = _compact_text(
            _string(raw_ref.get("representative_quote")) or _string(raw_ref.get("quote")),
            90,
        )
        if not utterance_id or not quote:
            continue
        lines.append(f"- source_ref_id: {utterance_id}; quote: {quote}")
        if len(lines) >= 6:
            break
    return lines


def _chapter_context(snapshot: list[dict[str, object]]) -> str:
    parts: list[str] = []
    for fallback_index, item in enumerate(snapshot, start=1):
        chapter_id = _string(item.get("chapter_id"))
        title = _string(item.get("chapter_title")) or _string(item.get("title")) or f"第{fallback_index}章"
        content = _string(item.get("content"))
        if not chapter_id or not content:
            continue
        source_quote_lines = _source_quote_lines(item.get("source_refs"))
        parts.append(
            "\n".join(
                [
                    f"chapter_id: {chapter_id}",
                    f"chapter_index: {_int(item.get('chapter_index')) or fallback_index}",
                    f"title: {title}",
                    f"source_count: {_int(item.get('source_count'))}",
                    f"chapter_summary: {_chapter_summary(item) or '无'}",
                    f"source_summaries: {' / '.join(_source_summaries(item)) or '无'}",
                    f"representative_quotes: {' / '.join(_representative_quotes(item, content))}",
                    "original_quote_candidates:",
                    *(source_quote_lines or ["- 无"]),
                    f"content_excerpt: {_compact_text(content, 420)}",
                ]
            )
        )
    return "\n\n".join(parts)


def build_manuscript_copy_prompt(
    *,
    title: str,
    snapshot: list[dict[str, object]],
    targets: list[str] | None = None,
    chapter_ids: list[str] | None = None,
    feedback: str | None = None,
) -> str:
    chapters = _chapter_context(snapshot)
    target_text = "、".join(targets or []) or "全部成书文案"
    chapter_text = "、".join(chapter_ids or []) or "全部章节"
    feedback_text = f"\n用户本次反馈：{feedback.strip()}" if feedback and feedback.strip() else ""
    return f"""请根据下面已经确认的回忆录章节，生成适合 PDF 成书的前置文案。

当前书名：{title}
本次生成目标：{target_text}
本次重点章节：{chapter_text}{feedback_text}

请输出严格 JSON，不要 Markdown，不要解释。格式：
{{
  "book_title": "正式书名",
  "cover_subtitle": "封面短副标题",
  "preface": "前言正文",
  "afterword": "后记正文",
  "chapter_intros": [
    {{
      "chapter_id": "对应 chapter_id",
      "intro": "章节导语",
      "pull_quote": "从本章 original_quote_candidates 中选择的原始口述句",
      "pull_quote_source_ref_id": "对应 source_ref_id"
    }}
  ],
  "back_cover_quote": "封底短句"
}}

要求：
- 只能基于给定章节标题、章节摘要、素材摘要、代表性口述和正文摘录，不新增未出现的人物、地点、事件或评价。
- book_title 要像家人会保存的一本书名，避免“主题回忆录”“章节规划”等内部词；如果原书名已经自然，可以保留。
- cover_subtitle 控制在 12 到 28 个汉字，可概括内容；素材不足时用“根据口述访谈整理而成”。
- preface 控制在 120 到 220 个汉字，语气安静、可信、像书，不要直接套书名或项目名，不要出现“主题回忆录”“这本书记录的是「书名」”“后台”“系统”“AI生成”等模板词。
- afterword 控制在 120 到 260 个汉字，偏家人保存、后续补充和记忆延续，不要写空泛祝福语，不要像模板。
- 每个 chapter_intro 控制在 35 到 90 个汉字，像书籍章节导语，用具体画面把读者带进正文；不要解释章节功能，不要写“这一章主要记录”“从口述内容看”“相关的回忆”“本章讲述”等系统摘要句。
- 每个 pull_quote 必须逐字来自对应章节 original_quote_candidates 的 quote，可只取其中一个完整短句；不要把摘要改写成原话，不要编金句。
- pull_quote_source_ref_id 必须来自对应章节 original_quote_candidates 的 source_ref_id。
- back_cover_quote 控制在 15 到 45 个汉字，优先从全书 original_quote_candidates 中选择真实短句；不要用公共格言、励志句或“凡是过往皆是序章”这类非本人原话。

章节材料：
{chapters}
"""


def build_title_suggestion_prompt(*, title: str, snapshot: list[dict[str, object]], count: int) -> str:
    chapters = _chapter_context(snapshot)
    return f"""请根据下面已经确认的回忆录章节，为这本家庭回忆录拟 {count} 个候选主书名。

当前暂定书名：{title}

请输出严格 JSON，不要 Markdown，不要解释。格式：
{{
  "suggestions": ["书名一", "书名二"]
}}

要求：
- 只能基于给定章节标题、代表性口述和正文摘录，不新增未出现的人物、地点或事件。
- 这是整本书的主标题，要有“一个人一生回忆”的全局感，像家人会保存、下载、转发和打印的一本书。
- 不要只抓某个场景、物件、单章细节或一句话作为主书名；除非这个细节能明显象征整个人生，否则不要用。
- 避免类似“坐在院子里看月亮”“从葡萄籽到葡萄熟”“外婆摸着进厨房”“升职加薪那些年”“我家那个院子”这类过窄标题。
- 可以包含朴素正式型、温和口语型、略有书感型，但都要克制，例如“外婆的一生”“外婆慢慢说”“走过的这些年”“一生的来路”“某某口述回忆录”。
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
    forbidden = (
        "后台",
        "系统",
        "AI生成",
        "AI 生成",
        "主题回忆录",
        "章节规划",
        "这一章主要记录",
        "从口述内容看",
        "相关的回忆",
        "本章讲述",
        "凡是过往皆是序章",
        "这本书还可以继续补充",
        "愿常翻看",
    )
    if any(term in text for term in forbidden):
        return ""
    return text


def _clean_title(value: object) -> str:
    title = _compact_text(_string(value), 32).strip("《》「」“”\"' ")
    forbidden = ("主题回忆录", "章节规划", "AI", "生成", "系统", "后台")
    if not title or any(term in title for term in forbidden):
        return ""
    return title[:32]


def _quote_candidates_by_chapter(snapshot: list[dict[str, object]]) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for item in snapshot:
        chapter_id = _string(item.get("chapter_id"))
        if not chapter_id:
            continue
        candidates: dict[str, str] = {}
        raw_refs = item.get("source_refs")
        if isinstance(raw_refs, list):
            for raw_ref in raw_refs:
                if not isinstance(raw_ref, dict):
                    continue
                utterance_id = _string(raw_ref.get("utterance_id"))
                quote = (
                    _string(raw_ref.get("representative_quote"))
                    or _string(raw_ref.get("quote"))
                )
                if utterance_id and quote:
                    candidates[utterance_id] = _compact_text(quote, 90)
        result[chapter_id] = candidates
    return result


def _validated_original_quote(
    *,
    chapter_id: str,
    value: object,
    source_ref_id: object,
    quote_candidates: dict[str, dict[str, str]],
) -> tuple[str, str]:
    quote = _clean_copy_text(value, max_chars=90).strip("“”\"'「」")
    candidate_by_source = quote_candidates.get(chapter_id, {})
    source_id = _string(source_ref_id)
    if source_id and source_id in candidate_by_source:
        source_quote = candidate_by_source[source_id]
        if not quote:
            return source_quote, source_id
        if quote in source_quote:
            return quote, source_id
        if source_quote in quote and len(source_quote) >= 8:
            return source_quote, source_id
        return "", ""
    for candidate_source_id, source_quote in candidate_by_source.items():
        if quote and quote in source_quote:
            return quote, candidate_source_id
        if quote and source_quote in quote and len(source_quote) >= 8:
            return source_quote, candidate_source_id
    return "", ""


def _validated_back_cover_quote(value: object, quote_candidates: dict[str, dict[str, str]]) -> str:
    quote = _clean_copy_text(value, max_chars=60).strip("“”\"'「」")
    if not quote:
        return ""
    for candidate_by_source in quote_candidates.values():
        for source_quote in candidate_by_source.values():
            if quote in source_quote:
                return quote
            if source_quote in quote and len(source_quote) >= 8:
                return source_quote
    return ""


def _copy_from_payload(
    payload: Any,
    *,
    snapshot: list[dict[str, object]],
    prompt: str,
    model: str | None,
) -> GeneratedManuscriptCopy | None:
    if not isinstance(payload, dict):
        return None
    quote_candidates = _quote_candidates_by_chapter(snapshot)
    raw_intros = payload.get("chapter_intros")
    chapter_intros: list[GeneratedChapterIntro] = []
    if isinstance(raw_intros, list):
        for raw_intro in raw_intros:
            if not isinstance(raw_intro, dict):
                continue
            chapter_id = _string(raw_intro.get("chapter_id"))
            intro = _clean_copy_text(raw_intro.get("intro"), max_chars=120)
            pull_quote, pull_quote_source_ref_id = _validated_original_quote(
                chapter_id=chapter_id,
                value=raw_intro.get("pull_quote"),
                source_ref_id=raw_intro.get("pull_quote_source_ref_id"),
                quote_candidates=quote_candidates,
            )
            if chapter_id and (intro or pull_quote):
                chapter_intros.append(
                    GeneratedChapterIntro(
                        chapter_id=chapter_id,
                        intro=intro,
                        pull_quote=pull_quote,
                        pull_quote_source_ref_id=pull_quote_source_ref_id,
                    )
                )
    copy = GeneratedManuscriptCopy(
        book_title=_clean_copy_text(payload.get("book_title"), max_chars=32),
        cover_subtitle=_clean_copy_text(payload.get("cover_subtitle"), max_chars=40),
        preface=_clean_copy_text(payload.get("preface"), max_chars=260),
        afterword=_clean_copy_text(payload.get("afterword"), max_chars=320),
        chapter_intros=chapter_intros,
        back_cover_quote=_validated_back_cover_quote(payload.get("back_cover_quote"), quote_candidates),
        model=model,
        prompt=prompt,
    )
    if (
        copy.book_title
        or copy.cover_subtitle
        or copy.preface
        or copy.afterword
        or copy.chapter_intros
        or copy.back_cover_quote
    ):
        return copy
    return None


async def generate_manuscript_editorial_copy(
    *,
    title: str,
    snapshot: list[dict[str, object]],
    ai_model: str | None,
    targets: list[str] | None = None,
    chapter_ids: list[str] | None = None,
    feedback: str | None = None,
    ai_client: AITextClient | None = None,
) -> GeneratedManuscriptCopy | None:
    prompt = build_manuscript_copy_prompt(
        title=title,
        snapshot=snapshot,
        targets=targets,
        chapter_ids=chapter_ids,
        feedback=feedback,
    )
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
        snapshot=snapshot,
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
