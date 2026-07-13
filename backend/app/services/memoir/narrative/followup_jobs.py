from __future__ import annotations

from hashlib import sha256
import json

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import MemoirNarrativeGapStatus
from app.domain.memoir.types import (
    MemoirAutomationEventStatus,
    MemoirAutomationEventType,
    MemoirEditingJobType,
)
from app.models.memoir import MemoirBookPlanChapter, MemoirEditingJob
from app.models.memoir_narrative import MemoirNarrativeGap
from app.services.memoir import automation_events
from app.services.memoir.editing_jobs import EditingJobResult, enqueue_editing_job
from app.services.memoir.narrative import followup_planner


async def _generation_signature(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str | None,
) -> str:
    filters = [
        MemoirNarrativeGap.project_id == project_id,
        MemoirNarrativeGap.status == MemoirNarrativeGapStatus.OPEN.value,
    ]
    if chapter_id:
        filters.append(
            or_(
                MemoirNarrativeGap.primary_chapter_id == chapter_id,
                MemoirNarrativeGap.chapters.any(chapter_id=chapter_id),
            )
        )
    rows = (
        await db.execute(
            select(
                MemoirNarrativeGap.id,
                MemoirNarrativeGap.last_detected_at,
                MemoirNarrativeGap.status,
            )
            .where(*filters)
            .order_by(MemoirNarrativeGap.id)
        )
    ).all()
    payload = {
        "planner_version": followup_planner.FOLLOWUP_PLANNER_VERSION,
        "chapter_id": chapter_id,
        "gaps": [
            [gap_id, detected_at.isoformat(), status]
            for gap_id, detected_at, status in rows
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return sha256(encoded.encode("utf-8")).hexdigest()


async def schedule_followup_task_generation(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str | None,
    source: str,
) -> MemoirEditingJob:
    if chapter_id:
        chapter_exists = await db.scalar(
            select(MemoirBookPlanChapter.id).where(
                MemoirBookPlanChapter.project_id == project_id,
                MemoirBookPlanChapter.id == chapter_id,
            )
        )
        if chapter_exists is None:
            raise ValueError("chapter does not belong to the project")
    signature = await _generation_signature(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    scope = chapter_id or "book"
    return await enqueue_editing_job(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        job_type=MemoirEditingJobType.GENERATE_FOLLOWUP_TASKS,
        payload_json={
            "chapter_id": chapter_id,
            "input_signature": signature,
            "planner_version": followup_planner.FOLLOWUP_PLANNER_VERSION,
            "source": source,
        },
        dedupe_key=f"generate-followup-tasks:{project_id}:{scope}:{signature}",
        replace_completed=True,
        retry_failed=True,
    )


async def run_followup_task_generation_job(
    db: AsyncSession,
    job: MemoirEditingJob,
) -> EditingJobResult:
    payload = job.payload_json if isinstance(job.payload_json, dict) else {}
    chapter_id_value = payload.get("chapter_id")
    chapter_id = str(chapter_id_value).strip() if chapter_id_value else None
    result = await followup_planner.generate_followup_tasks(
        db,
        project_id=job.project_id,
        chapter_id=chapter_id,
    )
    result_json = {
        "chapter_id": chapter_id,
        "created_task_ids": list(result.created_task_ids),
        "updated_task_ids": list(result.updated_task_ids),
        "skipped_gap_ids": list(result.skipped_gap_ids),
        "planner_version": followup_planner.FOLLOWUP_PLANNER_VERSION,
    }
    await automation_events.record_or_update_automation_event(
        db,
        project_id=job.project_id,
        event_type=MemoirAutomationEventType.FOLLOWUP_TASKS_GENERATED,
        status=MemoirAutomationEventStatus.COMPLETED,
        chapter_id=chapter_id,
        utterance_ids=[],
        summary_text="补访任务已根据章节缺口更新。",
        event_signature=f"followup_tasks_generated:{job.id}",
        metadata_json={
            "source": "followup_planner",
            "editing_job_id": job.id,
            **result_json,
        },
    )
    return EditingJobResult(result_json=result_json)
