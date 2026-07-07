from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import MemoirSemanticCacheStatus
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirChapterCard,
    MemoirSourceCard,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.schemas.memoir_book import MemoirBookChapterSourceRef
from app.services.ai_factory import get_ai_service
from app.services.ai_types import AITextClient
from app.services.json_utils import extract_json_payload
from app.services.memoir.book_source_links import chapter_source_ref_dicts as _chapter_source_ref_dicts
from app.services.memoir.semantic_state import semantic_json_signature, upsert_chapter_card
from app.services.memoir.source_cards import SOURCE_CARD_ENGINE, source_text_for_utterance, source_text_hash


CHAPTER_CARD_ENGINE = "llm_chapter_card_v1"
CHAPTER_CARD_SYSTEM_PROMPT = "你是一位谨慎的中文回忆录章节审读编辑，只能根据章节正文和来源素材卡片判断章节状态。"
CHAPTER_CARD_UNAVAILABLE_ERROR = "chapter card AI unavailable"
VALID_GAP_SEVERITIES = {"high", "medium", "low"}
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChapterCardSourceContext:
    utterance_id: str
    source_text_hash: str
    quote: str
    source_text: str
    source_card_id: str | None
    source_card_hash: str | None
    source_card_signature: str | None
    source_card_summary: str


@dataclass(frozen=True)
class ChapterCardContext:
    chapter: MemoirBookPlanChapter
    draft_version: MemoirBookChapterDraftVersion | None
    sources: list[ChapterCardSourceContext]
    input_signature: str
    signature_payload: dict[str, object]


@dataclass(frozen=True)
class ChapterCardGenerationResult:
    card_json: dict
    provider: str | None
    model: str | None
    prompt: str


def _compact_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _trim_text(value: object, *, limit: int = 200) -> str:
    text = _compact_text(value if isinstance(value, str) else "")
    return text[:limit]


def _string_list(value: object, *, limit: int = 8, item_limit: int = 50) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        text = _trim_text(item, limit=item_limit)
        if text and text not in items:
            items.append(text)
        if len(items) >= limit:
            break
    return items


def _preview_quote(text: str, *, limit: int = 90) -> str:
    compact = _compact_text(text)
    if len(compact) <= limit:
        return compact
    for marker in ("。", "！", "？", "；", ";", "!", "?"):
        index = compact.find(marker)
        if 0 < index + 1 <= limit:
            return compact[: index + 1].strip()
    return compact[:limit].rstrip()


def _source_ref_utterance_id(ref: object) -> str:
    if isinstance(ref, MemoirBookChapterSourceRef):
        return ref.utterance_id
    if isinstance(ref, dict) and isinstance(ref.get("utterance_id"), str):
        return ref["utterance_id"].strip()
    return ""


def _source_refs(ref_items: list[dict[str, object]]) -> list[MemoirBookChapterSourceRef]:
    refs: list[MemoirBookChapterSourceRef] = []
    seen: set[str] = set()
    for item in ref_items:
        if not isinstance(item, dict):
            continue
        try:
            ref = MemoirBookChapterSourceRef.model_validate(item)
        except ValueError:
            continue
        if not ref.utterance_id or ref.utterance_id in seen:
            continue
        refs.append(ref)
        seen.add(ref.utterance_id)
    return refs


def _latest_draft_version(draft: MemoirBookChapterDraft | None) -> MemoirBookChapterDraftVersion | None:
    if not draft or not draft.versions:
        return None
    return max(draft.versions, key=lambda item: (item.version, item.created_at, item.id))


def _card_summary(card: MemoirSourceCard | None, source_text: str) -> str:
    if card and isinstance(card.card_json, dict):
        summary = _trim_text(card.card_json.get("summary"), limit=220)
        if summary:
            return summary
    return _preview_quote(source_text, limit=140)


def _source_card_by_utterance(cards: list[MemoirSourceCard], source_hashes: dict[str, str]) -> dict[str, MemoirSourceCard]:
    by_utterance: dict[str, MemoirSourceCard] = {}
    for card in cards:
        if card.status != MemoirSemanticCacheStatus.READY.value:
            continue
        if card.source_text_hash != source_hashes.get(card.utterance_id):
            continue
        existing = by_utterance.get(card.utterance_id)
        if not existing or (card.updated_at, card.id) > (existing.updated_at, existing.id):
            by_utterance[card.utterance_id] = card
    return by_utterance


async def _load_chapter(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> MemoirBookPlanChapter:
    chapter = await db.scalar(
        select(MemoirBookPlanChapter)
        .join(MemoirBookPlan, MemoirBookPlanChapter.plan_id == MemoirBookPlan.id)
        .where(
            MemoirBookPlanChapter.id == chapter_id,
            MemoirBookPlanChapter.project_id == project_id,
        )
        .options(
            selectinload(MemoirBookPlanChapter.plan),
            selectinload(MemoirBookPlanChapter.draft).selectinload(MemoirBookChapterDraft.versions),
        )
        .execution_options(populate_existing=True)
    )
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    return chapter


async def _utterances_by_id(
    db: AsyncSession,
    *,
    project_id: str,
    utterance_ids: list[str],
) -> dict[str, MemoirVoiceUtterance]:
    if not utterance_ids:
        return {}
    rows = (
        await db.execute(
            select(MemoirVoiceUtterance)
            .join(MemoirVoiceSession, MemoirVoiceUtterance.voice_session_id == MemoirVoiceSession.id)
            .where(
                MemoirVoiceSession.project_id == project_id,
                MemoirVoiceUtterance.id.in_(utterance_ids),
            )
        )
    ).scalars().all()
    return {row.id: row for row in rows}


async def _source_cards_by_utterance(
    db: AsyncSession,
    *,
    project_id: str,
    utterance_ids: list[str],
    source_hashes: dict[str, str],
) -> dict[str, MemoirSourceCard]:
    if not utterance_ids:
        return {}
    cards = (
        await db.execute(
            select(MemoirSourceCard).where(
                MemoirSourceCard.project_id == project_id,
                MemoirSourceCard.utterance_id.in_(utterance_ids),
                MemoirSourceCard.engine == SOURCE_CARD_ENGINE,
                MemoirSourceCard.status == MemoirSemanticCacheStatus.READY.value,
            )
        )
    ).scalars().all()
    return _source_card_by_utterance(cards, source_hashes)


async def build_chapter_card_context(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> ChapterCardContext:
    chapter = await _load_chapter(db, project_id=project_id, chapter_id=chapter_id)
    refs = _source_refs(await _chapter_source_ref_dicts(db, chapter=chapter))
    utterance_ids = [_source_ref_utterance_id(ref) for ref in refs]
    utterances = await _utterances_by_id(db, project_id=project_id, utterance_ids=utterance_ids)
    source_hashes = {
        utterance_id: source_text_hash(source_text_for_utterance(utterance))
        for utterance_id, utterance in utterances.items()
    }
    cards_by_utterance = await _source_cards_by_utterance(
        db,
        project_id=project_id,
        utterance_ids=utterance_ids,
        source_hashes=source_hashes,
    )

    sources: list[ChapterCardSourceContext] = []
    source_signature_items: list[dict[str, object]] = []
    for ref in refs:
        utterance = utterances.get(ref.utterance_id)
        if not utterance:
            continue
        source_text = source_text_for_utterance(utterance)
        source_hash = source_hashes.get(ref.utterance_id) or source_text_hash(source_text)
        card = cards_by_utterance.get(ref.utterance_id)
        quote = ref.quote.strip() if ref.quote and ref.quote.strip() in source_text else _preview_quote(source_text)
        source_context = ChapterCardSourceContext(
            utterance_id=ref.utterance_id,
            source_text_hash=source_hash,
            quote=quote,
            source_text=source_text,
            source_card_id=card.id if card else None,
            source_card_hash=card.source_text_hash if card else None,
            source_card_signature=semantic_json_signature(
                {
                    "id": card.id,
                    "source_text_hash": card.source_text_hash,
                    "card_json": card.card_json,
                }
            ) if card else None,
            source_card_summary=_card_summary(card, source_text),
        )
        sources.append(source_context)
        source_signature_items.append(
            {
                "utterance_id": source_context.utterance_id,
                "quote": source_context.quote,
                "source_text_hash": source_context.source_text_hash,
                "source_card_id": source_context.source_card_id,
                "source_card_hash": source_context.source_card_hash,
                "source_card_signature": source_context.source_card_signature,
            }
        )

    draft_version = _latest_draft_version(chapter.draft)
    signature_payload: dict[str, object] = {
        "chapter_id": chapter.id,
        "chapter_title": chapter.title,
        "plan_id": chapter.plan_id,
        "source_refs": source_signature_items,
        "draft_version_id": draft_version.id if draft_version else None,
        "draft_version": draft_version.version if draft_version else None,
        "draft_content_hash": source_text_hash(draft_version.content) if draft_version else "",
    }
    return ChapterCardContext(
        chapter=chapter,
        draft_version=draft_version,
        sources=sources,
        input_signature=semantic_json_signature(signature_payload),
        signature_payload=signature_payload,
    )


def build_chapter_card_prompt(context: ChapterCardContext) -> str:
    draft_text = _trim_text(context.draft_version.content if context.draft_version else "", limit=1600)
    source_lines = "\n\n".join(
        (
            f"source {index}\n"
            f"utterance_id: {source.utterance_id}\n"
            f"source_card_id: {source.source_card_id or '无'}\n"
            f"source_summary: {source.source_card_summary or '无'}\n"
            f"source_quote: {source.quote}"
        )
        for index, source in enumerate(context.sources, start=1)
    )
    return f"""请基于章节标题、最新章节正文和来源素材，生成本章语义状态卡片。

chapter_id: {context.chapter.id}
chapter_title: {context.chapter.title}

latest_draft:
{draft_text or "无"}

sources:
{source_lines or "无"}

请输出严格 JSON，不要 Markdown，不要解释。格式：
{{
  "summary": "本章当前语义摘要",
  "covered": {{
    "time": ["已覆盖时间"],
    "places": ["已覆盖地点"],
    "people": ["已覆盖人物"],
    "events": ["已覆盖事件"]
  }},
  "gaps": [
    {{"type": "event_sequence", "severity": "medium", "message": "缺口说明"}}
  ],
  "next_questions": ["下一轮追问问题"],
  "evidence_refs": [
    {{"utterance_id": "来源 utterance_id", "quote": "必须逐字来自对应 source_quote 或 source_summary 的短句"}}
  ]
}}

要求：
- 不要改写章节正文，不要输出正文。
- summary 是章节状态摘要，不是章节导语。
- covered/gaps/next_questions 只能根据 latest_draft 和 sources 判断。
- evidence_refs 的 quote 必须能回溯到对应来源。
- gaps.severity 只能是 high、medium、low。
"""


def _covered_payload(value: object) -> dict[str, list[str]]:
    raw = value if isinstance(value, dict) else {}
    return {
        "time": _string_list(raw.get("time")),
        "places": _string_list(raw.get("places")),
        "people": _string_list(raw.get("people")),
        "events": _string_list(raw.get("events")),
    }


def _gaps_payload(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    gaps: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        message = _trim_text(item.get("message"), limit=160)
        if not message:
            continue
        severity = _trim_text(item.get("severity"), limit=20).lower()
        if severity not in VALID_GAP_SEVERITIES:
            severity = "medium"
        gap_type = _trim_text(item.get("type"), limit=60) or "content_gap"
        gaps.append({"type": gap_type, "severity": severity, "message": message})
        if len(gaps) >= 8:
            break
    return gaps


def _evidence_refs(value: object, sources: list[ChapterCardSourceContext]) -> list[dict[str, str]]:
    source_text_by_id = {
        source.utterance_id: f"{source.source_card_summary}\n{source.quote}\n{source.source_text}"
        for source in sources
    }
    if not isinstance(value, list):
        value = []
    refs: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        utterance_id = _trim_text(item.get("utterance_id"), limit=80)
        quote = _trim_text(item.get("quote"), limit=140)
        if not utterance_id or not quote:
            continue
        source_text = source_text_by_id.get(utterance_id, "")
        if quote not in source_text:
            continue
        refs.append({"utterance_id": utterance_id, "quote": quote})
        if len(refs) >= 8:
            break
    if not refs and sources:
        first = sources[0]
        refs.append({"utterance_id": first.utterance_id, "quote": first.quote})
    return refs


def normalize_chapter_card_payload(payload: Any, *, context: ChapterCardContext) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("AI did not return a JSON object")
    summary = _trim_text(payload.get("summary"), limit=320)
    if not summary:
        if context.draft_version:
            summary = _preview_quote(context.draft_version.content, limit=180)
        elif context.sources:
            summary = f"本章围绕{context.chapter.title}，已有 {len(context.sources)} 条来源素材。"
        else:
            summary = f"本章围绕{context.chapter.title}，暂缺可审读素材。"
    return {
        "summary": summary,
        "covered": _covered_payload(payload.get("covered")),
        "gaps": _gaps_payload(payload.get("gaps")),
        "next_questions": _string_list(payload.get("next_questions"), limit=6, item_limit=100),
        "source_card_ids": [source.source_card_id for source in context.sources if source.source_card_id],
        "evidence_refs": _evidence_refs(payload.get("evidence_refs"), context.sources),
        "input_signature": context.input_signature,
    }


async def generate_chapter_card_payload(
    *,
    context: ChapterCardContext,
    ai_model: str | None = None,
    ai_client: AITextClient | None = None,
) -> ChapterCardGenerationResult:
    prompt = build_chapter_card_prompt(context)
    client = ai_client
    if client is None:
        client = get_ai_service(ai_model)
    content = await client._call_llm(
        CHAPTER_CARD_SYSTEM_PROMPT,
        prompt,
        temperature=0.15,
        max_tokens=1600,
        timeout=75.0,
    )
    payload = extract_json_payload(str(content))
    return ChapterCardGenerationResult(
        card_json=normalize_chapter_card_payload(payload, context=context),
        provider=getattr(client, "provider_name", None),
        model=getattr(client, "model_name", ai_model),
        prompt=prompt,
    )


async def _ready_card_for_signature(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    input_signature: str,
    engine: str,
) -> MemoirChapterCard | None:
    return await db.scalar(
        select(MemoirChapterCard).where(
            MemoirChapterCard.project_id == project_id,
            MemoirChapterCard.chapter_id == chapter_id,
            MemoirChapterCard.engine == engine,
            MemoirChapterCard.input_signature == input_signature,
            MemoirChapterCard.status == MemoirSemanticCacheStatus.READY.value,
        )
    )


async def _mark_stale_chapter_cards(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    current_signature: str,
    engine: str,
) -> int:
    cards = (
        await db.execute(
            select(MemoirChapterCard).where(
                MemoirChapterCard.project_id == project_id,
                MemoirChapterCard.chapter_id == chapter_id,
                MemoirChapterCard.engine == engine,
                MemoirChapterCard.input_signature != current_signature,
                MemoirChapterCard.status == MemoirSemanticCacheStatus.READY.value,
            )
        )
    ).scalars().all()
    for card in cards:
        card.status = MemoirSemanticCacheStatus.STALE.value
        db.add(card)
    if cards:
        await db.flush()
    return len(cards)


async def generate_or_reuse_chapter_card(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    ai_model: str | None = None,
    ai_client: AITextClient | None = None,
    engine: str = CHAPTER_CARD_ENGINE,
    force_regenerate: bool = False,
) -> MemoirChapterCard:
    context = await build_chapter_card_context(db, project_id=project_id, chapter_id=chapter_id)
    await _mark_stale_chapter_cards(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        current_signature=context.input_signature,
        engine=engine,
    )
    if not force_regenerate:
        existing = await _ready_card_for_signature(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
            input_signature=context.input_signature,
            engine=engine,
        )
        if existing:
            return existing

    try:
        generated = await generate_chapter_card_payload(
            context=context,
            ai_model=ai_model,
            ai_client=ai_client,
        )
        card_json = {
            **generated.card_json,
            "generation": {
                "provider": generated.provider,
                "model": generated.model,
                "engine": engine,
            },
            "signature_payload": context.signature_payload,
        }
        return await upsert_chapter_card(
            db,
            project_id=project_id,
            plan_id=context.chapter.plan_id,
            chapter_id=context.chapter.id,
            draft_version_id=context.draft_version.id if context.draft_version else None,
            input_signature=context.input_signature,
            engine=engine,
            status=MemoirSemanticCacheStatus.READY,
            card_json=card_json,
        )
    except Exception as exc:
        logger.exception("memoir chapter card generation failed chapter_id=%s", chapter_id)
        return await upsert_chapter_card(
            db,
            project_id=project_id,
            plan_id=context.chapter.plan_id,
            chapter_id=context.chapter.id,
            draft_version_id=context.draft_version.id if context.draft_version else None,
            input_signature=context.input_signature,
            engine=engine,
            status=MemoirSemanticCacheStatus.FAILED,
            card_json={"signature_payload": context.signature_payload},
            error_message=str(exc) or CHAPTER_CARD_UNAVAILABLE_ERROR,
        )
