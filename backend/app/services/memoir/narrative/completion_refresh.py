from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import (
    MemoirChapterNodeStatus,
    MemoirFollowupTaskStatus,
    MemoirNarrativeActorType,
    MemoirNarrativeGapRecommendedAction,
    MemoirNarrativeGapStatus,
)
from app.domain.memoir.types import (
    MemoirAutomationEventStatus,
    MemoirAutomationEventType,
    MemoirEditingJobStatus,
    MemoirEditingJobType,
)
from app.models.memoir import MemoirEditingJob
from app.models.memoir_narrative import (
    MemoirFollowupTask,
    MemoirNarrativeGap,
)
from app.schemas.memoir_narrative import MemoirNarrativeGapCandidate
from app.services.ai_types import AITextClient
from app.services.memoir import automation_events
from app.services.memoir.editing_jobs import EditingJobResult, enqueue_editing_job
from app.services.memoir.narrative import (
    completion_planner,
    draft_blocks,
    draft_generation_jobs,
    followup_planner,
    gap_semantic,
    gaps,
)
from app.services.memoir.narrative.gap_rules import (
    ChapterAuditContext,
    evaluate_deterministic_gaps,
    load_chapter_audit_context,
)


COMPLETION_REFRESH_ENGINE_VERSION = "2"


def _persistent_candidates(
    candidates: list[MemoirNarrativeGapCandidate],
) -> list[MemoirNarrativeGapCandidate]:
    return [
        candidate
        for candidate in candidates
        if completion_planner.plan_gap_action(candidate).recommended_action
        != MemoirNarrativeGapRecommendedAction.EDITORIAL
    ]


def _editorial_guidance(
    candidates: list[MemoirNarrativeGapCandidate],
) -> list[str]:
    return list(
        dict.fromkeys(
            "：".join(
                part
                for part in (candidate.title.strip(), candidate.description.strip())
                if part
            )
            for candidate in candidates
            if completion_planner.plan_gap_action(candidate).recommended_action
            == MemoirNarrativeGapRecommendedAction.EDITORIAL
        )
    )[:12]


async def _user_confirmation_guidance(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> list[str]:
    confirmed_gaps = list(
        (
            await db.scalars(
                select(MemoirNarrativeGap)
                .where(
                    MemoirNarrativeGap.project_id == project_id,
                    MemoirNarrativeGap.status == MemoirNarrativeGapStatus.RESOLVED.value,
                    MemoirNarrativeGap.resolved_by
                    == MemoirNarrativeActorType.USER.value,
                    MemoirNarrativeGap.recommended_action
                    == MemoirNarrativeGapRecommendedAction.MANUAL_REVIEW.value,
                    MemoirNarrativeGap.resolution_note != "",
                    or_(
                        MemoirNarrativeGap.primary_chapter_id == chapter_id,
                        MemoirNarrativeGap.chapters.any(chapter_id=chapter_id),
                    ),
                )
                .order_by(MemoirNarrativeGap.resolved_at.desc())
                .limit(12)
            )
        ).all()
    )
    return [
        f"用户确认“{gap.title}”的准确说法：{gap.resolution_note.strip()}"
        for gap in confirmed_gaps
        if gap.resolution_note.strip()
    ]


def chapter_completion_content_signature(context: ChapterAuditContext) -> str:
    payload = {
        "engine_version": COMPLETION_REFRESH_ENGINE_VERSION,
        "gap_action_policy_version": completion_planner.GAP_ACTION_POLICY_VERSION,
        "followup_planner_version": followup_planner.FOLLOWUP_PLANNER_VERSION,
        "chapter_id": context.chapter.id,
        "chapter_title": context.chapter.title,
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
        "draft_blocks": [
            [
                block.id,
                block.node_id,
                block.block_role,
                block.sort_order,
                block.current_version_id,
                block.editorial_status,
                block.freshness_status,
                list(block.claim_ids),
                list(block.story_ids),
                sha256(block.content.encode("utf-8")).hexdigest(),
            ]
            for block in context.draft_blocks
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return sha256(encoded.encode("utf-8")).hexdigest()


def chapter_completion_refresh_signature(
    context: ChapterAuditContext,
    *,
    include_semantic_audit: bool,
    ai_model: str | None,
) -> str:
    payload = {
        "content_signature": chapter_completion_content_signature(context),
        "semantic": include_semantic_audit,
        "ai_model": ai_model or "default",
    }
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return sha256(encoded.encode("utf-8")).hexdigest()


def chapter_completion_refresh_dedupe_key(
    *,
    project_id: str,
    chapter_id: str,
    signature: str,
) -> str:
    return f"chapter-completion-refresh:{project_id}:{chapter_id}:{signature}"


async def enqueue_chapter_completion_refresh(
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
    return await _enqueue_chapter_completion_refresh_for_context(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        context=context,
        include_semantic_audit=include_semantic_audit,
        ai_model=ai_model,
        source=source,
        replace_completed=True,
    )


async def _enqueue_chapter_completion_refresh_for_context(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    context: ChapterAuditContext,
    include_semantic_audit: bool,
    ai_model: str | None,
    source: str,
    replace_completed: bool,
) -> MemoirEditingJob:
    signature = chapter_completion_refresh_signature(
        context,
        include_semantic_audit=include_semantic_audit,
        ai_model=ai_model,
    )
    return await enqueue_editing_job(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        job_type=MemoirEditingJobType.REFRESH_CHAPTER_COMPLETION,
        payload_json={
            "chapter_id": chapter_id,
            "include_semantic_audit": include_semantic_audit,
            "ai_model": ai_model,
            "input_signature": signature,
            "content_signature": chapter_completion_content_signature(context),
            "engine_version": COMPLETION_REFRESH_ENGINE_VERSION,
            "source": source,
        },
        dedupe_key=chapter_completion_refresh_dedupe_key(
            project_id=project_id,
            chapter_id=chapter_id,
            signature=signature,
        ),
        replace_completed=replace_completed,
        retry_failed=True,
    )


async def run_chapter_completion_refresh_job(
    db: AsyncSession,
    job: MemoirEditingJob,
    *,
    semantic_ai_client: AITextClient | None = None,
) -> EditingJobResult:
    payload = job.payload_json if isinstance(job.payload_json, dict) else {}
    chapter_id = str(payload.get("chapter_id") or job.chapter_id or "").strip()
    if not chapter_id:
        raise ValueError("chapter completion refresh job requires chapter_id")
    project_id = job.project_id
    job_id = job.id
    context = await load_chapter_audit_context(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    content_signature = chapter_completion_content_signature(context)
    expected_content_signature = str(payload.get("content_signature") or "").strip()
    if expected_content_signature and content_signature != expected_content_signature:
        replacement_job = await _enqueue_chapter_completion_refresh_for_context(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
            context=context,
            include_semantic_audit=bool(
                payload.get("include_semantic_audit", True)
            ),
            ai_model=(str(payload["ai_model"]) if payload.get("ai_model") else None),
            source="chapter_content_changed_before_refresh",
            replace_completed=False,
        )
        return EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            result_json={
                "chapter_id": chapter_id,
                "published": False,
                "skip_reason": "chapter_content_changed_before_analysis",
                "replacement_job_id": replacement_job.id,
                "engine_version": COMPLETION_REFRESH_ENGINE_VERSION,
            },
            error_message="chapter content changed before refresh analysis",
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

    if semantic_error is not None:
        return EditingJobResult(
            status=MemoirEditingJobStatus.FAILED,
            result_json={
                "chapter_id": chapter_id,
                "deterministic_gap_count": len(deterministic_candidates),
                "semantic_gap_count": 0,
                "published": False,
                "failed_stage": "semantic_analysis",
                "engine_version": COMPLETION_REFRESH_ENGINE_VERSION,
            },
            error_message=str(semantic_error),
        )

    # End the analysis transaction before validating and atomically publishing
    # the planned Gap/Task revision.
    await db.rollback()
    db.expire_all()
    await db.refresh(job)
    current_context = await load_chapter_audit_context(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    current_content_signature = chapter_completion_content_signature(current_context)
    if current_content_signature != content_signature:
        replacement_job = await _enqueue_chapter_completion_refresh_for_context(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
            context=current_context,
            include_semantic_audit=include_semantic,
            ai_model=(str(payload["ai_model"]) if payload.get("ai_model") else None),
            source="chapter_content_changed_during_refresh",
            replace_completed=False,
        )
        return EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            result_json={
                "chapter_id": chapter_id,
                "published": False,
                "skip_reason": "chapter_content_changed_during_analysis",
                "replacement_job_id": replacement_job.id,
                "engine_version": COMPLETION_REFRESH_ENGINE_VERSION,
            },
            error_message="chapter content changed during refresh analysis",
        )

    # Establish the database write transaction before Gap upserts open their
    # uniqueness savepoints. SQLite otherwise treats the first SAVEPOINT as the
    # outer transaction and releasing it can make partial Gap writes survive a
    # later rollback. The job row is already owned by this worker lease.
    job.updated_at = datetime.now(UTC).replace(tzinfo=None)
    db.add(job)
    await db.flush()

    persistent_deterministic = _persistent_candidates(deterministic_candidates)
    persistent_semantic = _persistent_candidates(semantic_candidates)
    editorial_guidance = list(
        dict.fromkeys(
            [
                *await _user_confirmation_guidance(
                    db,
                    project_id=project_id,
                    chapter_id=chapter_id,
                ),
                *_editorial_guidance(
                    [*deterministic_candidates, *semantic_candidates]
                ),
            ]
        )
    )[:12]

    _, deterministic_created = await gaps.upsert_gaps(
        db,
        project_id=project_id,
        candidates=persistent_deterministic,
    )
    deterministic_resolved = await gaps.reconcile_chapter_rule_gaps(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        complete_candidates=persistent_deterministic,
    )
    semantic_created = 0
    semantic_resolved = 0
    if persistent_semantic:
        _, semantic_created = await gaps.upsert_gaps(
            db,
            project_id=project_id,
            candidates=persistent_semantic,
        )
    if include_semantic:
        semantic_resolved = await gaps.reconcile_chapter_semantic_gaps(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
            complete_candidates=persistent_semantic,
        )

    task_result = await followup_planner.generate_followup_tasks(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    active_task_exists = await db.scalar(
        select(MemoirFollowupTask.id)
        .where(
            MemoirFollowupTask.project_id == project_id,
            MemoirFollowupTask.chapter_id == chapter_id,
            MemoirFollowupTask.status.in_(
                {
                    MemoirFollowupTaskStatus.PENDING.value,
                    MemoirFollowupTaskStatus.IN_PROGRESS.value,
                    MemoirFollowupTaskStatus.DEFERRED.value,
                }
            ),
        )
        .limit(1)
    )
    manual_review_exists = await db.scalar(
        select(MemoirNarrativeGap.id)
        .where(
            MemoirNarrativeGap.project_id == project_id,
            MemoirNarrativeGap.status == MemoirNarrativeGapStatus.OPEN.value,
            MemoirNarrativeGap.recommended_action
            == MemoirNarrativeGapRecommendedAction.MANUAL_REVIEW.value,
            or_(
                MemoirNarrativeGap.primary_chapter_id == chapter_id,
                MemoirNarrativeGap.chapters.any(chapter_id=chapter_id),
            ),
        )
        .limit(1)
    )
    draft_job = None
    if (
        active_task_exists is None
        and manual_review_exists is None
        and any(
            node.status == MemoirChapterNodeStatus.READY.value
            for node in current_context.nodes
        )
    ):
        if editorial_guidance:
            await draft_blocks.mark_chapter_blocks_stale(
                db,
                project_id=project_id,
                chapter_ids={chapter_id},
            )
        draft_job = await draft_generation_jobs.enqueue_chapter_block_generation(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
            editorial_guidance=editorial_guidance,
            source="book_completion_auto",
        )
    result = {
        "chapter_id": chapter_id,
        "deterministic_gap_count": len(deterministic_candidates),
        "deterministic_created_count": deterministic_created,
        "deterministic_resolved_count": deterministic_resolved,
        "semantic_gap_count": len(semantic_candidates),
        "semantic_created_count": semantic_created,
        "semantic_resolved_count": semantic_resolved,
        "created_task_ids": list(task_result.created_task_ids),
        "updated_task_ids": list(task_result.updated_task_ids),
        "skipped_gap_ids": list(task_result.skipped_gap_ids),
        "editorial_guidance_count": len(editorial_guidance),
        "draft_generation_job_id": draft_job.id if draft_job is not None else None,
        "published": True,
        "provider": provider,
        "model": model,
        "engine_version": COMPLETION_REFRESH_ENGINE_VERSION,
        "planner_version": followup_planner.FOLLOWUP_PLANNER_VERSION,
    }
    await automation_events.record_or_update_automation_event(
        db,
        project_id=project_id,
        event_type=MemoirAutomationEventType.AUDIT_COMPLETED,
        status=MemoirAutomationEventStatus.COMPLETED,
        chapter_id=chapter_id,
        utterance_ids=[],
        editing_job_id=job_id,
        summary_text="章节补全状态已更新。",
        event_signature=f"chapter_completion_refresh:{job_id}",
        metadata_json={
            "source": "chapter_completion_refresh",
            "editing_job_id": job_id,
            **result,
        },
    )
    return EditingJobResult(result_json=result)
