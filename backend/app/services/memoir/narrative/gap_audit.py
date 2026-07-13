from __future__ import annotations

from hashlib import sha256
import json

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
from app.services.memoir.narrative import followup_jobs, gap_semantic, gaps
from app.services.memoir.narrative.gap_rules import (
    evaluate_deterministic_gaps,
    load_chapter_audit_context,
)


GAP_AUDIT_ENGINE_VERSION = "1"


def _input_signature(context, *, include_semantic_audit: bool, ai_model: str | None) -> str:
    payload = {
        "engine_version": GAP_AUDIT_ENGINE_VERSION,
        "chapter_id": context.chapter.id,
        "contract": (
            {
                "id": context.contract.id,
                "version": context.contract.version,
                "updated_at": context.contract.updated_at.isoformat(),
            }
            if context.contract
            else None
        ),
        "nodes": [
            [node.id, node.status, node.updated_at.isoformat()]
            for node in context.nodes
        ],
        "links": [
            [link.node_id, link.story_id, link.story_role, link.required]
            for link in context.links
        ],
        "stories": [
            [story.id, story.version, story.updated_at.isoformat()]
            for story in sorted(context.stories.values(), key=lambda item: item.id)
        ],
        "semantic": include_semantic_audit,
        "ai_model": ai_model or "default",
    }
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return sha256(encoded.encode("utf-8")).hexdigest()


async def enqueue_chapter_gap_audit(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    include_semantic_audit: bool,
    ai_model: str | None,
) -> MemoirEditingJob:
    context = await load_chapter_audit_context(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    signature = _input_signature(
        context,
        include_semantic_audit=include_semantic_audit,
        ai_model=ai_model,
    )
    return await enqueue_editing_job(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        job_type=MemoirEditingJobType.RECOMPUTE_CHAPTER_STRUCTURE_AUDIT,
        payload_json={
            "chapter_id": chapter_id,
            "include_semantic_audit": include_semantic_audit,
            "ai_model": ai_model,
            "input_signature": signature,
            "engine_version": GAP_AUDIT_ENGINE_VERSION,
            "source": "chapter_workspace",
        },
        dedupe_key=f"chapter-gap-audit:{project_id}:{chapter_id}:{signature}",
        replace_completed=True,
        retry_failed=True,
    )


async def run_chapter_gap_audit_job(
    db: AsyncSession,
    job: MemoirEditingJob,
    *,
    semantic_ai_client: AITextClient | None = None,
) -> EditingJobResult:
    payload = job.payload_json if isinstance(job.payload_json, dict) else {}
    chapter_id = str(payload.get("chapter_id") or job.chapter_id or "").strip()
    if not chapter_id:
        raise ValueError("chapter gap audit job requires chapter_id")
    context = await load_chapter_audit_context(
        db,
        project_id=job.project_id,
        chapter_id=chapter_id,
    )
    deterministic_candidates = evaluate_deterministic_gaps(context)
    _, deterministic_created = await gaps.upsert_gaps(
        db,
        project_id=job.project_id,
        candidates=deterministic_candidates,
    )
    semantic_candidates = []
    semantic_created = 0
    provider = None
    model = None
    include_semantic = bool(payload.get("include_semantic_audit", True))
    if include_semantic:
        try:
            semantic_candidates, provider, model = await gap_semantic.evaluate_semantic_gaps(
                context,
                ai_model=(str(payload["ai_model"]) if payload.get("ai_model") else None),
                ai_client=semantic_ai_client,
            )
            _, semantic_created = await gaps.upsert_gaps(
                db,
                project_id=job.project_id,
                candidates=semantic_candidates,
            )
        except Exception as exc:
            await followup_jobs.schedule_followup_task_generation(
                db,
                project_id=job.project_id,
                chapter_id=chapter_id,
                source="chapter_gap_audit_partial",
            )
            return EditingJobResult(
                status=MemoirEditingJobStatus.FAILED,
                result_json={
                    "chapter_id": chapter_id,
                    "deterministic_gap_count": len(deterministic_candidates),
                    "deterministic_created_count": deterministic_created,
                    "semantic_gap_count": 0,
                    "semantic_created_count": 0,
                    "partial_result": True,
                    "engine_version": GAP_AUDIT_ENGINE_VERSION,
                },
                error_message=str(exc),
            )

    await followup_jobs.schedule_followup_task_generation(
        db,
        project_id=job.project_id,
        chapter_id=chapter_id,
        source="chapter_gap_audit",
    )
    result = {
        "chapter_id": chapter_id,
        "deterministic_gap_count": len(deterministic_candidates),
        "deterministic_created_count": deterministic_created,
        "semantic_gap_count": len(semantic_candidates),
        "semantic_created_count": semantic_created,
        "partial_result": False,
        "provider": provider,
        "model": model,
        "engine_version": GAP_AUDIT_ENGINE_VERSION,
    }
    await automation_events.record_or_update_automation_event(
        db,
        project_id=job.project_id,
        event_type=MemoirAutomationEventType.AUDIT_COMPLETED,
        status=MemoirAutomationEventStatus.COMPLETED,
        chapter_id=chapter_id,
        utterance_ids=[],
        summary_text="章节完整性检查已更新。",
        event_signature=f"chapter_gap_audit:{job.id}",
        metadata_json={
            "source": "narrative_gap_audit",
            "editing_job_id": job.id,
            **result,
        },
    )
    return EditingJobResult(result_json=result)
