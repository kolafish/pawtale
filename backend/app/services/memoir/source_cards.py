from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import MemoirEditingJobStatus, MemoirEditingJobType, MemoirSemanticCacheStatus
from app.models.memoir import MemoirEditingJob, MemoirSourceCard, MemoirTranscriptVersion, MemoirVoiceUtterance
from app.services.ai_factory import get_ai_service
from app.services.ai_types import AITextClient
from app.services.json_utils import extract_json_payload
from app.services.memoir.chapter_source_links import bind_source_card_to_active_utterance_links
from app.services.memoir.editing_jobs import EditingJobResult, enqueue_editing_job
from app.services.memoir.semantic_state import semantic_text_hash, upsert_source_card


SOURCE_CARD_ENGINE = "llm_source_card_v1"
SOURCE_CARD_SYSTEM_PROMPT = "你是一位谨慎的中文回忆录素材整理编辑，只能根据给定口述内容生成素材卡片。"
SOURCE_CARD_UNAVAILABLE_ERROR = "source card AI unavailable"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SourceCardGenerationResult:
    card_json: dict
    provider: str | None
    model: str | None
    prompt: str


def _compact_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def source_text_for_utterance(utterance: MemoirVoiceUtterance) -> str:
    return _compact_text((utterance.transcript_edited or "").strip() or (utterance.transcript_raw or "").strip())


def source_text_hash(source_text: str) -> str:
    return semantic_text_hash(source_text)


def _trim_text(value: object, *, limit: int = 120) -> str:
    text = _compact_text(value if isinstance(value, str) else "")
    return text[:limit]


def _string_list(value: object, *, limit: int = 8, item_limit: int = 40) -> list[str]:
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


def _preview_quote(source_text: str, *, limit: int = 80) -> str:
    text = _compact_text(source_text)
    if len(text) <= limit:
        return text
    for marker in ("。", "！", "？", "；", ";", "!", "?"):
        index = text.find(marker)
        if 0 < index + 1 <= limit:
            return text[: index + 1].strip()
    return text[:limit].rstrip()


def _evidence_quote(value: object, source_text: str) -> str:
    quote = _trim_text(value, limit=120)
    return quote if quote and quote in source_text else ""


def _key_facts(value: object, source_text: str) -> list[dict[str, str]]:
    if not isinstance(value, list):
        value = []
    facts: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        quote = _evidence_quote(item.get("evidence_quote"), source_text)
        if not quote:
            continue
        text = _trim_text(item.get("text"), limit=120) or quote
        facts.append({"text": text, "evidence_quote": quote})
        if len(facts) >= 6:
            break
    if not facts and source_text:
        quote = _preview_quote(source_text)
        facts.append({"text": quote, "evidence_quote": quote})
    return facts


def normalize_source_card_payload(payload: Any, *, source_text: str) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("AI did not return a JSON object")
    fallback_quote = _preview_quote(source_text)
    summary = _trim_text(payload.get("summary"), limit=260) or fallback_quote
    short_title = _trim_text(payload.get("short_title"), limit=40) or fallback_quote[:40]
    return {
        "short_title": short_title,
        "summary": summary,
        "people": _string_list(payload.get("people")),
        "places": _string_list(payload.get("places")),
        "time_hints": _string_list(payload.get("time_hints")),
        "event_tags": _string_list(payload.get("event_tags")),
        "emotion_tags": _string_list(payload.get("emotion_tags")),
        "key_facts": _key_facts(payload.get("key_facts"), source_text),
        "open_questions": _string_list(payload.get("open_questions"), limit=5, item_limit=80),
    }


def build_source_card_prompt(*, source_text: str, utterance_id: str) -> str:
    return f"""请为下面这一条回忆录口述素材生成结构化素材卡片。

utterance_id: {utterance_id}
source_text:
{source_text}

请输出严格 JSON，不要 Markdown，不要解释。格式：
{{
  "short_title": "10 到 20 字的素材标题",
  "summary": "一到两句话摘要，只能概括 source_text 已讲内容",
  "people": ["人物"],
  "places": ["地点"],
  "time_hints": ["时间线索"],
  "event_tags": ["事件标签"],
  "emotion_tags": ["情绪标签"],
  "key_facts": [
    {{"text": "事实描述", "evidence_quote": "必须逐字来自 source_text 的短句"}}
  ],
  "open_questions": ["可以继续追问的问题"]
}}

要求：
- 不要新增 source_text 没有说过的人物、地点、时间、事件或情绪。
- key_facts 的 evidence_quote 必须逐字来自 source_text。
- open_questions 只能围绕 source_text 中已经出现的线索继续追问。
"""


async def generate_source_card_payload(
    *,
    source_text: str,
    utterance_id: str,
    ai_model: str | None = None,
    ai_client: AITextClient | None = None,
) -> SourceCardGenerationResult:
    prompt = build_source_card_prompt(source_text=source_text, utterance_id=utterance_id)
    client = ai_client
    if client is None:
        client = get_ai_service(ai_model)
    content = await client._call_llm(
        SOURCE_CARD_SYSTEM_PROMPT,
        prompt,
        temperature=0.1,
        max_tokens=1200,
        timeout=60.0,
    )
    payload = extract_json_payload(str(content))
    return SourceCardGenerationResult(
        card_json=normalize_source_card_payload(payload, source_text=source_text),
        provider=getattr(client, "provider_name", None),
        model=getattr(client, "model_name", ai_model),
        prompt=prompt,
    )


async def _current_transcript_version_id(db: AsyncSession, utterance: MemoirVoiceUtterance) -> str | None:
    message = utterance.chat_message
    if message and message.transcript_version_id:
        return message.transcript_version_id
    return await db.scalar(
        select(MemoirTranscriptVersion.id)
        .where(MemoirTranscriptVersion.source_artifact_id == utterance.id)
        .order_by(MemoirTranscriptVersion.created_at.desc(), MemoirTranscriptVersion.id.desc())
    )


async def _load_project_utterance(
    db: AsyncSession,
    *,
    project_id: str,
    utterance_id: str,
) -> MemoirVoiceUtterance:
    utterance = await db.scalar(
        select(MemoirVoiceUtterance)
        .where(MemoirVoiceUtterance.id == utterance_id)
        .options(
            selectinload(MemoirVoiceUtterance.voice_session),
            selectinload(MemoirVoiceUtterance.chat_message),
        )
    )
    if not utterance or utterance.voice_session.project_id != project_id:
        raise HTTPException(status_code=404, detail="素材不存在")
    return utterance


async def _ready_card_for_signature(
    db: AsyncSession,
    *,
    project_id: str,
    utterance_id: str,
    source_hash: str,
    engine: str,
) -> MemoirSourceCard | None:
    return await db.scalar(
        select(MemoirSourceCard).where(
            MemoirSourceCard.project_id == project_id,
            MemoirSourceCard.utterance_id == utterance_id,
            MemoirSourceCard.engine == engine,
            MemoirSourceCard.source_text_hash == source_hash,
            MemoirSourceCard.status == MemoirSemanticCacheStatus.READY.value,
        )
    )


async def _mark_stale_source_cards(
    db: AsyncSession,
    *,
    project_id: str,
    utterance_id: str,
    current_hash: str,
    engine: str,
) -> int:
    result = await db.execute(
        select(MemoirSourceCard).where(
            MemoirSourceCard.project_id == project_id,
            MemoirSourceCard.utterance_id == utterance_id,
            MemoirSourceCard.engine == engine,
            MemoirSourceCard.source_text_hash != current_hash,
            MemoirSourceCard.status == MemoirSemanticCacheStatus.READY.value,
        )
    )
    cards = result.scalars().all()
    for card in cards:
        card.status = MemoirSemanticCacheStatus.STALE.value
        db.add(card)
    if cards:
        await db.flush()
    return len(cards)


async def generate_or_reuse_source_card(
    db: AsyncSession,
    *,
    project_id: str,
    utterance_id: str,
    ai_model: str | None = None,
    ai_client: AITextClient | None = None,
    engine: str = SOURCE_CARD_ENGINE,
    force_regenerate: bool = False,
) -> MemoirSourceCard:
    utterance = await _load_project_utterance(db, project_id=project_id, utterance_id=utterance_id)
    source_text = source_text_for_utterance(utterance)
    current_hash = source_text_hash(source_text)
    transcript_version_id = await _current_transcript_version_id(db, utterance)

    await _mark_stale_source_cards(
        db,
        project_id=project_id,
        utterance_id=utterance_id,
        current_hash=current_hash,
        engine=engine,
    )
    if not force_regenerate:
        existing = await _ready_card_for_signature(
            db,
            project_id=project_id,
            utterance_id=utterance_id,
            source_hash=current_hash,
            engine=engine,
        )
        if existing:
            return existing

    if not source_text:
        return await upsert_source_card(
            db,
            project_id=project_id,
            utterance_id=utterance_id,
            transcript_version_id=transcript_version_id,
            source_text_hash=current_hash,
            engine=engine,
            status=MemoirSemanticCacheStatus.FAILED,
            card_json={},
            error_message="source text empty",
        )

    try:
        generated = await generate_source_card_payload(
            source_text=source_text,
            utterance_id=utterance_id,
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
        }
        return await upsert_source_card(
            db,
            project_id=project_id,
            utterance_id=utterance_id,
            transcript_version_id=transcript_version_id,
            source_text_hash=current_hash,
            engine=engine,
            status=MemoirSemanticCacheStatus.READY,
            card_json=card_json,
        )
    except Exception as exc:
        logger.exception("memoir source card generation failed utterance_id=%s", utterance_id)
        return await upsert_source_card(
            db,
            project_id=project_id,
            utterance_id=utterance_id,
            transcript_version_id=transcript_version_id,
            source_text_hash=current_hash,
            engine=engine,
            status=MemoirSemanticCacheStatus.FAILED,
            card_json={},
            error_message=str(exc) or SOURCE_CARD_UNAVAILABLE_ERROR,
        )


async def schedule_source_card_generation_job(
    db: AsyncSession,
    *,
    project_id: str,
    utterance_id: str,
    ai_model: str | None = None,
    reason: str = "",
) -> MemoirEditingJob:
    payload: dict[str, object] = {"utterance_id": utterance_id}
    if ai_model:
        payload["ai_model"] = ai_model
    if reason:
        payload["reason"] = reason
    return await enqueue_editing_job(
        db,
        project_id=project_id,
        job_type=MemoirEditingJobType.GENERATE_SOURCE_CARD,
        payload_json=payload,
        dedupe_key=f"generate_source_card:{utterance_id}",
        replace_completed=True,
    )


async def run_source_card_generation_job(db: AsyncSession, job: MemoirEditingJob) -> EditingJobResult:
    payload = job.payload_json or {}
    utterance_id = str(payload.get("utterance_id") or "").strip()
    if not utterance_id:
        return EditingJobResult(status=MemoirEditingJobStatus.SKIPPED, error_message="utterance unavailable")
    try:
        card = await generate_or_reuse_source_card(
            db,
            project_id=job.project_id,
            utterance_id=utterance_id,
            ai_model=payload.get("ai_model") if isinstance(payload.get("ai_model"), str) else None,
        )
    except HTTPException as exc:
        if exc.status_code in {400, 404}:
            return EditingJobResult(status=MemoirEditingJobStatus.SKIPPED, error_message=str(exc.detail))
        raise
    linked_source_count = 0
    if card.status == MemoirSemanticCacheStatus.READY.value:
        linked_source_count = await bind_source_card_to_active_utterance_links(
            db,
            project_id=job.project_id,
            utterance_id=utterance_id,
            source_card_id=card.id,
            transcript_version_id=card.transcript_version_id,
        )
    return EditingJobResult(
        result_json={
            "source_card_id": card.id,
            "utterance_id": card.utterance_id,
            "source_text_hash": card.source_text_hash,
            "status": card.status,
            "error_message": card.error_message,
            "linked_source_count": linked_source_count,
        }
    )
