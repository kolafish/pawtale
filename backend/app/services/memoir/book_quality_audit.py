from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

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
    MemoirBookQualityAuditDisplayResponse,
    MemoirBookQualityAuditPrimaryActionResponse,
    MemoirBookQualityAuditPrimaryChapterResponse,
    MemoirBookQualityAuditRunRequest,
    MemoirBookQualityAuditRunResponse,
)
from app.schemas.memoir_voice import MemoirAutomationEventResponse
from app.services.ai_types import AITextClient
from app.services.memoir import automation_events
from app.services.memoir import progress_snapshot
from app.services.memoir.book_quality_audit_rules import (
    audit_display_contract,
    chapter_audit_item,
    event_signature as build_event_signature,
    next_actions as build_next_actions,
    overall_status as determine_overall_status,
    summary_text as build_summary_text,
    write_rules_snapshot,
)
from app.services.memoir.book_quality_audit_semantic import try_semantic_snapshot
from app.services.memoir.editing_jobs import EditingJobResult, enqueue_editing_job


def _payload_bool(payload: dict[str, Any], key: str, default: bool) -> bool:
    value = payload.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _payload_optional_string(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)



async def schedule_project_quality_audit(
    db: AsyncSession,
    *,
    project_id: str,
    reason: str,
    run_after: datetime | None = None,
    semantic_audit: bool = True,
    ai_model: str | None = None,
) -> MemoirEditingJob:
    settings = get_settings()
    scheduled_at = run_after or (_utcnow() + timedelta(seconds=max(0, settings.memoir_quality_audit_delay_seconds)))
    payload_json: dict[str, Any] = {
        "reason": reason.strip() or "project_updated",
        "semantic_audit": semantic_audit,
    }
    if ai_model:
        payload_json["ai_model"] = ai_model
    return await enqueue_editing_job(
        db,
        project_id=project_id,
        job_type=MemoirEditingJobType.QUALITY_AUDIT,
        payload_json=payload_json,
        run_after=scheduled_at,
        dedupe_key=f"quality_audit:{project_id}",
        replace_completed=True,
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
) -> dict[str, object]:
    return {
        "source": "book_quality_audit",
        "snapshot_id": snapshot_id,
        "snapshot_engine": snapshot_engine,
        "overall_status": overall_status,
        "pending_utterance_count": pending_utterance_count,
        "next_actions": next_actions,
        "display": display.model_dump(mode="json"),
        "primary_action": primary_action.model_dump(mode="json"),
        "primary_chapter": primary_chapter.model_dump(mode="json") if primary_chapter else None,
    }


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
        chapter_audit_item(item)
        for item in sorted(snapshot.chapter_progress_items, key=lambda value: value.sort_order)
        if item.include_in_book
    ]
    audit_status = determine_overall_status(
        chapter_items=chapter_items,
        pending_utterance_count=snapshot.pending_utterance_count,
    )
    actions = build_next_actions(
        overall_status=audit_status,
        pending_utterance_count=snapshot.pending_utterance_count,
        chapter_items=chapter_items,
    )
    audit_summary = build_summary_text(
        overall_status=audit_status,
        chapter_items=chapter_items,
        pending_utterance_count=snapshot.pending_utterance_count,
    )
    display, primary_action, primary_chapter = audit_display_contract(
        overall_status=audit_status,
        chapter_items=chapter_items,
        pending_utterance_count=snapshot.pending_utterance_count,
    )
    rules_snapshot = await write_rules_snapshot(
        db,
        project_id=project_id,
        overall_status=audit_status,
        summary_text=audit_summary,
        display=display,
        primary_action=primary_action,
        chapter_items=chapter_items,
        pending_utterance_count=snapshot.pending_utterance_count,
    )
    selected_snapshot = rules_snapshot
    if req.semantic_audit:
        semantic_snapshot = await try_semantic_snapshot(
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
            audit_summary = semantic_snapshot.summary_text
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
        audit_event_signature = build_event_signature(
            overall_status=audit_status,
            pending_utterance_count=snapshot.pending_utterance_count,
            chapter_items=chapter_items,
        )
        event = await automation_events.record_or_update_automation_event(
            db,
            project_id=project_id,
            event_type=MemoirAutomationEventType.AUDIT_COMPLETED,
            status=MemoirAutomationEventStatus.COMPLETED,
            summary_text=audit_summary,
            event_signature=audit_event_signature,
            metadata_json=_event_metadata(
                snapshot_id=selected_snapshot.id,
                snapshot_engine=selected_snapshot.engine,
                overall_status=audit_status,
                pending_utterance_count=snapshot.pending_utterance_count,
                next_actions=actions,
                display=display,
                primary_action=primary_action,
                primary_chapter=primary_chapter,
            ),
        )
        await db.commit()
        event_response = automation_events.event_response(event)
    else:
        await db.commit()

    return MemoirBookQualityAuditRunResponse(
        project_id=project_id,
        overall_status=audit_status,
        summary_text=audit_summary,
        next_actions=actions,
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

    payload = job.payload_json or {}
    semantic_audit = _payload_bool(payload, "semantic_audit", True)
    ai_model = _payload_optional_string(payload, "ai_model")
    response = await run_book_quality_audit(
        db,
        user_id=project.user_id,
        project_id=job.project_id,
        req=MemoirBookQualityAuditRunRequest(
            record_event=True,
            semantic_audit=semantic_audit,
            ai_model=ai_model,
        ),
    )
    event_metadata = response.event.metadata_json if response.event else {}
    return EditingJobResult(
        result_json={
            "overall_status": response.overall_status,
            "summary_text": response.summary_text,
            "event_id": response.event.id if response.event else "",
            "reason": str(payload.get("reason") or ""),
            "semantic_audit": semantic_audit,
            "snapshot_engine": str(event_metadata.get("snapshot_engine") or ""),
        },
    )
