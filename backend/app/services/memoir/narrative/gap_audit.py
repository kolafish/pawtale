from __future__ import annotations

from hashlib import sha256
import json

from sqlalchemy import select
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
    ChapterAuditContext,
    evaluate_deterministic_gaps,
    load_chapter_audit_context,
)


GAP_AUDIT_ENGINE_VERSION = "1"


def chapter_gap_audit_content_signature(context: ChapterAuditContext) -> str:
    payload = {
        "engine_version": GAP_AUDIT_ENGINE_VERSION,
        "chapter_id": context.chapter.id,
        "contract": (
            {
                "id": context.contract.id,
                "version": context.contract.version,
                "status": context.contract.status,
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
        "story_claims": [
            [story_id, link.claim_id, link.narrative_role, link.is_core]
            for story_id, links in sorted(context.story_claims.items())
            for link in sorted(links, key=lambda item: (item.sort_order, item.id))
        ],
        "claims": [
            [
                claim.id,
                claim.updated_at.isoformat(),
                claim.verification_status,
                claim.publication_status,
                claim.superseded_by_claim_id,
                context.evidence_counts.get(claim.id, 0),
            ]
            for claim in sorted(context.claims.values(), key=lambda item: item.id)
        ],
        "slots": [
            [story_id, slot.slot_key, slot.status, slot.updated_at.isoformat()]
            for story_id, slots in sorted(context.slots.items())
            for slot in sorted(slots.values(), key=lambda item: item.slot_key)
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return sha256(encoded.encode("utf-8")).hexdigest()


def chapter_gap_audit_signature(
    context: ChapterAuditContext,
    *,
    include_semantic_audit: bool,
    ai_model: str | None,
) -> str:
    payload = {
        "content_signature": chapter_gap_audit_content_signature(context),
        "semantic": include_semantic_audit,
        "ai_model": ai_model or "default",
    }
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return sha256(encoded.encode("utf-8")).hexdigest()


async def current_chapter_gap_audit_job(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    content_signature: str,
) -> MemoirEditingJob | None:
    jobs = list(
        (
            await db.scalars(
                select(MemoirEditingJob)
                .where(
                    MemoirEditingJob.project_id == project_id,
                    MemoirEditingJob.chapter_id == chapter_id,
                    MemoirEditingJob.job_type
                    == MemoirEditingJobType.RECOMPUTE_CHAPTER_STRUCTURE_AUDIT.value,
                )
                .order_by(MemoirEditingJob.updated_at.desc(), MemoirEditingJob.created_at.desc())
                .limit(20)
                .execution_options(populate_existing=True)
            )
        ).all()
    )
    current = [
        job
        for job in jobs
        if isinstance(job.payload_json, dict)
        and job.payload_json.get("content_signature") == content_signature
    ]
    for statuses in (
        {MemoirEditingJobStatus.SUCCEEDED.value},
        {MemoirEditingJobStatus.PENDING.value, MemoirEditingJobStatus.RUNNING.value},
        {MemoirEditingJobStatus.FAILED.value, MemoirEditingJobStatus.SKIPPED.value},
    ):
        match = next((job for job in current if job.status in statuses), None)
        if match is not None:
            return match
    return None


def chapter_gap_audit_dedupe_key(
    *,
    project_id: str,
    chapter_id: str,
    signature: str,
) -> str:
    return f"chapter-gap-audit:{project_id}:{chapter_id}:{signature}"


async def enqueue_chapter_gap_audit(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    include_semantic_audit: bool,
    ai_model: str | None,
    source: str = "chapter_workspace",
) -> MemoirEditingJob:
    context = await load_chapter_audit_context(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    signature = chapter_gap_audit_signature(
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
            "content_signature": chapter_gap_audit_content_signature(context),
            "engine_version": GAP_AUDIT_ENGINE_VERSION,
            "source": source,
        },
        dedupe_key=chapter_gap_audit_dedupe_key(
            project_id=project_id,
            chapter_id=chapter_id,
            signature=signature,
        ),
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
    semantic_candidates = []
    provider = None
    model = None
    semantic_error: Exception | None = None
    include_semantic = bool(payload.get("include_semantic_audit", True))
    if include_semantic:
        try:
            semantic_candidates, provider, model = await gap_semantic.evaluate_semantic_gaps(
                context,
                ai_model=(str(payload["ai_model"]) if payload.get("ai_model") else None),
                ai_client=semantic_ai_client,
            )
        except Exception as exc:
            semantic_error = exc

    # Do not hold SQLite's writer lock while waiting for the semantic provider.
    _, deterministic_created = await gaps.upsert_gaps(
        db,
        project_id=job.project_id,
        candidates=deterministic_candidates,
    )
    deterministic_resolved = await gaps.reconcile_chapter_rule_gaps(
        db,
        project_id=job.project_id,
        chapter_id=chapter_id,
        complete_candidates=deterministic_candidates,
    )
    semantic_created = 0
    if semantic_error is not None:
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
                "deterministic_resolved_count": deterministic_resolved,
                "semantic_gap_count": 0,
                "semantic_created_count": 0,
                "partial_result": True,
                "engine_version": GAP_AUDIT_ENGINE_VERSION,
            },
            error_message=str(semantic_error),
        )
    if semantic_candidates:
        _, semantic_created = await gaps.upsert_gaps(
            db,
            project_id=job.project_id,
            candidates=semantic_candidates,
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
        "deterministic_resolved_count": deterministic_resolved,
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
