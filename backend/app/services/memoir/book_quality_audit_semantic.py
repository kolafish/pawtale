from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import MemoirSemanticCacheStatus
from app.models.memoir import MemoirBookQualityAuditSnapshot, MemoirChapterCard
from app.schemas.memoir_book import (
    MemoirBookQualityAuditChapterResponse,
    MemoirBookQualityAuditDisplayResponse,
    MemoirBookQualityAuditPrimaryActionResponse,
)
from app.services.ai_factory import get_ai_service
from app.services.ai_types import AITextClient
from app.services.json_utils import extract_json_payload
from app.services.memoir.chapter_cards import generate_or_reuse_chapter_card
from app.services.memoir.semantic_state import semantic_json_signature, upsert_book_quality_audit_snapshot


SEMANTIC_AUDIT_ENGINE = "llm_semantic_v1"
SEMANTIC_AUDIT_SYSTEM_PROMPT = "你是一位谨慎的中文回忆录审读编辑，只能根据章节卡片和规则审计结果提出内容审计建议。"
VALID_SEMANTIC_GAP_SEVERITIES = {"high", "medium", "low"}
VALID_AUDIT_OVERALL_STATUSES = {
    "needs_plan",
    "needs_sources",
    "needs_drafts",
    "needs_updates",
    "collecting",
    "healthy",
}
VALID_PRIMARY_ACTION_TYPES = {
    "generate_plan",
    "assign_sources",
    "continue_interview",
    "open_chapter_workspace",
    "review_book",
    "none",
}
logger = logging.getLogger(__name__)


def _compact_text(value: object, *, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _string_list(value: object, *, limit: int = 6, item_limit: int = 100) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        text = _compact_text(item, limit=item_limit)
        if text and text not in items:
            items.append(text)
        if len(items) >= limit:
            break
    return items


def _gap_items(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    gaps: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        message = _compact_text(item.get("message"), limit=180)
        if not message:
            continue
        severity = _compact_text(item.get("severity"), limit=20).lower()
        if severity not in VALID_SEMANTIC_GAP_SEVERITIES:
            severity = "medium"
        gap_type = _compact_text(item.get("type"), limit=60) or "content_gap"
        gaps.append({"type": gap_type, "severity": severity, "message": message})
        if len(gaps) >= 8:
            break
    return gaps


def _evidence_refs(value: object, allowed_refs: dict[str, set[str]]) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    refs: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        utterance_id = _compact_text(item.get("utterance_id"), limit=80)
        quote = _compact_text(item.get("quote"), limit=160)
        if not utterance_id or not quote:
            continue
        allowed_quotes = allowed_refs.get(utterance_id, set())
        if not allowed_quotes or quote not in allowed_quotes:
            continue
        refs.append({"utterance_id": utterance_id, "quote": quote})
        if len(refs) >= 8:
            break
    return refs


def _chapter_card_ids(cards: list[MemoirChapterCard]) -> list[str]:
    return [card.id for card in cards if card.status == "ready"]


def _source_card_ids_from_chapter_cards(cards: list[MemoirChapterCard]) -> list[str]:
    ids: list[str] = []
    for card in cards:
        card_json = card.card_json if isinstance(card.card_json, dict) else {}
        raw_ids = card_json.get("source_card_ids")
        if not isinstance(raw_ids, list):
            continue
        for item in raw_ids:
            if isinstance(item, str) and item.strip() and item not in ids:
                ids.append(item)
    return ids


def _semantic_signature(
    *,
    rules_snapshot: MemoirBookQualityAuditSnapshot,
    chapter_cards: list[MemoirChapterCard],
) -> str:
    return semantic_json_signature(
        {
            "engine": SEMANTIC_AUDIT_ENGINE,
            "rules_snapshot_signature": rules_snapshot.input_signature,
            "chapter_cards": [
                {
                    "id": card.id,
                    "chapter_id": card.chapter_id,
                    "input_signature": card.input_signature,
                    "status": card.status,
                    "card_json": card.card_json,
                }
                for card in sorted(chapter_cards, key=lambda item: (item.chapter_id, item.id))
            ],
        }
    )


async def _ready_semantic_snapshot(
    db: AsyncSession,
    *,
    project_id: str,
    input_signature: str,
) -> MemoirBookQualityAuditSnapshot | None:
    return await db.scalar(
        select(MemoirBookQualityAuditSnapshot).where(
            MemoirBookQualityAuditSnapshot.project_id == project_id,
            MemoirBookQualityAuditSnapshot.engine == SEMANTIC_AUDIT_ENGINE,
            MemoirBookQualityAuditSnapshot.input_signature == input_signature,
            MemoirBookQualityAuditSnapshot.error_message == "",
        )
    )


def _chapter_card_allowed_refs(card: MemoirChapterCard) -> dict[str, set[str]]:
    allowed: dict[str, set[str]] = {}
    card_json = card.card_json if isinstance(card.card_json, dict) else {}
    evidence_refs = card_json.get("evidence_refs")
    if isinstance(evidence_refs, list):
        for item in evidence_refs:
            if not isinstance(item, dict):
                continue
            utterance_id = item.get("utterance_id")
            quote = item.get("quote")
            if isinstance(utterance_id, str) and isinstance(quote, str) and quote.strip():
                allowed.setdefault(utterance_id, set()).add(quote.strip())
    return allowed


def _semantic_chapter_audits(payload: object, chapter_cards: list[MemoirChapterCard]) -> list[dict[str, object]]:
    raw_items = payload if isinstance(payload, list) else []
    cards_by_chapter = {card.chapter_id: card for card in chapter_cards}
    allowed_by_chapter = {card.chapter_id: _chapter_card_allowed_refs(card) for card in chapter_cards}
    audits: list[dict[str, object]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        chapter_id = _compact_text(item.get("chapter_id"), limit=80)
        card = cards_by_chapter.get(chapter_id)
        if not card:
            continue
        card_json = card.card_json if isinstance(card.card_json, dict) else {}
        audits.append(
            {
                "chapter_id": chapter_id,
                "chapter_card_id": card.id,
                "summary": _compact_text(item.get("summary"), limit=300) or _compact_text(card_json.get("summary")),
                "gaps": _gap_items(item.get("gaps")) or _gap_items(card_json.get("gaps")),
                "next_questions": _string_list(item.get("next_questions"), limit=6, item_limit=100)
                or _string_list(card_json.get("next_questions"), limit=6, item_limit=100),
                "evidence_refs": _evidence_refs(item.get("evidence_refs"), allowed_by_chapter.get(chapter_id, {}))
                or list(card_json.get("evidence_refs") or [])[:4],
            }
        )
    if audits:
        return audits
    for card in chapter_cards:
        card_json = card.card_json if isinstance(card.card_json, dict) else {}
        audits.append(
            {
                "chapter_id": card.chapter_id,
                "chapter_card_id": card.id,
                "summary": _compact_text(card_json.get("summary")),
                "gaps": _gap_items(card_json.get("gaps")),
                "next_questions": _string_list(card_json.get("next_questions"), limit=6, item_limit=100),
                "evidence_refs": list(card_json.get("evidence_refs") or [])[:4],
            }
        )
    return audits


def _semantic_display(
    payload: dict,
    *,
    fallback: MemoirBookQualityAuditDisplayResponse,
) -> dict[str, str]:
    display = payload.get("display")
    raw = display if isinstance(display, dict) else {}
    return {
        "title": _compact_text(raw.get("title"), limit=80) or fallback.title,
        "summary": _compact_text(raw.get("summary"), limit=140) or fallback.summary,
        "detail": _compact_text(raw.get("detail"), limit=260) or fallback.detail,
    }


def _semantic_primary_action(
    payload: dict,
    *,
    fallback: MemoirBookQualityAuditPrimaryActionResponse,
) -> dict[str, object]:
    action = payload.get("primary_action")
    raw = action if isinstance(action, dict) else {}
    action_type = _compact_text(raw.get("type"), limit=60) or fallback.type
    if action_type not in VALID_PRIMARY_ACTION_TYPES:
        action_type = fallback.type
    return {
        "type": action_type,
        "cta_text": _compact_text(raw.get("cta_text"), limit=60) or fallback.cta_text,
        "chapter_id": _compact_text(raw.get("chapter_id"), limit=80) or fallback.chapter_id,
        "utterance_count": (
            raw.get("utterance_count") if isinstance(raw.get("utterance_count"), int) else fallback.utterance_count
        ),
    }


def _normalize_semantic_payload(
    payload: Any,
    *,
    rules_snapshot: MemoirBookQualityAuditSnapshot,
    chapter_cards: list[MemoirChapterCard],
    fallback_display: MemoirBookQualityAuditDisplayResponse,
    fallback_primary_action: MemoirBookQualityAuditPrimaryActionResponse,
) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ValueError("AI did not return a JSON object")
    overall_status = _compact_text(payload.get("overall_status"), limit=40) or rules_snapshot.overall_status
    if overall_status not in VALID_AUDIT_OVERALL_STATUSES:
        overall_status = rules_snapshot.overall_status
    return {
        "overall_status": overall_status,
        "summary_text": _compact_text(payload.get("summary_text"), limit=260) or rules_snapshot.summary_text,
        "display_json": _semantic_display(payload, fallback=fallback_display),
        "primary_action_json": _semantic_primary_action(payload, fallback=fallback_primary_action),
        "chapter_audits_json": _semantic_chapter_audits(payload.get("chapter_audits"), chapter_cards),
    }


def _semantic_prompt(
    *,
    rules_snapshot: MemoirBookQualityAuditSnapshot,
    chapter_cards: list[MemoirChapterCard],
) -> str:
    rules = {
        "overall_status": rules_snapshot.overall_status,
        "summary_text": rules_snapshot.summary_text,
        "display": rules_snapshot.display_json,
        "primary_action": rules_snapshot.primary_action_json,
        "chapter_audits": rules_snapshot.chapter_audits_json,
    }
    cards = [
        {
            "chapter_id": card.chapter_id,
            "chapter_card_id": card.id,
            "card": card.card_json,
        }
        for card in chapter_cards
    ]
    return f"""请基于规则审计结果和章节卡片，生成一次回忆录语义审计快照。

规则审计：
{json.dumps(rules, ensure_ascii=False, indent=2, default=str)}

章节卡片：
{json.dumps(cards, ensure_ascii=False, indent=2, default=str)}

请输出严格 JSON，不要 Markdown，不要解释。格式：
{{
  "overall_status": "needs_updates/collecting/healthy 等规则状态之一",
  "summary_text": "一句审计摘要",
  "display": {{"title": "展示标题", "summary": "展示摘要", "detail": "展示详情"}},
  "primary_action": {{"type": "open_chapter_workspace", "cta_text": "整理本章", "chapter_id": "可选章节 id", "utterance_count": 0}},
  "chapter_audits": [
    {{
      "chapter_id": "章节 id",
      "summary": "章节语义审计摘要",
      "gaps": [{{"type": "event_sequence", "severity": "medium", "message": "缺口"}}],
      "next_questions": ["下一轮追问"],
      "evidence_refs": [{{"utterance_id": "来源 id", "quote": "必须来自章节卡片 evidence_refs 的 quote"}}]
    }}
  ]
}}

要求：
- 只能根据规则审计和章节卡片判断，不新增事实。
- 不要直接改正文。
- evidence_refs 必须来自章节卡片已有 evidence_refs。
- 如果章节卡片显示缺口不严重，保持低干扰建议。
"""


async def _semantic_snapshot_from_llm(
    db: AsyncSession,
    *,
    project_id: str,
    rules_snapshot: MemoirBookQualityAuditSnapshot,
    chapter_cards: list[MemoirChapterCard],
    display: MemoirBookQualityAuditDisplayResponse,
    primary_action: MemoirBookQualityAuditPrimaryActionResponse,
    ai_model: str | None,
    ai_client: AITextClient | None,
) -> MemoirBookQualityAuditSnapshot:
    input_signature = _semantic_signature(rules_snapshot=rules_snapshot, chapter_cards=chapter_cards)
    existing = await _ready_semantic_snapshot(db, project_id=project_id, input_signature=input_signature)
    if existing:
        return existing

    client = ai_client
    try:
        if client is None:
            client = get_ai_service(ai_model)
        # Rules and chapter-card snapshots are durable cache entries. Commit
        # them before the semantic provider call so the long network wait does
        # not retain a SQLite write transaction.
        await db.commit()
        content = await client._call_llm(
            SEMANTIC_AUDIT_SYSTEM_PROMPT,
            _semantic_prompt(rules_snapshot=rules_snapshot, chapter_cards=chapter_cards),
            temperature=0.1,
            max_tokens=2200,
            timeout=90.0,
        )
        normalized = _normalize_semantic_payload(
            extract_json_payload(str(content)),
            rules_snapshot=rules_snapshot,
            chapter_cards=chapter_cards,
            fallback_display=display,
            fallback_primary_action=primary_action,
        )
        return await upsert_book_quality_audit_snapshot(
            db,
            project_id=project_id,
            engine=SEMANTIC_AUDIT_ENGINE,
            input_signature=input_signature,
            overall_status=str(normalized["overall_status"]),
            summary_text=str(normalized["summary_text"]),
            display_json=normalized["display_json"],
            primary_action_json=normalized["primary_action_json"],
            chapter_audits_json=normalized["chapter_audits_json"],
            source_card_ids_json=_source_card_ids_from_chapter_cards(chapter_cards),
            chapter_card_ids_json=_chapter_card_ids(chapter_cards),
        )
    except Exception as exc:
        logger.exception("memoir semantic quality audit failed project_id=%s", project_id)
        await db.rollback()
        await upsert_book_quality_audit_snapshot(
            db,
            project_id=project_id,
            engine=SEMANTIC_AUDIT_ENGINE,
            input_signature=input_signature,
            overall_status=rules_snapshot.overall_status,
            summary_text=rules_snapshot.summary_text,
            display_json=display.model_dump(mode="json"),
            primary_action_json=primary_action.model_dump(mode="json"),
            chapter_audits_json=[],
            source_card_ids_json=_source_card_ids_from_chapter_cards(chapter_cards),
            chapter_card_ids_json=_chapter_card_ids(chapter_cards),
            error_message=str(exc) or "semantic audit failed",
        )
        raise


async def try_semantic_snapshot(
    db: AsyncSession,
    *,
    project_id: str,
    rules_snapshot: MemoirBookQualityAuditSnapshot,
    chapter_items: list[MemoirBookQualityAuditChapterResponse],
    display: MemoirBookQualityAuditDisplayResponse,
    primary_action: MemoirBookQualityAuditPrimaryActionResponse,
    ai_model: str | None,
    chapter_card_ai_client: AITextClient | None,
    semantic_ai_client: AITextClient | None,
) -> MemoirBookQualityAuditSnapshot | None:
    target_items = [item for item in chapter_items if item.attention in {"high", "medium"}]
    if not target_items:
        return None
    chapter_cards: list[MemoirChapterCard] = []
    for item in target_items:
        card = await generate_or_reuse_chapter_card(
            db,
            project_id=project_id,
            chapter_id=item.chapter_id,
            ai_model=ai_model,
            ai_client=chapter_card_ai_client,
        )
        if card.status == MemoirSemanticCacheStatus.READY.value:
            chapter_cards.append(card)
    if not chapter_cards:
        return None
    try:
        return await _semantic_snapshot_from_llm(
            db,
            project_id=project_id,
            rules_snapshot=rules_snapshot,
            chapter_cards=chapter_cards,
            display=display,
            primary_action=primary_action,
            ai_model=ai_model,
            ai_client=semantic_ai_client,
        )
    except Exception:
        return None
