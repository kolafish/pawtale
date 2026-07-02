from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import MemoirAutomationEventType
from app.models.memoir import (
    MemoirAutomationEvent,
    MemoirBookPlanChapter,
    MemoirBookQualityAuditSnapshot,
    MemoirChapterCard,
)
from app.services.memoir.automation_event_metadata import AUDIT_SNAPSHOT_ID_KEY, _metadata_string


def _compact_response_text(value: object, *, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit].rstrip() if len(text) > limit else text


def _response_string_list(value: object, *, limit: int = 4, item_limit: int = 120) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        text = _compact_response_text(item, limit=item_limit)
        if text and text not in items:
            items.append(text)
        if len(items) >= limit:
            break
    return items


def _response_gap_items(value: object, *, limit: int = 4) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    items: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        message = _compact_response_text(item.get("message"), limit=160)
        if not message:
            continue
        items.append(
            {
                "type": _compact_response_text(item.get("type"), limit=40),
                "severity": _compact_response_text(item.get("severity"), limit=20),
                "message": message,
            }
        )
        if len(items) >= limit:
            break
    return items


def _response_evidence_items(value: object, *, limit: int = 4) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    items: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        utterance_id = _compact_response_text(item.get("utterance_id"), limit=80)
        quote = _compact_response_text(item.get("quote"), limit=120)
        if not utterance_id and not quote:
            continue
        items.append({"utterance_id": utterance_id, "quote": quote})
        if len(items) >= limit:
            break
    return items


def _audit_card_response(
    card: MemoirChapterCard,
    *,
    chapter_titles: dict[str, str],
) -> dict[str, object]:
    card_json = card.card_json if isinstance(card.card_json, dict) else {}
    return {
        "id": card.id,
        "chapter_id": card.chapter_id,
        "chapter_title": chapter_titles.get(card.chapter_id, ""),
        "engine": card.engine,
        "summary": _compact_response_text(card_json.get("summary"), limit=220),
        "gaps": _response_gap_items(card_json.get("gaps")),
        "next_questions": _response_string_list(card_json.get("next_questions"), limit=6, item_limit=100),
        "source_card_ids": _response_string_list(card_json.get("source_card_ids"), limit=20, item_limit=80),
        "evidence_refs": _response_evidence_items(card_json.get("evidence_refs")),
    }


def _audit_chapter_response(
    item: object,
    *,
    chapter_titles: dict[str, str],
) -> dict[str, object] | None:
    if not isinstance(item, dict):
        return None
    chapter_id = _compact_response_text(item.get("chapter_id"), limit=80)
    return {
        "chapter_id": chapter_id,
        "chapter_card_id": _compact_response_text(item.get("chapter_card_id"), limit=80),
        "chapter_title": chapter_titles.get(chapter_id, ""),
        "summary": _compact_response_text(item.get("summary"), limit=220),
        "gaps": _response_gap_items(item.get("gaps")),
        "next_questions": _response_string_list(item.get("next_questions"), limit=6, item_limit=100),
        "evidence_refs": _response_evidence_items(item.get("evidence_refs")),
    }


def _audit_snapshot_response(
    snapshot: MemoirBookQualityAuditSnapshot,
    *,
    chapter_cards: list[MemoirChapterCard],
    chapter_titles: dict[str, str],
) -> dict[str, object]:
    cards = [_audit_card_response(card, chapter_titles=chapter_titles) for card in chapter_cards]
    chapter_audits = [
        item
        for item in (
            _audit_chapter_response(audit, chapter_titles=chapter_titles)
            for audit in (snapshot.chapter_audits_json or [])
        )
        if item is not None
    ]
    next_questions = _response_string_list(
        [question for card in cards for question in card.get("next_questions", [])],
        limit=6,
        item_limit=100,
    ) or _response_string_list(
        [question for audit in chapter_audits for question in audit.get("next_questions", [])],
        limit=6,
        item_limit=100,
    )
    return {
        "id": snapshot.id,
        "engine": snapshot.engine,
        "overall_status": snapshot.overall_status,
        "summary_text": _compact_response_text(snapshot.summary_text, limit=260),
        "display": snapshot.display_json or {},
        "primary_action": snapshot.primary_action_json or {},
        "next_questions": next_questions,
        "chapter_cards": cards,
        "chapter_audits": chapter_audits,
    }


async def audit_snapshot_metadata_by_event(
    db: AsyncSession,
    events: list[MemoirAutomationEvent],
) -> dict[str, dict[str, object]]:
    snapshot_ids = list(dict.fromkeys(
        _metadata_string(event.metadata_json or {}, AUDIT_SNAPSHOT_ID_KEY)
        for event in events
        if event.event_type == MemoirAutomationEventType.AUDIT_COMPLETED.value
    ))
    snapshot_ids = [snapshot_id for snapshot_id in snapshot_ids if snapshot_id]
    if not snapshot_ids:
        return {}

    project_ids = {event.project_id for event in events}
    snapshot_result = await db.execute(
        select(MemoirBookQualityAuditSnapshot).where(
            MemoirBookQualityAuditSnapshot.id.in_(snapshot_ids),
            MemoirBookQualityAuditSnapshot.project_id.in_(project_ids),
        )
    )
    snapshots = {snapshot.id: snapshot for snapshot in snapshot_result.scalars().all()}
    chapter_card_ids = list(dict.fromkeys(
        card_id
        for snapshot in snapshots.values()
        for card_id in _response_string_list(snapshot.chapter_card_ids_json, limit=50, item_limit=80)
    ))
    chapter_cards: list[MemoirChapterCard] = []
    if chapter_card_ids:
        card_result = await db.execute(
            select(MemoirChapterCard).where(
                MemoirChapterCard.id.in_(chapter_card_ids),
                MemoirChapterCard.project_id.in_(project_ids),
            )
        )
        cards_by_id = {card.id: card for card in card_result.scalars().all()}
        for card_id in chapter_card_ids:
            card = cards_by_id.get(card_id)
            if card:
                chapter_cards.append(card)

    chapter_ids = list(dict.fromkeys(
        [card.chapter_id for card in chapter_cards]
        + [
            _compact_response_text(item.get("chapter_id"), limit=80)
            for snapshot in snapshots.values()
            for item in (snapshot.chapter_audits_json or [])
            if isinstance(item, dict)
        ]
    ))
    chapter_ids = [chapter_id for chapter_id in chapter_ids if chapter_id]
    chapter_titles: dict[str, str] = {}
    if chapter_ids:
        chapter_result = await db.execute(
            select(MemoirBookPlanChapter.id, MemoirBookPlanChapter.title).where(
                MemoirBookPlanChapter.id.in_(chapter_ids),
                MemoirBookPlanChapter.project_id.in_(project_ids),
            )
        )
        chapter_titles = {chapter_id: title for chapter_id, title in chapter_result.all()}

    cards_by_snapshot_id: dict[str, list[MemoirChapterCard]] = {}
    cards_by_id = {card.id: card for card in chapter_cards}
    for snapshot in snapshots.values():
        cards_by_snapshot_id[snapshot.id] = [
            cards_by_id[card_id]
            for card_id in _response_string_list(snapshot.chapter_card_ids_json, limit=50, item_limit=80)
            if card_id in cards_by_id
        ]

    metadata_by_event: dict[str, dict[str, object]] = {}
    for event in events:
        snapshot_id = _metadata_string(event.metadata_json or {}, AUDIT_SNAPSHOT_ID_KEY)
        snapshot = snapshots.get(snapshot_id)
        if not snapshot or snapshot.project_id != event.project_id:
            continue
        metadata_by_event[event.id] = _audit_snapshot_response(
            snapshot,
            chapter_cards=cards_by_snapshot_id.get(snapshot.id, []),
            chapter_titles=chapter_titles,
        )
    return metadata_by_event
