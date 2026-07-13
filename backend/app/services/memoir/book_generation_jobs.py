from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import (
    MemoirAutomationEventStatus,
    MemoirAutomationEventType,
    MemoirEditingJobStatus,
    MemoirEditingJobType,
)
from app.models.memoir import MemoirAutomationEvent, MemoirEditingJob
from app.models.projects import Project
from app.schemas.memoir_book import (
    MemoirBookOutputEditorialCopyRegenerateRequest,
)
from app.schemas.memoir_voice import MemoirAutomationEventResponse
from app.services.memoir import automation_events
from app.services.memoir.access import load_memoir_project
from app.services.memoir.book_outputs import regenerate_book_output_editorial_copy
from app.services.memoir.editing_jobs import EditingJobResult, enqueue_editing_job


logger = logging.getLogger(__name__)

AUTOMATION_EVENT_ID_KEY = "automation_event_id"
GENERATION_EVENT_SOURCE = "book_generation_job"
ACTIVE_JOB_STATUSES = {
    MemoirEditingJobStatus.PENDING.value,
    MemoirEditingJobStatus.RUNNING.value,
}


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _safe_str(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _request_payload(req: object) -> dict:
    model_dump = getattr(req, "model_dump", None)
    if callable(model_dump):
        return model_dump(exclude_unset=True, exclude_none=True)
    return {}


def _editorial_copy_label(req: MemoirBookOutputEditorialCopyRegenerateRequest) -> str:
    labels = {
        "preface": "前言",
        "afterword": "后记",
        "back_cover_quote": "封底摘录",
        "chapter_intros": "章节导语",
        "pull_quotes": "章首引语",
    }
    targets = [target for target in req.targets if target in labels]
    if len(targets) == 1:
        return labels[targets[0]]
    if len(targets) > 1:
        return "、".join(labels[target] for target in targets)
    return "成书文案"


def _editorial_copy_dedupe_key(
    project_id: str,
    req: MemoirBookOutputEditorialCopyRegenerateRequest,
) -> str:
    target_key = ",".join(sorted(req.targets)) or "all"
    chapter_key = ",".join(sorted(req.chapter_ids)) or "book"
    return f"generate_book_editorial_copy:{project_id}:{target_key}:{chapter_key}"


async def _existing_active_generation_job(
    db: AsyncSession,
    *,
    dedupe_key: str,
) -> MemoirEditingJob | None:
    existing = await db.scalar(select(MemoirEditingJob).where(MemoirEditingJob.dedupe_key == dedupe_key))
    if existing and existing.status in ACTIVE_JOB_STATUSES:
        return existing
    return None


async def _enqueue_generation_job(
    db: AsyncSession,
    *,
    project_id: str,
    job_type: MemoirEditingJobType,
    chapter_id: str | None,
    dedupe_key: str,
    payload_json: dict,
) -> MemoirEditingJob:
    existing = await _existing_active_generation_job(db, dedupe_key=dedupe_key)
    if existing:
        return existing
    return await enqueue_editing_job(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        job_type=job_type,
        payload_json=payload_json,
        run_after=_utcnow(),
        dedupe_key=dedupe_key,
        replace_completed=True,
    )


async def _ensure_generation_event(
    db: AsyncSession,
    *,
    job: MemoirEditingJob,
    event_type: MemoirAutomationEventType,
    summary_text: str,
    chapter_id: str | None = None,
    metadata_json: dict | None = None,
) -> MemoirAutomationEvent:
    payload = dict(job.payload_json or {})
    metadata = {
        "source": GENERATION_EVENT_SOURCE,
        "job_id": job.id,
        "job_type": job.job_type,
        **(metadata_json or {}),
    }
    event_id = _safe_str(payload.get(AUTOMATION_EVENT_ID_KEY))
    event = await db.get(MemoirAutomationEvent, event_id) if event_id else None
    if event and event.project_id == job.project_id:
        event.event_type = event_type.value
        event.status = MemoirAutomationEventStatus.PENDING.value
        event.chapter_id = chapter_id
        event.summary_text = summary_text
        event.metadata_json = {
            **(event.metadata_json or {}),
            **metadata,
        }
        db.add(event)
        await db.flush()
    else:
        event = await automation_events.record_automation_event(
            db,
            project_id=job.project_id,
            event_type=event_type,
            status=MemoirAutomationEventStatus.PENDING,
            chapter_id=chapter_id,
            summary_text=summary_text,
            metadata_json=metadata,
        )
        payload[AUTOMATION_EVENT_ID_KEY] = event.id
        job.payload_json = payload
        db.add(job)
        await db.flush()
    return event


async def _update_generation_event(
    db: AsyncSession,
    *,
    job: MemoirEditingJob,
    event_type: MemoirAutomationEventType,
    status: MemoirAutomationEventStatus,
    summary_text: str,
    chapter_id: str | None = None,
    metadata_json: dict | None = None,
) -> MemoirAutomationEvent:
    payload = dict(job.payload_json or {})
    metadata = {
        "source": GENERATION_EVENT_SOURCE,
        "job_id": job.id,
        "job_type": job.job_type,
        **(metadata_json or {}),
    }
    event_id = _safe_str(payload.get(AUTOMATION_EVENT_ID_KEY))
    event = await db.get(MemoirAutomationEvent, event_id) if event_id else None
    if event and event.project_id == job.project_id:
        event.event_type = event_type.value
        event.status = status.value
        event.chapter_id = chapter_id
        event.summary_text = summary_text
        event.metadata_json = {
            **(event.metadata_json or {}),
            **metadata,
        }
        db.add(event)
        await db.flush()
        return event

    return await automation_events.record_automation_event(
        db,
        project_id=job.project_id,
        event_type=event_type,
        status=status,
        chapter_id=chapter_id,
        summary_text=summary_text,
        metadata_json=metadata,
    )


async def enqueue_book_editorial_copy_generation_job(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookOutputEditorialCopyRegenerateRequest,
) -> MemoirAutomationEventResponse:
    await load_memoir_project(db, project_id, user_id)
    request_id = str(uuid4())
    label = _editorial_copy_label(req)
    job = await _enqueue_generation_job(
        db,
        project_id=project_id,
        chapter_id=None,
        job_type=MemoirEditingJobType.GENERATE_BOOK_EDITORIAL_COPY,
        dedupe_key=_editorial_copy_dedupe_key(project_id, req),
        payload_json={
            "request_id": request_id,
            "user_id": user_id,
            "request": _request_payload(req),
            "target_label": label,
        },
    )
    event = await _ensure_generation_event(
        db,
        job=job,
        event_type=MemoirAutomationEventType.BOOK_EDITORIAL_COPY_GENERATED,
        summary_text=f"{label}已提交后台重生成。",
        metadata_json={
            "request_id": _safe_str((job.payload_json or {}).get("request_id")) or request_id,
            "targets": list(req.targets),
            "chapter_ids": list(req.chapter_ids),
            "target_label": label,
        },
    )
    await db.commit()
    return automation_events.event_response(event)


def _job_request_payload(job: MemoirEditingJob) -> dict:
    request = (job.payload_json or {}).get("request")
    return request if isinstance(request, dict) else {}


async def _failed_result(
    db: AsyncSession,
    *,
    job: MemoirEditingJob,
    event_type: MemoirAutomationEventType,
    summary_text: str,
    error_message: str,
    chapter_id: str | None = None,
) -> EditingJobResult:
    await _update_generation_event(
        db,
        job=job,
        event_type=event_type,
        status=MemoirAutomationEventStatus.FAILED,
        chapter_id=chapter_id,
        summary_text=summary_text,
        metadata_json={"error_message": error_message},
    )
    return EditingJobResult(
        status=MemoirEditingJobStatus.FAILED,
        error_message=error_message,
        result_json={"failed_at": _utcnow().isoformat()},
    )


async def run_book_editorial_copy_generation_job(
    db: AsyncSession,
    job: MemoirEditingJob,
) -> EditingJobResult:
    job_id = job.id
    payload = dict(job.payload_json or {})
    label = _safe_str(payload.get("target_label")) or "成书文案"
    try:
        project = await db.get(Project, job.project_id)
        user_id = _safe_str(payload.get("user_id")) or (project.user_id if project else "")
        if not project or not user_id:
            return await _failed_result(
                db,
                job=job,
                event_type=MemoirAutomationEventType.BOOK_EDITORIAL_COPY_GENERATED,
                summary_text=f"{label}后台重生成失败。",
                error_message="project unavailable",
            )
        req = MemoirBookOutputEditorialCopyRegenerateRequest(**_job_request_payload(job))
        label = _editorial_copy_label(req)
        output = await regenerate_book_output_editorial_copy(
            db,
            user_id=user_id,
            project_id=job.project_id,
            req=req,
        )
        latest_version = output.latest_version
        await _update_generation_event(
            db,
            job=job,
            event_type=MemoirAutomationEventType.BOOK_EDITORIAL_COPY_GENERATED,
            status=MemoirAutomationEventStatus.COMPLETED,
            summary_text=f"{label}已重生成，可以预览或导出。",
            metadata_json={
                "result_version_id": latest_version.id if latest_version else "",
                "book_title": output.title,
                "targets": list(req.targets),
                "chapter_ids": list(req.chapter_ids),
                "target_label": label,
            },
        )
        return EditingJobResult(
            result_json={
                "result_version_id": latest_version.id if latest_version else "",
                "completed_at": _utcnow().isoformat(),
            },
        )
    except HTTPException as exc:
        await db.rollback()
        job = await db.get(MemoirEditingJob, job_id) or job
        return await _failed_result(
            db,
            job=job,
            event_type=MemoirAutomationEventType.BOOK_EDITORIAL_COPY_GENERATED,
            summary_text=f"{label}后台重生成失败。",
            error_message=str(exc.detail),
        )
    except ValidationError as exc:
        await db.rollback()
        job = await db.get(MemoirEditingJob, job_id) or job
        return await _failed_result(
            db,
            job=job,
            event_type=MemoirAutomationEventType.BOOK_EDITORIAL_COPY_GENERATED,
            summary_text=f"{label}后台重生成失败。",
            error_message=str(exc),
        )
    except Exception as exc:
        await db.rollback()
        logger.exception("memoir book editorial copy generation job failed job_id=%s", job_id)
        job = await db.get(MemoirEditingJob, job_id) or job
        return await _failed_result(
            db,
            job=job,
            event_type=MemoirAutomationEventType.BOOK_EDITORIAL_COPY_GENERATED,
            summary_text=f"{label}后台重生成失败。",
            error_message=str(exc),
        )
