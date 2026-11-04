from __future__ import annotations

import logging
import json
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator

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
from app.services.memoir.prompt_budget import representative_excerpt
from app.services.memoir.book_manuscript_cleanup import original_quote_candidates, body_excerpt_candidates, matched_quote


COPY_TEXT_LIMITS = {"book_title": 32, "cover_subtitle": 28, "preface": 220, "afterword": 160, "intro": 90, "back_cover_quote": 45}


SYSTEM_PROMPT = "你是一位谨慎的中文家庭回忆录编辑，只把已有口述材料整理成适合成书阅读的前置文案。"
logger = logging.getLogger(__name__)


class _ChapterIntroOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def discard_obsolete_source_ref(cls, value: Any) -> Any:
        # Polished copy uses body excerpts; old model habits must not label them as raw quotes.
        if isinstance(value, dict) and "pull_quote_source_ref_id" not in cls.model_fields:
            return {key: item for key, item in value.items() if key != "pull_quote_source_ref_id"}
        return value


class _TitleSuggestionsOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suggestions: list[str] = Field(min_length=1)


@dataclass(frozen=True)
class GeneratedChapterIntro:
    chapter_id: str
    intro: str
    pull_quote: str = ""
    pull_quote_source_ref_id: str = ""
    valid_empty_quote: bool = False


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
    valid_empty_back_cover_quote: bool = False


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
    content_budget = 24000 // max(1, sum(bool(_string(item.get("content"))) for item in snapshot))
    for fallback_index, item in enumerate(snapshot, start=1):
        chapter_id = _string(item.get("chapter_id"))
        title = _string(item.get("chapter_title")) or _string(item.get("title")) or f"第{fallback_index}章"
        content = _string(item.get("content"))
        if not chapter_id or not content:
            continue
        source_quote_lines = [f"- source_ref_id: {source_refs.encode(source_id)}; quote: {quote}"
            for source_id, quotes in candidates.originals.get(chapter_id, {}).items() for quote in quotes]
        source_quote_lines = _distributed_candidates(source_quote_lines)
        parts.append(
            "\n".join(
                [
                    f"chapter_id: {chapter_refs.encode(chapter_id)}",
                    f"chapter_index: {_int(item.get('chapter_index')) or fallback_index}",
                    f"title: {title}",
                    f"source_count: {_int(item.get('source_count'))}",
                    *(["original_quote_candidates:", *(source_quote_lines or ["- 无"])] if candidates.originals else []),
                    "body_excerpt_candidates（当前正文摘句，不是原话）:",
                    *(f"- {quote}" for quote in _distributed_candidates(list(candidates.excerpts.get(chapter_id, ())))),
                    f"正文或首中尾摘录（省略部分不能当作不存在）: {representative_excerpt(content, limit=content_budget)}",
                ]
            )
        )
    return "\n\n".join(parts)


COPY_FACT_RULES = "以当前书稿和用户本次反馈中明确给出的事实更正为依据；更正仅作用于指定文案，不改变正文。标题只定位主题。保留否定、转述来源、视角和局部不确定程度；不从旧素材恢复已修改的说法，记不清不改成未记录或已经丢失。以客观编辑者口吻概括，不冒充讲述者或家属，不推测性别、生死、写书动机、整理过程或未说出的情绪，不补场景细节。性别未明确时不用他或她等带性别的第三人称。尊重实际收录范围，专题或部分人生不扩大为一生；材料不足就简短，不作真实性或完整性保证，不评论材料多少或列出待补清单。篇幅和完整性只作内部生成限制，不写入文案；前言导语直接介绍经历，后记简短收束已讲内容。"


def _distributed_candidates(values: list[str], limit: int = 6) -> list[str]:
    if len(values) <= limit:
        return values
    return [values[round(index * (len(values) - 1) / (limit - 1))] for index in range(limit)]


def _copy_output_model(targets: list[str] | None, polish_level: MemoirTranscriptPolishLevel) -> type[BaseModel]:
    fields = {key: (str, Field(default="", max_length=limit)) for key, limit in COPY_TEXT_LIMITS.items() if key != "intro"}
    selected = set(targets or [*fields, "chapter_intros", "pull_quotes"])
    if targets and "cover_subtitle" in selected:
        fields["cover_subtitle"] = (str, Field(min_length=1, max_length=COPY_TEXT_LIMITS["cover_subtitle"]))
    if "chapter_intros" in selected or "pull_quotes" in selected:
        intro_fields = {"chapter_id": (str, ...)}
        if "chapter_intros" in selected:
            intro_fields["intro"] = (str, Field(max_length=COPY_TEXT_LIMITS["intro"]))
        if "pull_quotes" in selected:
            intro_fields["pull_quote"] = (str, ...)
            if polish_level == MemoirTranscriptPolishLevel.LOW:
                intro_fields["pull_quote_source_ref_id"] = (str, "")
        intro_model = create_model("RequestedChapterIntro", __base__=_ChapterIntroOutput, **intro_fields)
        fields["chapter_intros"] = (list[intro_model], Field(default_factory=list))
        selected.add("chapter_intros")
    return create_model("RequestedManuscriptCopy", __config__=ConfigDict(extra="forbid"),
        **{key: field for key, field in fields.items() if key in selected})


def build_manuscript_copy_prompt(
    *, title: str, snapshot: list[dict[str, object]], targets: list[str] | None = None,
    chapter_ids: list[str] | None = None, feedback: str | None = None,
    candidates: _QuoteCandidates | None = None,
    polish_level: MemoirTranscriptPolishLevel = MemoirTranscriptPolishLevel.HIGH,
) -> str:
    chapter_refs, source_refs = _manuscript_reference_maps(snapshot)
    chapters = _chapter_context(snapshot, chapter_refs=chapter_refs, source_refs=source_refs,
        candidates=candidates or _quote_candidates(snapshot, polish_level=polish_level))
    rules = {
        "book_title": "book_title：概括实际主题范围，朴素自然；现有书名合适即可保留。",
        "cover_subtitle": f"cover_subtitle：12 到 {COPY_TEXT_LIMITS['cover_subtitle']} 字概括收录范围，不补充未提供的事实。",
        "preface": f"preface：最多 {COPY_TEXT_LIMITS['preface']} 字，概括已收录经历的范围与联系，不逐章复述细节；整理与修订过程由整理说明承担，不套书名。",
        "afterword": f"afterword：最多 {COPY_TEXT_LIMITS['afterword']} 字，简短收束已讲内容，不重复整理说明，不代家人表态，不写空泛祝福。",
        "chapter_intros": f"chapter_intros：仅为指定章节填写 intro，最多 {COPY_TEXT_LIMITS['intro']} 字，用正文已有事实引入，不新造画面或解释章节功能。",
        "pull_quotes": ("pull_quote：逐字选本章 original_quote_candidates 的完整候选并填来源编号；无合适原话可用 body_excerpt_candidates，来源编号留空。" if polish_level == MemoirTranscriptPolishLevel.LOW else
            "pull_quote：逐字选本章 body_excerpt_candidates 的完整候选，不标为原话，不填来源编号。"),
        "back_cover_quote": f"back_cover_quote：从全书候选逐字选 15 到 {COPY_TEXT_LIMITS['back_cover_quote']} 字的完整句子，无合适候选留空。",
    }
    selected = targets or list(rules)
    instructions = "\n".join("- " + rules[key] for key in selected)
    chapters_requested = "、".join(chapter_refs.encode(identifier) for identifier in chapter_ids) if chapter_ids else "全部章节"
    return f"""根据当前书稿生成指定成书文案。
当前书名：{title}
本次目标：{'、'.join(selected)}
重点章节：{chapters_requested}
用户反馈：{feedback or '无'}
{COPY_FACT_RULES}
{instructions}
引语须能独立理解，不能遗漏转述归属或疑义，不选脱离上下文会变意的句子。来源候选只是当前素材版本与正文共有的句子，不保证是录音逐字原话。没有适合独立引用的候选就留空，即使候选列表不为空；不截断、改写、拼接或增删标点。
只输出本次目标对应的字段；章节输出只包含重点章节。严格 JSON，遵守 schema：
{json.dumps(_copy_output_model(targets, polish_level).model_json_schema(), ensure_ascii=False)}
章节材料：
{chapters}
"""


def build_title_suggestion_prompt(*, title: str, snapshot: list[dict[str, object]], count: int) -> str:
    chapter_refs, source_refs = _manuscript_reference_maps(snapshot)
    chapters = _chapter_context(snapshot, chapter_refs=chapter_refs, source_refs=source_refs, candidates=_quote_candidates(snapshot))
    return f"""根据当前书稿拟 {count} 个不同主书名，当前书名：{title}。
{COPY_FACT_RULES}
覆盖实际共同主题；只讲某阶段或专题时可以围绕它拟题，不要求概括一生。不因亲属称呼推定讲述者身份。
每个标题 4 到 14 字，朴素克制，不含副标题或内部流程术语；不靠夸张评价营造文学感。
只返回 JSON：{{"suggestions":["候选书名"]}}。
章节材料：
{chapters}
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


def _clean_copy_text(value: object) -> str:
    return " ".join(_string(value).split())


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
            intro = _clean_copy_text(raw_intro.get("intro"))
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
            valid_empty_quote = raw_intro.get("pull_quote") == ""
            if chapter_id and (intro or pull_quote or valid_empty_quote):
                chapter_intros.append(
                    GeneratedChapterIntro(
                        chapter_id=chapter_id,
                        intro=intro,
                        pull_quote=pull_quote,
                        pull_quote_source_ref_id=pull_quote_source_ref_id,
                        valid_empty_quote=valid_empty_quote,
                    )
                )
    copy = GeneratedManuscriptCopy(
        book_title=_clean_copy_text(payload.get("book_title")),
        cover_subtitle=_clean_copy_text(payload.get("cover_subtitle")),
        preface=_clean_copy_text(payload.get("preface")),
        afterword=_clean_copy_text(payload.get("afterword")),
        chapter_intros=chapter_intros,
        back_cover_quote=_validated_back_cover_quote(payload.get("back_cover_quote"), [
            quote for chapter in quote_candidates.values() for candidates in chapter.values() for quote in candidates
        ] + [quote for candidates in excerpts.values() for quote in candidates]),
        model=model,
        prompt=prompt,
        valid_empty_back_cover_quote=payload.get("back_cover_quote") == "",
    )
    if (
        copy.book_title
        or copy.cover_subtitle
        or copy.preface
        or copy.afterword
        or copy.chapter_intros
        or copy.back_cover_quote
        or payload.get("back_cover_quote") == ""
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
            output_model=_copy_output_model(targets, polish_level),
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
        # Omitted quote fields must remain distinct from explicit empty results.
        generated.value.model_dump(exclude_unset=True),
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
