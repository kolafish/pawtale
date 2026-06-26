from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import MemoirAutomationEventStatus, MemoirAutomationEventType
from app.schemas.memoir_book import (
    MemoirBookQualityAuditChapterResponse,
    MemoirBookQualityAuditRunRequest,
    MemoirBookQualityAuditRunResponse,
)
from app.schemas.memoir_voice import MemoirAutomationEventResponse, MemoirProjectProgressChapterResponse
from app.services.memoir import automation_events
from app.services.memoir import progress_snapshot


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


def _event_metadata(
    *,
    overall_status: str,
    pending_utterance_count: int,
    next_actions: list[str],
    chapter_items: list[MemoirBookQualityAuditChapterResponse],
) -> dict[str, object]:
    return {
        "source": "book_quality_audit",
        "overall_status": overall_status,
        "pending_utterance_count": pending_utterance_count,
        "next_actions": next_actions,
        "chapter_items": [item.model_dump(mode="json") for item in chapter_items],
    }


async def run_book_quality_audit(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookQualityAuditRunRequest,
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

    event_response: MemoirAutomationEventResponse | None = None
    if req.record_event:
        event = await automation_events.record_automation_event(
            db,
            project_id=project_id,
            event_type=MemoirAutomationEventType.AUDIT_COMPLETED,
            status=MemoirAutomationEventStatus.COMPLETED,
            summary_text=summary_text,
            metadata_json=_event_metadata(
                overall_status=overall_status,
                pending_utterance_count=snapshot.pending_utterance_count,
                next_actions=next_actions,
                chapter_items=chapter_items,
            ),
        )
        await db.commit()
        event_response = automation_events.event_response(event)

    return MemoirBookQualityAuditRunResponse(
        project_id=project_id,
        overall_status=overall_status,
        summary_text=summary_text,
        next_actions=next_actions,
        chapter_items=chapter_items,
        event=event_response,
    )
