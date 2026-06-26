from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import async_session_maker
from app.domain.memoir.types import (
    MemoirAutomationEventStatus,
    MemoirAutomationEventType,
    MemoirBookAutoRevisionJobStatus,
)
from app.models.memoir import (
    MemoirBookAutoRevisionJob,
    MemoirBookChapterDraft,
    MemoirBookPlanChapter,
)
from app.models.projects import Project
from app.schemas.memoir_book import MemoirBookChapterDraftGenerateRequest
from app.services.ai_types import AITextClient
from app.services.memoir import automation_events
from app.services.memoir.book_drafts import generate_chapter_draft
from app.services.memoir.book_plan_sources import (
    pending_source_ids as _pending_source_ids,
    removed_source_ids as _removed_source_ids,
)


AI_MODEL_METADATA_KEY = "ai_model"
logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _project_ai_model(project: Project | None) -> str | None:
    metadata = project.metadata_json if project else {}
    value = (metadata or {}).get(AI_MODEL_METADATA_KEY)
    return value.strip() if isinstance(value, str) and value.strip() else None


def remember_project_ai_model(project: Project, ai_model: str | None) -> None:
    model = (ai_model or "").strip()
    if not model:
        return
    project.metadata_json = {
        **(project.metadata_json or {}),
        AI_MODEL_METADATA_KEY: model,
        "ai_model_updated_at": _utcnow().isoformat(),
    }


async def _chapter_latest_draft(db: AsyncSession, project_id: str, chapter_id: str) -> MemoirBookChapterDraft | None:
    return await db.scalar(
        select(MemoirBookChapterDraft)
        .where(
            MemoirBookChapterDraft.project_id == project_id,
            MemoirBookChapterDraft.chapter_id == chapter_id,
        )
        .options(selectinload(MemoirBookChapterDraft.versions))
    )


async def schedule_chapter_auto_revision(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    ai_model: str | None = None,
    run_after: datetime | None = None,
) -> MemoirBookAutoRevisionJob | None:
    chapter = await db.get(MemoirBookPlanChapter, chapter_id)
    if not chapter or chapter.project_id != project_id:
        return None
    pending_source_ids = _pending_source_ids(chapter.metadata_json)
    removed_source_ids = _removed_source_ids(chapter.metadata_json)
    if not pending_source_ids or removed_source_ids:
        return None

    draft = await _chapter_latest_draft(db, project_id, chapter_id)
    if not draft or not draft.versions:
        return None

    project = await db.get(Project, project_id)
    model = (ai_model or "").strip() or _project_ai_model(project)
    settings = get_settings()
    scheduled_at = run_after or (_utcnow() + timedelta(seconds=max(0, settings.memoir_auto_revision_delay_seconds)))
    metadata = {
        "base_version_id": draft.versions[-1].id,
        "pending_source_utterance_ids": sorted(pending_source_ids),
        **({"ai_model": model} if model else {}),
    }

    job = await db.scalar(
        select(MemoirBookAutoRevisionJob).where(MemoirBookAutoRevisionJob.chapter_id == chapter_id)
    )
    if not job:
        job = MemoirBookAutoRevisionJob(
            project_id=project_id,
            chapter_id=chapter_id,
            status=MemoirBookAutoRevisionJobStatus.PENDING.value,
            run_after=scheduled_at,
            metadata_json=metadata,
        )
    else:
        job.status = MemoirBookAutoRevisionJobStatus.PENDING.value
        job.run_after = scheduled_at
        job.locked_at = None
        job.attempts = 0
        job.error_message = ""
        job.metadata_json = metadata
    db.add(job)
    await db.flush()
    return job


async def schedule_chapter_auto_revisions(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_ids: set[str],
    ai_model: str | None = None,
) -> None:
    for chapter_id in sorted(chapter_ids):
        await schedule_chapter_auto_revision(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
            ai_model=ai_model,
        )


async def _mark_job(
    db: AsyncSession,
    job: MemoirBookAutoRevisionJob,
    status: MemoirBookAutoRevisionJobStatus,
    *,
    error_message: str = "",
    metadata_patch: dict[str, object] | None = None,
) -> None:
    job.status = status.value
    job.error_message = error_message
    job.locked_at = None
    if metadata_patch:
        job.metadata_json = {**(job.metadata_json or {}), **metadata_patch}
    db.add(job)
    await db.commit()


async def run_auto_revision_job(
    db: AsyncSession,
    job_id: str,
    *,
    ai_client: AITextClient | None = None,
) -> bool:
    job = await db.get(MemoirBookAutoRevisionJob, job_id)
    if not job or job.status not in {
        MemoirBookAutoRevisionJobStatus.PENDING.value,
        MemoirBookAutoRevisionJobStatus.RUNNING.value,
    }:
        return False

    job.status = MemoirBookAutoRevisionJobStatus.RUNNING.value
    job.locked_at = _utcnow()
    job.attempts += 1
    db.add(job)
    await db.commit()

    try:
        project = await db.get(Project, job.project_id)
        if not project or not project.user_id:
            await _mark_job(db, job, MemoirBookAutoRevisionJobStatus.SKIPPED, error_message="project unavailable")
            return False

        draft = await _chapter_latest_draft(db, job.project_id, job.chapter_id)
        if not draft or not draft.versions:
            await _mark_job(db, job, MemoirBookAutoRevisionJobStatus.SKIPPED, error_message="draft unavailable")
            return False
        base_version_id = str((job.metadata_json or {}).get("base_version_id") or "")
        if draft.versions[-1].id != base_version_id:
            await _mark_job(db, job, MemoirBookAutoRevisionJobStatus.SKIPPED, error_message="draft changed")
            return False

        chapter = await db.get(MemoirBookPlanChapter, job.chapter_id)
        pending_source_ids = _pending_source_ids(chapter.metadata_json if chapter else {})
        if not pending_source_ids:
            await _mark_job(db, job, MemoirBookAutoRevisionJobStatus.SKIPPED, error_message="no pending sources")
            return False

        model = str((job.metadata_json or {}).get("ai_model") or _project_ai_model(project) or "").strip() or None
        pending_source_id_list = sorted(pending_source_ids)
        draft_response = await generate_chapter_draft(
            db,
            user_id=project.user_id,
            project_id=job.project_id,
            chapter_id=job.chapter_id,
            req=MemoirBookChapterDraftGenerateRequest(ai_model=model, force_regenerate=False),
            ai_client=ai_client,
        )
        title = chapter.title if chapter else "未命名章节"
        source_count = len(pending_source_id_list)
        await automation_events.record_automation_event(
            db,
            project_id=job.project_id,
            event_type=MemoirAutomationEventType.CHAPTER_AUTO_REVISED,
            status=MemoirAutomationEventStatus.COMPLETED,
            chapter_id=job.chapter_id,
            utterance_ids=pending_source_id_list,
            summary_text=f"「{title}」已自动补写 {source_count} 条新增讲述。",
            metadata_json={
                "source": "book_auto_revision",
                "job_id": job.id,
                "result_version_id": draft_response.latest_version.id if draft_response.latest_version else "",
                "pending_source_utterance_ids": pending_source_id_list,
                **({"ai_model": model} if model else {}),
            },
        )
        await _mark_job(
            db,
            job,
            MemoirBookAutoRevisionJobStatus.SUCCEEDED,
            metadata_patch={
                "result_version_id": draft_response.latest_version.id if draft_response.latest_version else "",
                "completed_at": _utcnow().isoformat(),
            },
        )
        return True
    except HTTPException as exc:
        await db.rollback()
        job = await db.get(MemoirBookAutoRevisionJob, job_id)
        if job:
            status = (
                MemoirBookAutoRevisionJobStatus.SKIPPED
                if exc.status_code == 409
                else MemoirBookAutoRevisionJobStatus.FAILED
            )
            await _mark_job(db, job, status, error_message=str(exc.detail))
        return False
    except Exception as exc:
        await db.rollback()
        logger.exception("memoir book auto revision job failed job_id=%s", job_id)
        job = await db.get(MemoirBookAutoRevisionJob, job_id)
        if job:
            await _mark_job(db, job, MemoirBookAutoRevisionJobStatus.FAILED, error_message=str(exc))
        return False


async def claim_due_auto_revision_jobs(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int | None = None,
) -> list[str]:
    settings = get_settings()
    due_at = now or _utcnow()
    batch_size = limit or max(1, settings.memoir_auto_revision_batch_size)
    jobs = (
        await db.execute(
            select(MemoirBookAutoRevisionJob)
            .where(
                MemoirBookAutoRevisionJob.status == MemoirBookAutoRevisionJobStatus.PENDING.value,
                MemoirBookAutoRevisionJob.run_after <= due_at,
            )
            .order_by(MemoirBookAutoRevisionJob.run_after, MemoirBookAutoRevisionJob.created_at)
            .limit(batch_size)
        )
    ).scalars().all()
    job_ids: list[str] = []
    for job in jobs:
        job.status = MemoirBookAutoRevisionJobStatus.RUNNING.value
        job.locked_at = due_at
        db.add(job)
        job_ids.append(job.id)
    await db.commit()
    return job_ids


async def run_due_auto_revision_jobs(
    session_maker: async_sessionmaker[AsyncSession] = async_session_maker,
    *,
    limit: int | None = None,
) -> int:
    async with session_maker() as db:
        job_ids = await claim_due_auto_revision_jobs(db, limit=limit)
    completed = 0
    for job_id in job_ids:
        async with session_maker() as db:
            if await run_auto_revision_job(db, job_id):
                completed += 1
    return completed


async def auto_revision_worker_loop(stop_event: asyncio.Event) -> None:
    settings = get_settings()
    poll_seconds = max(1, settings.memoir_auto_revision_poll_seconds)
    while not stop_event.is_set():
        try:
            await run_due_auto_revision_jobs()
        except Exception:
            logger.exception("memoir book auto revision worker tick failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_seconds)
        except asyncio.TimeoutError:
            pass
