from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config import get_settings
from app.domain.memoir.types import MemoirTranscriptPolishLevel
from app.services.ai_calls import AIStructuredOutputError, generate_structured_with_client
from app.services.ai_factory import (
    DEEPSEEK_FLASH_ALIASES,
    DEEPSEEK_V4_PRO_MODEL,
    VOLC_DOUBAO_PRO_MODEL,
    get_ai_service,
)
from app.services.ai_references import ModelReferenceMap
from app.services.ai_timeouts import AIProviderTimeoutError, ai_text_timeout_seconds
from app.services.ai_types import AITextClient
from app.services.ai_workloads import AITextWorkload, ai_text_workload_policy
from app.services.memoir.book_manuscript_cleanup import original_quote_candidates, body_excerpt_candidates, matched_quote


SYSTEM_PROMPT = "你是一位谨慎的中文家庭回忆录编辑，只把已有口述材料整理成适合成书阅读的前置文案。"
logger = logging.getLogger(__name__)


class _ChapterIntroOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_id: str = ""
    intro: str = ""
    pull_quote: str = ""

    @model_validator(mode="before")
    @classmethod
    def discard_obsolete_source_ref(cls, value: Any) -> Any:
        # Polished copy uses excerpts. Ignore only this obsolete model habit;
        # LOW still declares and validates the source reference normally.
        if isinstance(value, dict) and "pull_quote_source_ref_id" not in cls.model_fields:
            return {key: item for key, item in value.items() if key != "pull_quote_source_ref_id"}
        return value


class _OriginalChapterIntroOutput(_ChapterIntroOutput):
    pull_quote_source_ref_id: str = ""


class _ManuscriptCopyOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    book_title: str = ""
    cover_subtitle: str = ""
    preface: str = ""
    afterword: str = ""
    chapter_intros: list[_ChapterIntroOutput] = Field(default_factory=list)
    back_cover_quote: str = ""

    @model_validator(mode="after")
    def require_copy(self) -> "_ManuscriptCopyOutput":
        if not (
            self.book_title
            or self.cover_subtitle
            or self.preface
            or self.afterword
            or self.chapter_intros
            or self.back_cover_quote
        ):
            raise ValueError("manuscript copy is empty")
        return self


class _OriginalManuscriptCopyOutput(_ManuscriptCopyOutput):
    chapter_intros: list[_OriginalChapterIntroOutput] = Field(default_factory=list)


class _TitleSuggestionsOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suggestions: list[str] = Field(min_length=1)


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


def _manuscript_reference_maps(
    snapshot: list[dict[str, object]],
) -> tuple[ModelReferenceMap, ModelReferenceMap]:
    chapter_ids = [
        chapter_id
        for item in snapshot
        if (chapter_id := _string(item.get("chapter_id")))
    ]
    source_ids = [
        source_id
        for item in snapshot
        for raw_ref in (
            item.get("source_refs")
            if isinstance(item.get("source_refs"), list)
            else []
        )
        if isinstance(raw_ref, dict)
        and (source_id := _string(raw_ref.get("utterance_id")))
    ]
    return (
        ModelReferenceMap("C", chapter_ids),
        ModelReferenceMap("M", source_ids),
    )


@dataclass(frozen=True)
class _QuoteCandidates:
    originals: dict[str, dict[str, tuple[str, ...]]]
    excerpts: dict[str, tuple[str, ...]]


def _quote_candidates(snapshot: list[dict[str, object]], *, polish_level: MemoirTranscriptPolishLevel = MemoirTranscriptPolishLevel.HIGH) -> _QuoteCandidates:
    return _QuoteCandidates(
        originals={_string(item.get("chapter_id")): original_quote_candidates(item, max_chars=90) for item in snapshot}
            if polish_level == MemoirTranscriptPolishLevel.LOW else {},
        excerpts={_string(item.get("chapter_id")): body_excerpt_candidates(_string(item.get("content")), max_chars=90) for item in snapshot},
    )


def _chapter_context(
    snapshot: list[dict[str, object]],
    *,
    chapter_refs: ModelReferenceMap,
    source_refs: ModelReferenceMap,
    candidates: _QuoteCandidates,
) -> str:
    parts: list[str] = []
    for fallback_index, item in enumerate(snapshot, start=1):
        chapter_id = _string(item.get("chapter_id"))
        title = _string(item.get("chapter_title")) or _string(item.get("title")) or f"第{fallback_index}章"
        content = _string(item.get("content"))
        if not chapter_id or not content:
            continue
        source_quote_lines = [f"- source_ref_id: {source_refs.encode(source_id)}; quote: {quote}"
            for source_id, quotes in candidates.originals.get(chapter_id, {}).items() for quote in quotes][:6]
        parts.append(
            "\n".join(
                [
                    f"chapter_id: {chapter_refs.encode(chapter_id)}",
                    f"chapter_index: {_int(item.get('chapter_index')) or fallback_index}",
                    f"title: {title}",
                    f"source_count: {_int(item.get('source_count'))}",
                    *(["original_quote_candidates:", *(source_quote_lines or ["- 无"])] if candidates.originals else []),
                    "body_excerpt_candidates（当前正文摘句，不是原话）:",
                    *(f"- {quote}" for quote in candidates.excerpts.get(chapter_id, ())[:6]),
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
    candidates: _QuoteCandidates | None = None,
    polish_level: MemoirTranscriptPolishLevel = MemoirTranscriptPolishLevel.HIGH,
) -> str:
    chapter_refs, source_refs = _manuscript_reference_maps(snapshot)
    chapters = _chapter_context(
        snapshot,
        chapter_refs=chapter_refs,
        source_refs=source_refs,
        candidates=candidates or _quote_candidates(snapshot, polish_level=polish_level),
    )
    target_text = "、".join(targets or []) or "全部成书文案"
    chapter_text = (
        "、".join(chapter_refs.encode(identifier) for identifier in chapter_ids)
        if chapter_ids
        else "全部章节"
    )
    feedback_text = f"\n用户本次反馈：{feedback.strip()}" if feedback and feedback.strip() else ""
    original = polish_level == MemoirTranscriptPolishLevel.LOW
    quote_source_field = ',\n      "pull_quote_source_ref_id": "对应 source_ref_id"' if original else ''
    quote_rule = (
        "每个 pull_quote 必须逐字选择 original_quote_candidates 中的一条完整候选，来源编号填写对应 source_ref_id；"
        "没有原话候选时从 body_excerpt_candidates 选择正文摘句，来源编号留空。"
        if original else "每个 pull_quote 必须逐字选择 body_excerpt_candidates 中的一条正文摘句；正文摘句不标为原话，不提供来源编号。"
    )
    cover_rule = "original_quote_candidates 或 body_excerpt_candidates" if original else "body_excerpt_candidates"
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
      "pull_quote": "从本章候选中逐字选择的完整句子"{quote_source_field}
    }}
  ],
  "back_cover_quote": "封底短句"
}}

要求：
- 只输出本次生成目标中的文案；chapter_intros 只包含本次重点章节，未要求章节导语或引语时返回空数组。
- 只以当前书稿正文摘录为事实依据；正文已包含用户确认的修改，不从旧素材或旧摘要恢复被改掉的说法。章节标题只定位章节，不据此推断事实。不新增人物、地点、事件或评价。
- book_title 要像家人会保存的一本书名，避免“主题回忆录”“章节规划”等内部词；如果原书名已经自然，可以保留。
- cover_subtitle 控制在 12 到 28 个汉字，可概括内容；素材不足时用“根据口述访谈整理而成”。
- preface 最多 220 个汉字，不设最低字数，素材短就简短，语气安静、可信、像书，不要直接套书名或项目名，不要出现“主题回忆录”“这本书记录的是「书名」”“后台”“系统”“AI生成”等模板词。
- afterword 最多 160 个汉字，不设最低字数，偏家人保存、后续补充和记忆延续，不要写空泛祝福语，不要像模板。
- 每个 chapter_intro 最多 90 个汉字，不设最低字数，像书籍章节导语，用具体画面把读者带进正文；不要解释章节功能，不要写“这一章主要记录”“从口述内容看”“相关的回忆”“本章讲述”等系统摘要句。
- {quote_rule} 没有合适候选时留空。不改写、不截断、不增删标点，不编金句。
- back_cover_quote 控制在 15 到 45 个汉字，从全书 {cover_rule} 中逐字选择完整候选；没有合适候选时留空，不使用公共格言或励志句。

章节材料：
{chapters}

文案以客观编辑者的身份书写，不冒充讲述者或家属表达感想。不得声称整理过程、讲述顺序、写书动机、亲属态度、讲述者性别或生死状态、未讲过的心情。不能写‘心里踏实了’‘没有打算写书’‘每一句都是真的’等无依据的判断。不能添加风、天气、水声、表情、动作细节或时间先后。只概括提供的事实；前言可说明收录范围，后记可邀请以后补充，导语只提供进入故事的事实线索。素材不足时缩短，不能为达到篇幅而补写。前言直接概括已给出的经历，不描述访谈或整理过程，不对文字是否逐字、完整、真实作保证。
"""


def build_title_suggestion_prompt(*, title: str, snapshot: list[dict[str, object]], count: int) -> str:
    chapter_refs, source_refs = _manuscript_reference_maps(snapshot)
    chapters = _chapter_context(
        snapshot,
        chapter_refs=chapter_refs,
        source_refs=source_refs,
        candidates=_quote_candidates(snapshot),
    )
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

文案以客观编辑者的身份书写，不冒充讲述者或家属表达感想。不得声称整理过程、讲述顺序、写书动机、亲属态度、讲述者性别或生死状态、未讲过的心情。不能写‘心里踏实了’‘没有打算写书’‘每一句都是真的’等无依据的判断。不能添加风、天气、水声、表情、动作细节或时间先后。只概括提供的事实；前言可说明收录范围，后记可邀请以后补充，导语只提供进入故事的事实线索。素材不足时缩短，不能为达到篇幅而补写。前言直接概括已给出的经历，不描述访谈或整理过程，不对文字是否逐字、完整、真实作保证。
"""


def _text_ai_configured(ai_model: str | None) -> bool:
    settings = get_settings()
    model = (ai_model or settings.default_ai_model or "").lower()
    if model in DEEPSEEK_FLASH_ALIASES or model == DEEPSEEK_V4_PRO_MODEL:
        return bool(settings.deepseek_api_key.strip())
    if model in {"volc-doubao-pro", VOLC_DOUBAO_PRO_MODEL}:
        return bool(settings.volc_ark_api_key.strip())
    if not model:
        return bool(settings.deepseek_api_key.strip() or settings.volc_ark_api_key.strip())
    return bool(
        settings.deepseek_api_key.strip()
        or settings.volc_ark_api_key.strip()
    )


def _clean_copy_text(value: object, *, max_chars: int) -> str:
    return _compact_text(_string(value), max_chars)


def _clean_title(value: object) -> str:
    title = _compact_text(_string(value), 32).strip("《》「」“”\"' ")
    return title[:32]


def _validated_original_quote(
    *,
    chapter_id: str,
    value: object,
    source_ref_id: object,
    quote_candidates: dict[str, dict[str, tuple[str, ...]]],
) -> tuple[str, str]:
    candidate_by_source = quote_candidates.get(chapter_id, {})
    source_id = _string(source_ref_id)
    if source_id:
        candidate_by_source = {source_id: candidate_by_source.get(source_id, ())}
    for candidate_source_id, candidates in candidate_by_source.items():
        quote = matched_quote(_string(value), candidates, max_chars=90)
        if quote:
            return quote, candidate_source_id
    return "", ""


def _validated_back_cover_quote(value: object, candidates: list[str]) -> str:
    return matched_quote(_string(value), candidates, max_chars=60)


def _copy_from_payload(
    payload: Any,
    *,
    snapshot: list[dict[str, object]],
    prompt: str,
    model: str | None,
    candidates: _QuoteCandidates | None = None,
    polish_level: MemoirTranscriptPolishLevel = MemoirTranscriptPolishLevel.HIGH,
) -> GeneratedManuscriptCopy | None:
    if not isinstance(payload, dict):
        return None
    candidates = candidates or _quote_candidates(snapshot, polish_level=polish_level)
    quote_candidates, excerpts = candidates.originals, candidates.excerpts
    chapter_refs, source_refs = _manuscript_reference_maps(snapshot)
    raw_intros = payload.get("chapter_intros")
    chapter_intros: list[GeneratedChapterIntro] = []
    if isinstance(raw_intros, list):
        for raw_intro in raw_intros:
            if not isinstance(raw_intro, dict):
                continue
            try:
                chapter_id = chapter_refs.decode(raw_intro.get("chapter_id"))
            except ValueError:
                continue
            intro = _clean_copy_text(raw_intro.get("intro"), max_chars=120)
            source_ref = raw_intro.get("pull_quote_source_ref_id")
            try:
                source_ref = (
                    source_refs.decode(source_ref)
                    if _string(source_ref)
                    else ""
                )
            except ValueError:
                source_ref = ""
            pull_quote, pull_quote_source_ref_id = _validated_original_quote(
                chapter_id=chapter_id,
                value=raw_intro.get("pull_quote"),
                source_ref_id=source_ref,
                quote_candidates=quote_candidates,
            )
            if not pull_quote and not source_ref:
                pull_quote = matched_quote(_string(raw_intro.get("pull_quote")), excerpts.get(chapter_id, ()), max_chars=90)
            valid_empty_quote = (raw_intro.get("pull_quote") == ""
                and not any(quote_candidates.get(chapter_id, {}).values()) and not excerpts.get(chapter_id))
            if chapter_id and (intro or pull_quote or valid_empty_quote):
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
        back_cover_quote=_validated_back_cover_quote(payload.get("back_cover_quote"), [
            quote for chapter in quote_candidates.values() for candidates in chapter.values() for quote in candidates
        ] + [quote for candidates in excerpts.values() for quote in candidates]),
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
    polish_level: MemoirTranscriptPolishLevel = MemoirTranscriptPolishLevel.HIGH,
) -> GeneratedManuscriptCopy | None:
    candidates = _quote_candidates(snapshot, polish_level=polish_level)
    prompt = build_manuscript_copy_prompt(
        title=title,
        snapshot=snapshot,
        targets=targets,
        chapter_ids=chapter_ids,
        feedback=feedback,
        candidates=candidates,
        polish_level=polish_level,
    )
    client = ai_client
    if client is None and not _text_ai_configured(ai_model):
        return None
    try:
        if client is None:
            client = get_ai_service(ai_model)
        policy = ai_text_workload_policy(AITextWorkload.GENERATION_STRUCTURED)
        generated = await generate_structured_with_client(
            client,
            SYSTEM_PROMPT,
            prompt,
            output_model=_OriginalManuscriptCopyOutput if polish_level == MemoirTranscriptPolishLevel.LOW else _ManuscriptCopyOutput,
            requested_model=ai_model,
            temperature=0.35,
            max_tokens=policy.max_tokens,
            timeout=ai_text_timeout_seconds(policy.timeout_profile),
            max_attempts=policy.max_attempts,
        )
    except HTTPException as exc:
        if exc.status_code < 500:
            raise
        logger.exception("AI memoir manuscript copy failed provider=%s", getattr(client, "provider_name", "unknown"))
        return None
    except (httpx.HTTPError, AIProviderTimeoutError, AIStructuredOutputError):
        logger.exception("AI memoir manuscript copy failed provider=%s", getattr(client, "provider_name", "unknown"))
        return None

    return _copy_from_payload(
        generated.value.model_dump(),
        snapshot=snapshot,
        prompt=prompt,
        model=generated.response.resolved_model,
        candidates=candidates,
        polish_level=polish_level,
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
        policy = ai_text_workload_policy(AITextWorkload.INTERACTIVE_STRUCTURED)
        generated = await generate_structured_with_client(
            client,
            SYSTEM_PROMPT,
            prompt,
            output_model=_TitleSuggestionsOutput,
            requested_model=ai_model,
            temperature=0.55,
            max_tokens=policy.max_tokens,
            timeout=ai_text_timeout_seconds(policy.timeout_profile),
            max_attempts=policy.max_attempts,
        )
    except HTTPException as exc:
        if exc.status_code < 500:
            raise
        logger.exception("AI memoir title suggestions failed provider=%s", getattr(client, "provider_name", "unknown"))
        return None
    except (httpx.HTTPError, AIProviderTimeoutError, AIStructuredOutputError):
        logger.exception("AI memoir title suggestions failed provider=%s", getattr(client, "provider_name", "unknown"))
        return None

    raw_suggestions = generated.value.suggestions
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
        model=generated.response.resolved_model,
        prompt=prompt,
    )
