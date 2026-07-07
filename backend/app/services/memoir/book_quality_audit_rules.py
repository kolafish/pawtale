from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memoir import MemoirBookQualityAuditSnapshot
from app.schemas.memoir_book import (
    MemoirBookQualityAuditChapterResponse,
    MemoirBookQualityAuditDisplayResponse,
    MemoirBookQualityAuditPrimaryActionResponse,
    MemoirBookQualityAuditPrimaryChapterResponse,
)
from app.schemas.memoir_voice import MemoirProjectProgressChapterResponse
from app.services.memoir.semantic_state import upsert_book_quality_audit_snapshot


RULES_AUDIT_ENGINE = "rules_v1"


@dataclass(frozen=True)
class ChapterAuditRule:
    status: str
    attention: str
    applies: Callable[[MemoirProjectProgressChapterResponse], bool]
    reason: Callable[[MemoirProjectProgressChapterResponse], str]
    interview_hint: Callable[[MemoirProjectProgressChapterResponse], str]


CHAPTER_AUDIT_RULES: tuple[ChapterAuditRule, ...] = (
    ChapterAuditRule(
        status="needs_sources",
        attention="high",
        applies=lambda item: item.source_count <= 0,
        reason=lambda item: "章节还没有收录讲述素材",
        interview_hint=lambda item: f"可以请叙述者先聊聊「{item.title}」里最有画面感的一件事。",
    ),
    ChapterAuditRule(
        status="needs_update",
        attention="high",
        applies=lambda item: item.pending_source_count > 0,
        reason=lambda item: f"章节有 {item.pending_source_count} 条新增讲述待补写",
        interview_hint=lambda item: f"可以告诉叙述者「{item.title}」又有新素材，稍后会补进正文。",
    ),
    ChapterAuditRule(
        status="needs_draft",
        attention="high",
        applies=lambda item: item.draft_word_count <= 0,
        reason=lambda item: "章节还没有生成正文",
        interview_hint=lambda item: f"可以围绕「{item.title}」继续追问一两个关键细节，方便成文。",
    ),
    ChapterAuditRule(
        status="thin_sources",
        attention="medium",
        applies=lambda item: item.source_count < 2,
        reason=lambda item: "章节素材数量偏少",
        interview_hint=lambda item: f"可以继续问「{item.title}」还有没有相关的人、地点或细节。",
    ),
    ChapterAuditRule(
        status="thin_draft",
        attention="medium",
        applies=lambda item: item.draft_word_count < max(160, item.source_count * 45),
        reason=lambda item: "章节正文相对素材偏薄",
        interview_hint=lambda item: f"可以追问「{item.title}」里更具体的经过和感受。",
    ),
    ChapterAuditRule(
        status="healthy",
        attention="low",
        applies=lambda item: True,
        reason=lambda item: "章节素材和正文已有基础",
        interview_hint=lambda item: f"「{item.title}」已有基础，可以在自然聊到时再补充。",
    ),
)


def _json_signature(payload: dict[str, object]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def chapter_audit_item(
    item: MemoirProjectProgressChapterResponse,
) -> MemoirBookQualityAuditChapterResponse:
    rule = next(rule for rule in CHAPTER_AUDIT_RULES if rule.applies(item))

    return MemoirBookQualityAuditChapterResponse(
        chapter_id=item.chapter_id,
        title=item.title,
        sort_order=item.sort_order,
        source_count=item.source_count,
        pending_source_count=item.pending_source_count,
        draft_word_count=item.draft_word_count,
        status=rule.status,
        attention=rule.attention,
        reason=rule.reason(item),
        interview_hint=rule.interview_hint(item),
    )


def overall_status(
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


def next_actions(
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


def summary_text(
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


def audit_display_contract(
    *,
    overall_status: str,
    chapter_items: list[MemoirBookQualityAuditChapterResponse],
    pending_utterance_count: int,
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


def event_signature(
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
            "event_signature": event_signature(
                overall_status=overall_status,
                pending_utterance_count=pending_utterance_count,
                chapter_items=chapter_items,
            ),
            "chapters": [item.model_dump(mode="json") for item in chapter_items],
        }
    )


async def write_rules_snapshot(
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
