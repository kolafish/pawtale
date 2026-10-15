from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.memoir.types import MemoirNarrationMode
from app.services.ai_calls import generate_structured_with_client
from app.services.ai_factory import get_ai_service
from app.services.ai_timeouts import ai_text_timeout_seconds
from app.services.ai_types import AITextClient
from app.services.ai_workloads import AITextWorkload, ai_text_workload_policy
from app.services.asr.streaming import StreamingASRClient, StreamingASREvent
from app.services.asr.billing import record_volc_streaming_asr_usage


MAX_VOICE_SEARCH_AUDIO_BYTES = 2 * 1024 * 1024
ASR_SEARCH_DRAIN_TIMEOUT_SECONDS = 10.0
StreamingASRClientFactory = Callable[[], StreamingASRClient]
logger = logging.getLogger(__name__)


VOICE_SEARCH_SYSTEM_PROMPT = (
    "你是回忆录讲述记录搜索助手。你的任务是把用户口语化的中文搜索需求解析成检索关键词和可选筛选条件。"
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
    "narration_mode": "ai_interview | human_interview | book_completion | null",
    "module": "person | event | place | period | theme | family | work | chat | null"
  }}
}}

规则：
- 只保留原句里明确表达的人名、地点、事件、时间、主题、物件或关键词，不要发散联想。
- 如果用户说“已收录/写进书/章节里的”，included 用 included；如果说“未收录/没写进书/还没整理”，included 用 not_included；否则 all。
- 明确筛选 AI 访谈、自由讲述、自由叙述、引导访谈、主题访谈时，narration_mode 用 ai_interview（包含新旧普通 AI 访谈）；真人访谈用 human_interview；书稿补充用 book_completion；未指定访谈方式用 null。
- “自由”“引导”等故事中的普通关键词不是访谈方式，保留为 query，不要误设 narration_mode。访谈方式词只放筛选，不重复放入 query/keywords；“主题访谈”不代表 module=theme。
- 如果用户明确说人物/事情/地点/时间段/主题/家庭/工作/闲聊，只在 module 中填对应英文；否则 null。
- 没有故事关键词、只有筛选条件时，query 用空字符串，keywords 用空数组。否则使用原句去掉命令词和筛选词后的核心短语。
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

NARRATION_ALIASES = {
    "ai_interview": "ai_interview",
    "AI 访谈": "ai_interview",
    "AI访谈": "ai_interview",
    "自由讲述": "ai_interview",
    "自由叙述": "ai_interview",
    "引导访谈": "ai_interview",
    "主题访谈": "ai_interview",
    "human_interview": "human_interview",
    "真人访谈": "human_interview",
    "book_completion": "book_completion",
    "书稿补充": "book_completion",
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
    narration_mode: MemoirNarrationMode | None = None
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


class _VoiceSearchFiltersOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    included: str = "all"
    narration_mode: MemoirNarrationMode | None = None
    module: str | None = None


class _VoiceSearchOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = ""
    keywords: list[str] = Field(default_factory=list)
    filters: _VoiceSearchFiltersOutput = Field(
        default_factory=_VoiceSearchFiltersOutput
    )


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
    mime_type: str = "audio/pcm",
) -> str:
    if not audio:
        raise VoiceSearchTranscriptionError("voice_search_audio_empty", "请先录一段搜索语音", 400)
    if len(audio) > MAX_VOICE_SEARCH_AUDIO_BYTES:
        raise VoiceSearchTranscriptionError("voice_search_audio_too_large", "搜索语音太长，请控制在 10 秒内", 413)

    asr_client = asr_client_factory()
    final_texts: list[str] = []
    latest_text = ""
    collect_task: asyncio.Task[None] | None = None
    status = "failed"
    error_type: str | None = None
    audio_sent = False
    normalized_mime_type = mime_type.split(";", 1)[0].strip().lower()
    audio_duration_seconds = (
        len(audio) / (16_000 * 2)
        if normalized_mime_type in {"audio/pcm", "audio/x-pcm"}
        else 0
    )

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
        audio_sent = True
        await asr_client.finish()
        await asyncio.wait_for(asyncio.shield(collect_task), timeout=ASR_SEARCH_DRAIN_TIMEOUT_SECONDS)
        transcript = (final_texts[-1] if final_texts else latest_text).strip()
        if not transcript:
            raise VoiceSearchTranscriptionError("asr_empty_transcript", "没有识别到搜索内容，请换个说法再试", 422)
        status = "succeeded"
    except VoiceSearchTranscriptionError as exc:
        error_type = exc.code
        raise
    except asyncio.TimeoutError as exc:
        error_type = type(exc).__name__
        raise VoiceSearchTranscriptionError("asr_finish_timeout", "语音识别超时，请重试", 504) from exc
    except Exception as exc:
        error_type = type(exc).__name__
        raise VoiceSearchTranscriptionError(str(exc) or "asr_provider_error", "语音识别服务暂时不可用", 502) from exc
    finally:
        if collect_task is not None and not collect_task.done():
            collect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await collect_task
        with contextlib.suppress(Exception):
            await asr_client.close()
        await record_volc_streaming_asr_usage(
            audio_duration_seconds=audio_duration_seconds,
            status=status,
            error_type=error_type,
            billable=audio_sent,
        )

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
        policy = ai_text_workload_policy(AITextWorkload.INTERACTIVE_STRUCTURED)
        generated = await generate_structured_with_client(
            client,
            VOICE_SEARCH_SYSTEM_PROMPT,
            VOICE_SEARCH_USER_PROMPT_TEMPLATE.format(text=original_text),
            output_model=_VoiceSearchOutput,
            requested_model=ai_model,
            temperature=0.1,
            max_tokens=policy.max_tokens,
            timeout=ai_text_timeout_seconds(policy.timeout_profile),
            max_attempts=policy.max_attempts,
        )
        payload = generated.value.model_dump()
        parsed = _parsed_voice_search_payload(original_text, payload)
        return ParsedVoiceSearchQuery(
            original_text=original_text,
            query=parsed.query,
            keywords=parsed.keywords,
            filters=parsed.filters,
            display_text=parsed.display_text,
            used_ai=True,
            provider=generated.response.provider,
            model=generated.response.resolved_model,
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
        narration_mode=filters_payload.get("narration_mode") or fallback.filters.narration_mode,
        module=_normalized_module(filters_payload.get("module")) or fallback.filters.module,
    )
    # Providers sometimes echo mode names and the generic records suffix.
    # Do not strip command substrings inside story keywords such as 寻找自由.
    query = _remove_filter_terms(re.sub(r"(?:转写|语音)?记录$", "", query), filters)
    keywords = _normalized_keywords([
        _remove_filter_terms(re.sub(r"(?:转写|语音)?记录$", "", keyword), filters)
        for keyword in keywords
    ])
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
    narration_mode = _scan_alias(text, NARRATION_ALIASES)
    mode_free_text = _remove_alias_group(text, NARRATION_ALIASES) if narration_mode else text
    filters = VoiceSearchQueryFilters(
        included=_scan_alias(text, INCLUDED_ALIASES) or "all",
        narration_mode=MemoirNarrationMode(narration_mode) if narration_mode else None,
        module=_scan_alias(mode_free_text, MODULE_ALIASES),
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
    if filters.narration_mode:
        cleaned = _remove_alias_group(cleaned, NARRATION_ALIASES)
    if filters.module:
        cleaned = _remove_alias_group(cleaned, MODULE_ALIASES)
    cleaned = cleaned.replace("相关", " ")
    cleaned = re.sub(r"(^|[\s,，。；;、])的", " ", cleaned)
    return _normalized_query(cleaned)


def _remove_alias_group(text: str, aliases: dict[str, str]) -> str:
    cleaned = text
    for alias in sorted(aliases, key=len, reverse=True):
        if re.fullmatch(r"[a-z_]+", alias):
            continue
        cleaned = re.sub(re.escape(alias), " ", cleaned, flags=re.IGNORECASE)
    return cleaned


def _voice_search_filters_active(filters: VoiceSearchQueryFilters) -> bool:
    return filters.included != "all" or bool(filters.narration_mode) or bool(filters.module)


def _normalized_included_filter(value: Any) -> str | None:
    normalized = _scan_alias(str(value), INCLUDED_ALIASES) if value is not None else None
    if normalized in {"all", "included", "not_included"}:
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
        if alias.lower() == lowered or alias.lower() in lowered:
            return value
    return None


def _voice_search_display_text(query: str, filters: VoiceSearchQueryFilters) -> str:
    parts = [query] if query else []
    if filters.included == "included":
        parts.append("已收录")
    elif filters.included == "not_included":
        parts.append("未收录")
    if filters.narration_mode:
        parts.append({
            "ai_interview": "AI 访谈",
            "human_interview": "真人访谈",
            "book_completion": "书稿补充",
        }[filters.narration_mode])
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
