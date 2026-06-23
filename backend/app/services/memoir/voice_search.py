from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.services import get_ai_service
from app.services.ai_types import AITextClient
from app.services.json_utils import extract_json_payload
from app.services.memoir.asr import StreamingASRClient, StreamingASREvent


MAX_VOICE_SEARCH_AUDIO_BYTES = 2 * 1024 * 1024
ASR_SEARCH_DRAIN_TIMEOUT_SECONDS = 10.0
StreamingASRClientFactory = Callable[[], StreamingASRClient]
logger = logging.getLogger(__name__)


VOICE_SEARCH_SYSTEM_PROMPT = (
    "你是回忆录转写记录搜索助手。你的任务是把用户口语化的中文搜索需求解析成检索关键词和可选筛选条件。"
    "只输出 JSON，不要解释。"
)

VOICE_SEARCH_USER_PROMPT_TEMPLATE = """请解析下面这句搜索需求，输出严格 JSON：

用户原句：
{text}

JSON 格式：
{{
  "query": "用于全文搜索的关键词，多个词用空格分开，不要包含帮我、搜索、查找、看看等命令词",
  "keywords": ["关键词1", "关键词2"],
  "filters": {{
    "included": "all | included | not_included",
    "guidance_mode": "free_narration | guided_interview | null",
    "module": "person | event | place | period | theme | family | work | chat | null"
  }}
}}

规则：
- 只保留原句里明确表达的人名、地点、事件、时间、主题、物件或关键词，不要发散联想。
- 如果用户说“已收录/写进书/章节里的”，included 用 included；如果说“未收录/没写进书/还没整理”，included 用 not_included；否则 all。
- 如果用户明确说自由讲述，guidance_mode 用 free_narration；明确说引导访谈、主题访谈，guidance_mode 用 guided_interview；否则 null。
- 如果用户明确说人物/事情/地点/时间段/主题/家庭/工作/闲聊，只在 module 中填对应英文；否则 null。
- 如果无法提取关键词，query 使用原句去掉命令词后的核心短语。
"""

COMMAND_WORD_RE = re.compile(
    r"(帮我|给我|麻烦|请|想|要)?(搜索|搜|查找|查一下|找一下|找找|看看|看一下|筛一下|筛选|过滤)"
    r"|帮我找|给我找|找|相关(的)?(记录|内容|转写)?|转写记录|语音记录|记录|内容|里面|一下|那些|关于|有关"
)
SPLIT_WORD_RE = re.compile(r"[\s,，。；;、？！?]+")

INCLUDED_ALIASES = {
    "included": "included",
    "已收录": "included",
    "收录": "included",
    "写进书": "included",
    "章节": "included",
    "not_included": "not_included",
    "未收录": "not_included",
    "没收录": "not_included",
    "没写进书": "not_included",
    "还没整理": "not_included",
    "all": "all",
    "全部": "all",
}

GUIDANCE_ALIASES = {
    "free_narration": "free_narration",
    "自由讲述": "free_narration",
    "自由叙述": "free_narration",
    "guided_interview": "guided_interview",
    "引导访谈": "guided_interview",
    "主题访谈": "guided_interview",
    "强引导": "guided_interview",
}

MODULE_ALIASES = {
    "person": "person",
    "人物": "person",
    "event": "event",
    "事情": "event",
    "事件": "event",
    "place": "place",
    "地点": "place",
    "地方": "place",
    "period": "period",
    "时间段": "period",
    "年代": "period",
    "theme": "theme",
    "主题": "theme",
    "family": "family",
    "家庭": "family",
    "家人": "family",
    "work": "work",
    "工作": "work",
    "chat": "chat",
    "闲聊": "chat",
}


@dataclass(frozen=True)
class VoiceSearchQueryFilters:
    included: str = "all"
    guidance_mode: str | None = None
    module: str | None = None


@dataclass(frozen=True)
class ParsedVoiceSearchQuery:
    original_text: str
    query: str
    keywords: list[str]
    filters: VoiceSearchQueryFilters
    display_text: str
    used_ai: bool = False
    provider: str | None = None
    model: str | None = None


class VoiceSearchTranscriptionError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


async def transcribe_voice_search_audio(
    audio: bytes,
    *,
    asr_client_factory: StreamingASRClientFactory,
) -> str:
    if not audio:
        raise VoiceSearchTranscriptionError("voice_search_audio_empty", "请先录一段搜索语音", 400)
    if len(audio) > MAX_VOICE_SEARCH_AUDIO_BYTES:
        raise VoiceSearchTranscriptionError("voice_search_audio_too_large", "搜索语音太长，请控制在 10 秒内", 413)

    asr_client = asr_client_factory()
    final_texts: list[str] = []
    latest_text = ""
    collect_task: asyncio.Task[None] | None = None

    async def collect_events() -> None:
        nonlocal latest_text
        async for event in asr_client.events():
            text = _event_text(event)
            if text:
                latest_text = text
            if event.type == "final" and text:
                final_texts.append(text)
            if event.type == "error":
                raise VoiceSearchTranscriptionError(
                    event.error or "asr_provider_error",
                    "语音识别服务暂时不可用",
                    502,
                )

    try:
        await asr_client.start()
        collect_task = asyncio.create_task(collect_events())
        await asr_client.send_audio(audio)
        await asr_client.finish()
        await asyncio.wait_for(asyncio.shield(collect_task), timeout=ASR_SEARCH_DRAIN_TIMEOUT_SECONDS)
    except VoiceSearchTranscriptionError:
        raise
    except asyncio.TimeoutError as exc:
        raise VoiceSearchTranscriptionError("asr_finish_timeout", "语音识别超时，请重试", 504) from exc
    except Exception as exc:
        raise VoiceSearchTranscriptionError(str(exc) or "asr_provider_error", "语音识别服务暂时不可用", 502) from exc
    finally:
        if collect_task is not None and not collect_task.done():
            collect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await collect_task
        with contextlib.suppress(Exception):
            await asr_client.close()

    transcript = (final_texts[-1] if final_texts else latest_text).strip()
    if not transcript:
        raise VoiceSearchTranscriptionError("asr_empty_transcript", "没有识别到搜索内容，请换个说法再试", 422)
    return transcript


def _event_text(event: StreamingASREvent) -> str:
    return event.text.strip()


async def parse_voice_search_query(
    text: str,
    *,
    ai_model: str | None = None,
    ai_client: AITextClient | None = None,
) -> ParsedVoiceSearchQuery:
    original_text = text.strip()
    if not original_text:
        return _fallback_parse_voice_search_query("")

    client = ai_client
    try:
        if client is None:
            client = get_ai_service(ai_model)
        content = await client._call_llm(
            VOICE_SEARCH_SYSTEM_PROMPT,
            VOICE_SEARCH_USER_PROMPT_TEMPLATE.format(text=original_text),
            temperature=0.1,
            max_tokens=500,
            timeout=45.0,
        )
        payload = extract_json_payload(str(content))
        parsed = _parsed_voice_search_payload(original_text, payload)
        used_ai = isinstance(payload, dict)
        return ParsedVoiceSearchQuery(
            original_text=original_text,
            query=parsed.query,
            keywords=parsed.keywords,
            filters=parsed.filters,
            display_text=parsed.display_text,
            used_ai=used_ai,
            provider=getattr(client, "provider_name", None) if used_ai else None,
            model=getattr(client, "model_name", ai_model) if used_ai else None,
        )
    except Exception:
        logger.warning(
            "AI voice search query parse failed provider=%s model=%s; using fallback",
            getattr(client, "provider_name", "unknown"),
            getattr(client, "model_name", ai_model or "default"),
            exc_info=True,
        )
        return _fallback_parse_voice_search_query(original_text)


def _parsed_voice_search_payload(original_text: str, payload: Any) -> ParsedVoiceSearchQuery:
    fallback = _fallback_parse_voice_search_query(original_text)
    if not isinstance(payload, dict):
        return fallback
    filters_payload = payload.get("filters")
    filters_payload = filters_payload if isinstance(filters_payload, dict) else {}
    keywords = _normalized_keywords(payload.get("keywords"))
    query = _normalized_query(payload.get("query")) or " ".join(keywords) or fallback.query
    filters = VoiceSearchQueryFilters(
        included=_normalized_included_filter(filters_payload.get("included")) or fallback.filters.included,
        guidance_mode=_normalized_guidance_mode(filters_payload.get("guidance_mode")) or fallback.filters.guidance_mode,
        module=_normalized_module(filters_payload.get("module")) or fallback.filters.module,
    )
    if not keywords:
        keywords = _fallback_keywords(query)
    return ParsedVoiceSearchQuery(
        original_text=original_text,
        query=query,
        keywords=keywords[:8],
        filters=filters,
        display_text=_voice_search_display_text(query, filters),
        used_ai=False,
    )


def _fallback_parse_voice_search_query(text: str) -> ParsedVoiceSearchQuery:
    filters = VoiceSearchQueryFilters(
        included=_scan_alias(text, INCLUDED_ALIASES) or "all",
        guidance_mode=_scan_alias(text, GUIDANCE_ALIASES),
        module=_scan_alias(text, MODULE_ALIASES),
    )
    cleaned = _remove_filter_terms(COMMAND_WORD_RE.sub(" ", text), filters)
    query = cleaned or ("" if _voice_search_filters_active(filters) else text.strip())
    keywords = _fallback_keywords(query)
    return ParsedVoiceSearchQuery(
        original_text=text,
        query=query,
        keywords=keywords,
        filters=filters,
        display_text=_voice_search_display_text(query, filters),
        used_ai=False,
    )


def _normalized_keywords(value: Any) -> list[str]:
    if isinstance(value, str):
        candidates = SPLIT_WORD_RE.split(value)
    elif isinstance(value, list):
        candidates = [str(item) for item in value if isinstance(item, str | int | float)]
    else:
        candidates = []
    normalized: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        keyword = _normalized_query(candidate)
        if not keyword or keyword in seen:
            continue
        seen.add(keyword)
        normalized.append(keyword)
        if len(normalized) >= 8:
            break
    return normalized


def _fallback_keywords(query: str) -> list[str]:
    keywords = _normalized_keywords(SPLIT_WORD_RE.split(query))
    if keywords:
        return keywords
    return [query] if query else []


def _normalized_query(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = SPLIT_WORD_RE.sub(" ", value).strip(" ，,、")
    return re.sub(r"\s+", " ", text).strip()[:120]


def _remove_filter_terms(text: str, filters: VoiceSearchQueryFilters) -> str:
    cleaned = text
    if filters.included != "all":
        cleaned = _remove_alias_group(cleaned, INCLUDED_ALIASES)
    if filters.guidance_mode:
        cleaned = _remove_alias_group(cleaned, GUIDANCE_ALIASES)
    if filters.module:
        cleaned = _remove_alias_group(cleaned, MODULE_ALIASES)
    cleaned = cleaned.replace("相关", " ")
    cleaned = re.sub(r"(^|[\s,，。；;、])的(?=\S)", " ", cleaned)
    return _normalized_query(cleaned)


def _remove_alias_group(text: str, aliases: dict[str, str]) -> str:
    cleaned = text
    for alias in sorted(aliases, key=len, reverse=True):
        if re.fullmatch(r"[a-z_]+", alias):
            continue
        cleaned = cleaned.replace(alias, " ")
    return cleaned


def _voice_search_filters_active(filters: VoiceSearchQueryFilters) -> bool:
    return filters.included != "all" or bool(filters.guidance_mode) or bool(filters.module)


def _normalized_included_filter(value: Any) -> str | None:
    normalized = _scan_alias(str(value), INCLUDED_ALIASES) if value is not None else None
    if normalized in {"all", "included", "not_included"}:
        return normalized
    return None


def _normalized_guidance_mode(value: Any) -> str | None:
    normalized = _scan_alias(str(value), GUIDANCE_ALIASES) if value is not None else None
    if normalized in {"free_narration", "guided_interview"}:
        return normalized
    return None


def _normalized_module(value: Any) -> str | None:
    normalized = _scan_alias(str(value), MODULE_ALIASES) if value is not None else None
    if normalized:
        return normalized[:80]
    return None


def _scan_alias(text: str, aliases: dict[str, str]) -> str | None:
    normalized = text.strip()
    if not normalized:
        return None
    lowered = normalized.lower()
    for alias, value in sorted(aliases.items(), key=lambda item: len(item[0]), reverse=True):
        if alias.lower() == lowered or alias in normalized:
            return value
    return None


def _voice_search_display_text(query: str, filters: VoiceSearchQueryFilters) -> str:
    parts = [query] if query else []
    if filters.included == "included":
        parts.append("已收录")
    elif filters.included == "not_included":
        parts.append("未收录")
    if filters.guidance_mode == "free_narration":
        parts.append("自由讲述")
    elif filters.guidance_mode == "guided_interview":
        parts.append("引导访谈")
    if filters.module:
        parts.append(_module_label(filters.module))
    return " · ".join(parts) or "全部记录"


def _module_label(module: str) -> str:
    labels = {
        "person": "人物",
        "event": "事情",
        "place": "地点",
        "period": "时间段",
        "theme": "主题",
        "family": "家庭",
        "work": "工作",
        "chat": "闲聊",
    }
    return labels.get(module, module)
