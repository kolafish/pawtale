from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import (
    MemoirAutomationEventStatus,
    MemoirAutomationEventType,
    MemoirEditingJobStatus,
    MemoirEditingJobType,
)
from app.models.memoir import MemoirEditingJob
from app.services.ai_types import AITextClient
from app.services.memoir import automation_events
from app.services.memoir.editing_jobs import EditingJobResult, enqueue_editing_job
from app.services.memoir.narrative import book_consistency_semantic, gaps
from app.services.memoir.narrative.book_consistency_context import (
    BOOK_CONSISTENCY_ENGINE_VERSION,
    book_consistency_signature,
    load_book_consistency_context,
)
from app.services.memoir.narrative.book_consistency_rules import (
    evaluate_book_consistency_rules,
)
from app.services.memoir.narrative.gap_rules import evaluate_deterministic_gaps


async def enqueue_book_consistency_audit(
    db: AsyncSession,
    *,
    project_id: str,
    include_semantic_audit: bool,
    ai_model: str | None,
) -> MemoirEditingJob:
    context = await load_book_consistency_context(db, project_id=project_id)
    signature = book_consistency_signature(context)
    return await enqueue_editing_job(
        db,
        project_id=project_id,
        job_type=MemoirEditingJobType.AUDIT_BOOK_CONSISTENCY,
        payload_json={
            "include_semantic_audit": include_semantic_audit,
            "ai_model": ai_model,
            "input_signature": signature,
            "engine_version": BOOK_CONSISTENCY_ENGINE_VERSION,
            "source": "book_consistency",
        },
        dedupe_key=f"book-consistency:{project_id}:{signature}:{int(include_semantic_audit)}:{ai_model or 'default'}",
        replace_completed=True,
        retry_failed=True,
    )


async def run_book_consistency_audit_job(
    db: AsyncSession,
    job: MemoirEditingJob,
    *,
    semantic_ai_client: AITextClient | None = None,
) -> EditingJobResult:
    payload = job.payload_json if isinstance(job.payload_json, dict) else {}
    context = await load_book_consistency_context(db, project_id=job.project_id)
    input_signature = book_consistency_signature(context)
    chapter_candidates = [
        candidate
        for chapter in context.chapters
        for candidate in evaluate_deterministic_gaps(chapter)
    ]
    _, chapter_created = await gaps.upsert_gaps(
        db,
        project_id=job.project_id,
        candidates=chapter_candidates,
    )
    book_candidates = evaluate_book_consistency_rules(context)
    _, book_created = await gaps.upsert_gaps(
        db,
        project_id=job.project_id,
        candidates=book_candidates,
    )
    obsolete_rule_count = await gaps.reconcile_project_rule_gaps(
        db,
        project_id=job.project_id,
        complete_candidates=[*chapter_candidates, *book_candidates],
    )
    semantic_candidates = []
    semantic_created = 0
    provider = None
    model = None
    include_semantic = bool(payload.get("include_semantic_audit", True))
    if include_semantic:
        try:
            semantic_candidates, provider, model = (
                await book_consistency_semantic.evaluate_book_semantic_gaps(
                    context,
                    ai_model=(str(payload["ai_model"]) if payload.get("ai_model") else None),
                    ai_client=semantic_ai_client,
                )
            )
            _, semantic_created = await gaps.upsert_gaps(
                db,
                project_id=job.project_id,
                candidates=semantic_candidates,
            )
        except Exception as exc:
            return EditingJobResult(
                status=MemoirEditingJobStatus.FAILED,
                result_json={
                    "input_signature": input_signature,
                    "chapter_gap_count": len(chapter_candidates),
                    "chapter_created_count": chapter_created,
                    "book_rule_gap_count": len(book_candidates),
                    "book_rule_created_count": book_created,
                    "obsolete_rule_gap_count": obsolete_rule_count,
                    "semantic_gap_count": 0,
                    "semantic_created_count": 0,
                    "semantic_audit_completed": False,
                    "partial_result": True,
                    "engine_version": BOOK_CONSISTENCY_ENGINE_VERSION,
                },
                error_message=str(exc),
            )

    result = {
        "input_signature": input_signature,
        "chapter_gap_count": len(chapter_candidates),
        "chapter_created_count": chapter_created,
        "book_rule_gap_count": len(book_candidates),
        "book_rule_created_count": book_created,
        "obsolete_rule_gap_count": obsolete_rule_count,
        "semantic_gap_count": len(semantic_candidates),
        "semantic_created_count": semantic_created,
        "semantic_audit_completed": include_semantic,
        "partial_result": False,
        "provider": provider,
        "model": model,
        "engine_version": BOOK_CONSISTENCY_ENGINE_VERSION,
    }
    await automation_events.record_or_update_automation_event(
        db,
        project_id=job.project_id,
        event_type=MemoirAutomationEventType.AUDIT_COMPLETED,
        status=MemoirAutomationEventStatus.COMPLETED,
        chapter_id=None,
        utterance_ids=[],
        editing_job_id=job.id,
        summary_text="全书人物、时间和事实来源检查已更新。",
        event_signature=f"book_consistency_audit:{job.id}",
        metadata_json={
            "source": "book_consistency_audit",
            "editing_job_id": job.id,
            **result,
        },
    )
    return EditingJobResult(result_json=result)
