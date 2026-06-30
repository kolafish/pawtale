from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.domain.memoir.types import (
    MemoirAutomationEventStatus,
    MemoirAutomationEventType,
    MemoirEditingJobStatus,
    MemoirEditingJobType,
)
from app.models.memoir import MemoirEditingJob
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
from app.services.memoir.editing_jobs import EditingJobResult, enqueue_editing_job


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


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
    overall_status: str,
    pending_utterance_count: int,
    next_actions: list[str],
    chapter_items: list[MemoirBookQualityAuditChapterResponse],
    display: MemoirBookQualityAuditDisplayResponse,
    primary_action: MemoirBookQualityAuditPrimaryActionResponse,
    primary_chapter: MemoirBookQualityAuditPrimaryChapterResponse | None,
) -> dict[str, object]:
    return {
        "source": "book_quality_audit",
        automation_events.EVENT_SIGNATURE_KEY: _event_signature(
            overall_status=overall_status,
            pending_utterance_count=pending_utterance_count,
            chapter_items=chapter_items,
        ),
        "overall_status": overall_status,
        "pending_utterance_count": pending_utterance_count,
        "next_actions": next_actions,
        "chapter_items": [item.model_dump(mode="json") for item in chapter_items],
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
    display, primary_action, primary_chapter = _audit_display_contract(
        overall_status=overall_status,
        chapter_items=chapter_items,
        pending_utterance_count=snapshot.pending_utterance_count,
        next_actions=next_actions,
    )

    event_response: MemoirAutomationEventResponse | None = None
    if req.record_event:
        event = await automation_events.record_or_update_automation_event(
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
                display=display,
                primary_action=primary_action,
                primary_chapter=primary_chapter,
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
