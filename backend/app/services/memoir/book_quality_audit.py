from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.domain.memoir.types import (
    MemoirAutomationEventStatus,
    MemoirAutomationEventType,
    MemoirEditingJobStatus,
    MemoirEditingJobType,
    MemoirSemanticCacheStatus,
)
from app.models.memoir import MemoirBookQualityAuditSnapshot, MemoirChapterCard, MemoirEditingJob
from app.models.projects import Project
from app.schemas.memoir_book import (
    MemoirBookQualityAuditChapterResponse,
    MemoirBookQualityAuditDisplayResponse,
    MemoirBookQualityAuditPrimaryActionResponse,
    MemoirBookQualityAuditPrimaryChapterResponse,
    MemoirBookQualityAuditRunRequest,
    MemoirBookQualityAuditRunResponse,
)
from app.schemas.memoir_voice import MemoirAutomationEventResponse, MemoirProjectProgressChapterResponse
from app.services.memoir import automation_events
from app.services.memoir import progress_snapshot
from app.services.ai_factory import get_ai_service
from app.services.ai_types import AITextClient
from app.services.json_utils import extract_json_payload
from app.services.memoir.chapter_cards import generate_or_reuse_chapter_card
from app.services.memoir.editing_jobs import EditingJobResult, enqueue_editing_job
from app.services.memoir.semantic_state import upsert_book_quality_audit_snapshot


RULES_AUDIT_ENGINE = "rules_v1"
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
    "revise_chapter",
    "generate_chapter_draft",
    "review_book",
    "none",
}
logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _json_signature(payload: dict[str, object]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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


async def schedule_project_quality_audit(
    db: AsyncSession,
    *,
    project_id: str,
    reason: str,
    run_after: datetime | None = None,
) -> MemoirEditingJob:
    settings = get_settings()
    scheduled_at = run_after or (_utcnow() + timedelta(seconds=max(0, settings.memoir_quality_audit_delay_seconds)))
    return await enqueue_editing_job(
        db,
        project_id=project_id,
        job_type=MemoirEditingJobType.QUALITY_AUDIT,
        payload_json={
            "reason": reason.strip() or "project_updated",
        },
        run_after=scheduled_at,
        dedupe_key=f"quality_audit:{project_id}",
        replace_completed=True,
    )


def _chapter_audit_item(
    item: MemoirProjectProgressChapterResponse,
) -> MemoirBookQualityAuditChapterResponse:
    if item.source_count <= 0:
        status = "needs_sources"
        attention = "high"
        reason = "章节还没有收录讲述素材"
        hint = f"可以请叙述者先聊聊「{item.title}」里最有画面感的一件事。"
    elif item.pending_source_count > 0:
        status = "needs_update"
        attention = "high"
        reason = f"章节有 {item.pending_source_count} 条新增讲述待补写"
        hint = f"可以告诉叙述者「{item.title}」又有新素材，稍后会补进正文。"
    elif item.draft_word_count <= 0:
        status = "needs_draft"
        attention = "high"
        reason = "章节还没有生成正文"
        hint = f"可以围绕「{item.title}」继续追问一两个关键细节，方便成文。"
    elif item.source_count < 2:
        status = "thin_sources"
        attention = "medium"
        reason = "章节素材数量偏少"
        hint = f"可以继续问「{item.title}」还有没有相关的人、地点或细节。"
    elif item.draft_word_count < max(160, item.source_count * 45):
        status = "thin_draft"
        attention = "medium"
        reason = "章节正文相对素材偏薄"
        hint = f"可以追问「{item.title}」里更具体的经过和感受。"
    else:
        status = "healthy"
        attention = "low"
        reason = "章节素材和正文已有基础"
        hint = f"「{item.title}」已有基础，可以在自然聊到时再补充。"

    return MemoirBookQualityAuditChapterResponse(
        chapter_id=item.chapter_id,
        title=item.title,
        sort_order=item.sort_order,
        source_count=item.source_count,
        pending_source_count=item.pending_source_count,
        draft_word_count=item.draft_word_count,
        status=status,
        attention=attention,
        reason=reason,
        interview_hint=hint,
    )


def _overall_status(
    *,
    chapter_items: list[MemoirBookQualityAuditChapterResponse],
    pending_utterance_count: int,
) -> str:
    if not chapter_items:
        return "needs_plan"
    if pending_utterance_count > 0 or any(item.status == "needs_sources" for item in chapter_items):
        return "needs_sources"
    if any(item.status == "needs_update" for item in chapter_items):
        return "needs_updates"
    if any(item.status == "needs_draft" for item in chapter_items):
        return "needs_drafts"
    if any(item.attention != "low" for item in chapter_items):
        return "collecting"
    return "healthy"


def _next_actions(
    *,
    overall_status: str,
    pending_utterance_count: int,
    chapter_items: list[MemoirBookQualityAuditChapterResponse],
) -> list[str]:
    actions: list[str] = []
    if overall_status == "needs_plan":
        actions.append("先根据已有讲述生成并确认章节规划")
    if pending_utterance_count > 0:
        actions.append(f"整理 {pending_utterance_count} 条尚未归章的讲述记录")
    for item in chapter_items:
        if item.attention == "low":
            continue
        actions.append(item.interview_hint)
        if len(actions) >= 3:
            break
    return actions


def _summary_text(
    *,
    overall_status: str,
    chapter_items: list[MemoirBookQualityAuditChapterResponse],
    pending_utterance_count: int,
) -> str:
    high_count = sum(1 for item in chapter_items if item.attention == "high")
    medium_count = sum(1 for item in chapter_items if item.attention == "medium")
    if overall_status == "needs_plan":
        return "当前还没有确认章节规划，需要先整理出成书结构。"
    if pending_utterance_count > 0:
        return f"当前有 {pending_utterance_count} 条讲述尚未归入章节。"
    if high_count:
        return f"当前有 {high_count} 个章节需要优先处理。"
    if medium_count:
        return f"当前有 {medium_count} 个章节还可以继续补充。"
    return "当前章节素材和正文已有较稳定基础。"


def _first_chapter_with_status(
    chapter_items: list[MemoirBookQualityAuditChapterResponse],
    *statuses: str,
) -> MemoirBookQualityAuditChapterResponse | None:
    wanted = set(statuses)
    return next((item for item in chapter_items if item.status in wanted), None)


def _first_attention_chapter(
    chapter_items: list[MemoirBookQualityAuditChapterResponse],
) -> MemoirBookQualityAuditChapterResponse | None:
    for attention in ("high", "medium", "low"):
        item = next((chapter for chapter in chapter_items if chapter.attention == attention), None)
        if item:
            return item
    return None


def _primary_chapter_response(
    item: MemoirBookQualityAuditChapterResponse | None,
) -> MemoirBookQualityAuditPrimaryChapterResponse | None:
    if item is None:
        return None
    return MemoirBookQualityAuditPrimaryChapterResponse(
        chapter_id=item.chapter_id,
        title=item.title,
        status=item.status,
        attention=item.attention,
        reason=item.reason,
    )


def _audit_display_contract(
    *,
    overall_status: str,
    chapter_items: list[MemoirBookQualityAuditChapterResponse],
    pending_utterance_count: int,
    next_actions: list[str],
) -> tuple[
    MemoirBookQualityAuditDisplayResponse,
    MemoirBookQualityAuditPrimaryActionResponse,
    MemoirBookQualityAuditPrimaryChapterResponse | None,
]:
    primary_item: MemoirBookQualityAuditChapterResponse | None = None
    if overall_status == "needs_plan":
        detail = (
            f"已有 {pending_utterance_count} 条讲述可以用于生成目录。"
            if pending_utterance_count > 0
            else "先确认目录后，后续讲述才能稳定归入章节。"
        )
        return (
            MemoirBookQualityAuditDisplayResponse(
                title="先整理成书结构",
                summary="还没有确认章节规划。",
                detail=detail,
            ),
            MemoirBookQualityAuditPrimaryActionResponse(
                type="generate_plan",
                cta_text="生成目录",
                utterance_count=pending_utterance_count,
            ),
            None,
        )

    if pending_utterance_count > 0:
        primary_item = _first_chapter_with_status(chapter_items, "needs_sources") or _first_attention_chapter(
            chapter_items
        )
        detail = "这些讲述还没有进入成书目录，先整理归章后再补写正文。"
        if primary_item and primary_item.status == "needs_sources":
            detail += f"「{primary_item.title}」也还缺少讲述素材。"
        return (
            MemoirBookQualityAuditDisplayResponse(
                title=f"有 {pending_utterance_count} 条讲述待整理",
                summary="新讲述还没有归入章节。",
                detail=detail,
            ),
            MemoirBookQualityAuditPrimaryActionResponse(
                type="assign_sources",
                cta_text="整理讲述",
                chapter_id=primary_item.chapter_id if primary_item else None,
                utterance_count=pending_utterance_count,
            ),
            _primary_chapter_response(primary_item),
        )

    if overall_status == "needs_sources":
        primary_item = _first_chapter_with_status(chapter_items, "needs_sources") or _first_attention_chapter(
            chapter_items
        )
        title = primary_item.title if primary_item else "未命名章节"
        return (
            MemoirBookQualityAuditDisplayResponse(
                title=f"「{title}」还缺讲述素材",
                summary="这一章还没有收录可成文的讲述。",
                detail=(primary_item.interview_hint if primary_item else "可以继续访谈，先补一件具体的人和事。"),
            ),
            MemoirBookQualityAuditPrimaryActionResponse(
                type="continue_interview",
                cta_text="继续访谈",
                chapter_id=primary_item.chapter_id if primary_item else None,
            ),
            _primary_chapter_response(primary_item),
        )

    if overall_status == "needs_updates":
        primary_item = _first_chapter_with_status(chapter_items, "needs_update") or _first_attention_chapter(
            chapter_items
        )
        title = primary_item.title if primary_item else "未命名章节"
        pending_count = primary_item.pending_source_count if primary_item else 0
        return (
            MemoirBookQualityAuditDisplayResponse(
                title=f"「{title}」有新内容待写入",
                summary=f"有 {pending_count} 条新讲述还没有补进正文。",
                detail="建议先补写本章，再继续补充人物、地点和细节。",
            ),
            MemoirBookQualityAuditPrimaryActionResponse(
                type="revise_chapter",
                cta_text="补写本章",
                chapter_id=primary_item.chapter_id if primary_item else None,
                utterance_count=pending_count,
            ),
            _primary_chapter_response(primary_item),
        )

    if overall_status == "needs_drafts":
        primary_item = _first_chapter_with_status(chapter_items, "needs_draft") or _first_attention_chapter(
            chapter_items
        )
        title = primary_item.title if primary_item else "未命名章节"
        source_count = primary_item.source_count if primary_item else 0
        return (
            MemoirBookQualityAuditDisplayResponse(
                title=f"「{title}」可以生成正文",
                summary=f"本章已有 {source_count} 条素材，但还没有章节正文。",
                detail="可以先生成一版正文，再根据新增讲述继续修订。",
            ),
            MemoirBookQualityAuditPrimaryActionResponse(
                type="generate_chapter_draft",
                cta_text="生成本章",
                chapter_id=primary_item.chapter_id if primary_item else None,
                utterance_count=source_count,
            ),
            _primary_chapter_response(primary_item),
        )

    if overall_status == "collecting":
        primary_item = _first_attention_chapter(chapter_items)
        title = primary_item.title if primary_item else "当前章节"
        if primary_item and primary_item.status == "thin_draft":
            summary = "正文已有基础，但相对素材还偏薄。"
            action_type = "revise_chapter"
            cta_text = "完善本章"
        else:
            summary = "素材已有基础，还可以继续补充关键细节。"
            action_type = "continue_interview"
            cta_text = "继续追问"
        return (
            MemoirBookQualityAuditDisplayResponse(
                title=f"「{title}」还可以继续丰富",
                summary=summary,
                detail=(primary_item.interview_hint if primary_item else "可以继续补充人物、地点、经过和感受。"),
            ),
            MemoirBookQualityAuditPrimaryActionResponse(
                type=action_type,
                cta_text=cta_text,
                chapter_id=primary_item.chapter_id if primary_item else None,
            ),
            _primary_chapter_response(primary_item),
        )

    primary_item = _first_attention_chapter(chapter_items)
    return (
        MemoirBookQualityAuditDisplayResponse(
            title="成书整理状态良好",
            summary="当前章节素材和正文已有稳定基础。",
            detail="可以继续录音补充新回忆，也可以进入全书稿整理。",
        ),
        MemoirBookQualityAuditPrimaryActionResponse(
            type="review_book",
            cta_text="查看成书",
            chapter_id=primary_item.chapter_id if primary_item else None,
        ),
        _primary_chapter_response(primary_item),
    )


def _event_metadata(
    *,
    snapshot_id: str,
    snapshot_engine: str,
    overall_status: str,
    pending_utterance_count: int,
    next_actions: list[str],
    display: MemoirBookQualityAuditDisplayResponse,
    primary_action: MemoirBookQualityAuditPrimaryActionResponse,
    primary_chapter: MemoirBookQualityAuditPrimaryChapterResponse | None,
    event_signature: str,
) -> dict[str, object]:
    return {
        "source": "book_quality_audit",
        "snapshot_id": snapshot_id,
        "snapshot_engine": snapshot_engine,
        automation_events.EVENT_SIGNATURE_KEY: event_signature,
        "overall_status": overall_status,
        "pending_utterance_count": pending_utterance_count,
        "next_actions": next_actions,
        "display": display.model_dump(mode="json"),
        "primary_action": primary_action.model_dump(mode="json"),
        "primary_chapter": primary_chapter.model_dump(mode="json") if primary_chapter else None,
    }


def _event_signature(
    *,
    overall_status: str,
    pending_utterance_count: int,
    chapter_items: list[MemoirBookQualityAuditChapterResponse],
) -> str:
    chapter_parts = [
        ":".join(
            [
                item.chapter_id,
                item.status,
                item.attention,
                str(item.source_count),
                str(item.pending_source_count),
                str(item.draft_word_count),
            ]
        )
        for item in sorted(chapter_items, key=lambda value: (value.sort_order, value.chapter_id))
    ]
    return "|".join(
        [
            "book_quality_audit",
            overall_status,
            f"pending={pending_utterance_count}",
            *chapter_parts,
        ]
    )


def _rules_snapshot_signature(
    *,
    overall_status: str,
    pending_utterance_count: int,
    chapter_items: list[MemoirBookQualityAuditChapterResponse],
) -> str:
    return _json_signature(
        {
            "engine": RULES_AUDIT_ENGINE,
            "event_signature": _event_signature(
                overall_status=overall_status,
                pending_utterance_count=pending_utterance_count,
                chapter_items=chapter_items,
            ),
            "chapters": [item.model_dump(mode="json") for item in chapter_items],
        }
    )


async def _write_rules_snapshot(
    db: AsyncSession,
    *,
    project_id: str,
    overall_status: str,
    summary_text: str,
    display: MemoirBookQualityAuditDisplayResponse,
    primary_action: MemoirBookQualityAuditPrimaryActionResponse,
    chapter_items: list[MemoirBookQualityAuditChapterResponse],
    pending_utterance_count: int,
) -> MemoirBookQualityAuditSnapshot:
    return await upsert_book_quality_audit_snapshot(
        db,
        project_id=project_id,
        engine=RULES_AUDIT_ENGINE,
        input_signature=_rules_snapshot_signature(
            overall_status=overall_status,
            pending_utterance_count=pending_utterance_count,
            chapter_items=chapter_items,
        ),
        overall_status=overall_status,
        summary_text=summary_text,
        display_json=display.model_dump(mode="json"),
        primary_action_json=primary_action.model_dump(mode="json"),
        chapter_audits_json=[item.model_dump(mode="json") for item in chapter_items],
    )


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
    return _json_signature(
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
    allowed_by_chapter = {
        card.chapter_id: _chapter_card_allowed_refs(card)
        for card in chapter_cards
    }
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
        "utterance_count": raw.get("utterance_count") if isinstance(raw.get("utterance_count"), int) else fallback.utterance_count,
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
  "primary_action": {{"type": "continue_interview", "cta_text": "继续追问", "chapter_id": "可选章节 id", "utterance_count": 0}},
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


async def _try_semantic_snapshot(
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


async def run_book_quality_audit(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookQualityAuditRunRequest,
    chapter_card_ai_client: AITextClient | None = None,
    semantic_ai_client: AITextClient | None = None,
) -> MemoirBookQualityAuditRunResponse:
    snapshot = await progress_snapshot.get_project_progress_snapshot(
        db,
        user_id=user_id,
        project_id=project_id,
    )
    chapter_items = [
        _chapter_audit_item(item)
        for item in sorted(snapshot.chapter_progress_items, key=lambda value: value.sort_order)
    ]
    overall_status = _overall_status(
        chapter_items=chapter_items,
        pending_utterance_count=snapshot.pending_utterance_count,
    )
    next_actions = _next_actions(
        overall_status=overall_status,
        pending_utterance_count=snapshot.pending_utterance_count,
        chapter_items=chapter_items,
    )
    summary_text = _summary_text(
        overall_status=overall_status,
        chapter_items=chapter_items,
        pending_utterance_count=snapshot.pending_utterance_count,
    )
    display, primary_action, primary_chapter = _audit_display_contract(
        overall_status=overall_status,
        chapter_items=chapter_items,
        pending_utterance_count=snapshot.pending_utterance_count,
        next_actions=next_actions,
    )
    rules_snapshot = await _write_rules_snapshot(
        db,
        project_id=project_id,
        overall_status=overall_status,
        summary_text=summary_text,
        display=display,
        primary_action=primary_action,
        chapter_items=chapter_items,
        pending_utterance_count=snapshot.pending_utterance_count,
    )
    selected_snapshot = rules_snapshot
    if req.semantic_audit:
        semantic_snapshot = await _try_semantic_snapshot(
            db,
            project_id=project_id,
            rules_snapshot=rules_snapshot,
            chapter_items=chapter_items,
            display=display,
            primary_action=primary_action,
            ai_model=req.ai_model,
            chapter_card_ai_client=chapter_card_ai_client,
            semantic_ai_client=semantic_ai_client,
        )
        if semantic_snapshot is not None:
            selected_snapshot = semantic_snapshot
            summary_text = semantic_snapshot.summary_text
            try:
                display = MemoirBookQualityAuditDisplayResponse.model_validate(semantic_snapshot.display_json)
            except ValueError:
                pass
            try:
                primary_action = MemoirBookQualityAuditPrimaryActionResponse.model_validate(
                    semantic_snapshot.primary_action_json
                )
            except ValueError:
                pass

    event_response: MemoirAutomationEventResponse | None = None
    if req.record_event:
        event_signature = _event_signature(
            overall_status=overall_status,
            pending_utterance_count=snapshot.pending_utterance_count,
            chapter_items=chapter_items,
        )
        event = await automation_events.record_or_update_automation_event(
            db,
            project_id=project_id,
            event_type=MemoirAutomationEventType.AUDIT_COMPLETED,
            status=MemoirAutomationEventStatus.COMPLETED,
            summary_text=summary_text,
            metadata_json=_event_metadata(
                snapshot_id=selected_snapshot.id,
                snapshot_engine=selected_snapshot.engine,
                overall_status=overall_status,
                pending_utterance_count=snapshot.pending_utterance_count,
                next_actions=next_actions,
                display=display,
                primary_action=primary_action,
                primary_chapter=primary_chapter,
                event_signature=event_signature,
            ),
        )
        await db.commit()
        event_response = automation_events.event_response(event)
    else:
        await db.commit()

    return MemoirBookQualityAuditRunResponse(
        project_id=project_id,
        overall_status=overall_status,
        summary_text=summary_text,
        next_actions=next_actions,
        chapter_items=chapter_items,
        display=display,
        primary_action=primary_action,
        primary_chapter=primary_chapter,
        event=event_response,
    )


async def run_book_quality_audit_job(db: AsyncSession, job: MemoirEditingJob) -> EditingJobResult:
    project = await db.get(Project, job.project_id)
    if not project or not project.user_id:
        return EditingJobResult(status=MemoirEditingJobStatus.SKIPPED, error_message="project unavailable")

    response = await run_book_quality_audit(
        db,
        user_id=project.user_id,
        project_id=job.project_id,
        req=MemoirBookQualityAuditRunRequest(record_event=True),
    )
    return EditingJobResult(
        result_json={
            "overall_status": response.overall_status,
            "summary_text": response.summary_text,
            "event_id": response.event.id if response.event else "",
            "reason": str((job.payload_json or {}).get("reason") or ""),
        },
    )
