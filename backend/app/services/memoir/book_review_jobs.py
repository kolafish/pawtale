from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import (
    MemoirEditingJobStatus,
    MemoirEditingJobType,
    MemoirSemanticCacheStatus,
)
from app.models.memoir import MemoirBookPlanChapter, MemoirEditingJob
from app.schemas.memoir_book_review import MemoirBookReviewGenerateRequest
from app.services.memoir import book_reviews, editing_jobs
from app.services.memoir.access import load_memoir_project
from app.services.memoir.editing_jobs import EditingJobResult
from app.services.memoir.project_ai_preferences import select_project_ai_model


class _BookReviewCancelled(Exception):
    pass


_ACTIVE_STATUSES = {
    MemoirEditingJobStatus.PENDING.value,
    MemoirEditingJobStatus.RUNNING.value,
}


def _job_dedupe_key(project_id: str) -> str:
    return f"generate-book-review:{project_id}"


def _job_state(
    *,
    project_id: str,
    job: MemoirEditingJob | None,
) -> dict[str, object]:
    if job is None:
        return {
            "project_id": project_id,
            "status": "idle",
        }
    result = dict(job.result_json or {})
    cancelled = result.get("cancelled") is True
    status = (
        "cancelled"
        if cancelled
        else (
            job.status
            if job.status
            in {
                MemoirEditingJobStatus.PENDING.value,
                MemoirEditingJobStatus.RUNNING.value,
                MemoirEditingJobStatus.SUCCEEDED.value,
                MemoirEditingJobStatus.FAILED.value,
            }
            else MemoirEditingJobStatus.FAILED.value
        )
    )
    return {
        **result,
        "job_id": job.id,
        "project_id": project_id,
        "status": status,
        "can_cancel": status in _ACTIVE_STATUSES,
        "can_retry": status in {
            MemoirEditingJobStatus.FAILED.value,
            "cancelled",
        },
        "error_message": (
            job.error_message
            if status == MemoirEditingJobStatus.FAILED.value
            else ""
        ),
    }


async def _load_job(
    db: AsyncSession,
    *,
    project_id: str,
    job_id: str | None = None,
) -> MemoirEditingJob | None:
    statement = select(MemoirEditingJob).where(
        MemoirEditingJob.project_id == project_id,
        MemoirEditingJob.job_type
        == MemoirEditingJobType.GENERATE_BOOK_REVIEW.value,
    )
    if job_id:
        statement = statement.where(MemoirEditingJob.id == job_id)
    else:
        statement = statement.where(
            MemoirEditingJob.dedupe_key == _job_dedupe_key(project_id)
        )
    return await db.scalar(statement)


async def enqueue_book_review_job(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookReviewGenerateRequest,
    allow_admin: bool = False,
) -> dict[str, object]:
    project = await load_memoir_project(
        db,
        project_id,
        user_id,
        allow_admin=allow_admin,
    )
    selected_ai_model = select_project_ai_model(project, req.ai_model)
    existing = await _load_job(db, project_id=project_id)
    if existing is not None and existing.status in _ACTIVE_STATUSES:
        return _job_state(project_id=project_id, job=existing)

    total_chapter_count = int(
        await db.scalar(
            select(func.count())
            .select_from(MemoirBookPlanChapter)
            .where(
                MemoirBookPlanChapter.project_id == project_id,
                MemoirBookPlanChapter.include_in_book.is_(True),
            )
        )
        or 0
    )
    if total_chapter_count == 0:
        raise ValueError("全书没有收录章节")

    job = await editing_jobs.enqueue_editing_job(
        db,
        project_id=project_id,
        job_type=MemoirEditingJobType.GENERATE_BOOK_REVIEW,
        dedupe_key=_job_dedupe_key(project_id),
        replace_completed=True,
        payload_json={
            "user_id": user_id,
            "allow_admin": allow_admin,
            "force_regenerate": req.force_regenerate,
            "requested_ai_model": selected_ai_model,
        },
    )
    job.result_json = {
        "stage": "queued",
        "total_chapter_count": total_chapter_count,
        "processed_chapter_count": 0,
        "current_chapter_title": "",
        "requested_ai_model": selected_ai_model,
        "resolved_ai_model": None,
        "review_id": None,
    }
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return _job_state(project_id=project_id, job=job)


async def get_book_review_job(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    allow_admin: bool = False,
) -> dict[str, object]:
    await load_memoir_project(
        db,
        project_id,
        user_id,
        allow_admin=allow_admin,
    )
    return _job_state(
        project_id=project_id,
        job=await _load_job(db, project_id=project_id),
    )


async def cancel_book_review_job(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    job_id: str,
    allow_admin: bool = False,
) -> dict[str, object]:
    await load_memoir_project(
        db,
        project_id,
        user_id,
        allow_admin=allow_admin,
    )
    job = await _load_job(
        db,
        project_id=project_id,
        job_id=job_id,
    )
    if job is None:
        raise ValueError("全书检查任务不存在")
    if job.status not in _ACTIVE_STATUSES:
        return _job_state(project_id=project_id, job=job)
    result_json = {
        **dict(job.result_json or {}),
        "stage": "cancelled",
        "cancelled": True,
        "current_chapter_title": "",
    }
    await db.execute(
        update(MemoirEditingJob)
        .where(
            MemoirEditingJob.id == job.id,
            MemoirEditingJob.status.in_(_ACTIVE_STATUSES),
        )
        .values(
            status=MemoirEditingJobStatus.SKIPPED.value,
            locked_at=None,
            lease_token=None,
            lease_expires_at=None,
            result_json=result_json,
        )
    )
    await db.commit()
    await db.refresh(job)
    return _job_state(project_id=project_id, job=job)


async def run_book_review_job(
    db: AsyncSession,
    job: MemoirEditingJob,
) -> EditingJobResult:
    payload = dict(job.payload_json or {})
    progress = dict(job.result_json or {})
    job_id = job.id
    lease_token = job.lease_token
    project_id = job.project_id

    async def update_progress(patch: dict[str, object]) -> None:
        progress.update(patch)
        if not await editing_jobs.update_editing_job_progress_for_lease(
            db,
            job_id=job_id,
            lease_token=lease_token,
            result_json=progress,
        ):
            raise _BookReviewCancelled

    try:
        await load_memoir_project(
            db,
            project_id,
            str(payload.get("user_id") or ""),
            allow_admin=payload.get("allow_admin") is True,
        )
        review = await book_reviews.generate_or_reuse_book_review(
            db,
            project_id=project_id,
            ai_model=(
                payload["requested_ai_model"]
                if isinstance(payload.get("requested_ai_model"), str)
                else None
            ),
            force_regenerate=payload.get("force_regenerate") is True,
            progress_callback=update_progress,
        )
    except _BookReviewCancelled:
        return EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            result_json={
                **progress,
                "stage": "cancelled",
                "cancelled": True,
                "current_chapter_title": "",
            },
        )
    except HTTPException as exc:
        return EditingJobResult(
            status=MemoirEditingJobStatus.FAILED,
            result_json={
                **progress,
                "stage": "failed",
                "current_chapter_title": "",
            },
            error_message=str(exc.detail),
        )

    generation = (
        review.review_json.get("generation", {})
        if isinstance(review.review_json, dict)
        else {}
    )
    completed = {
        **progress,
        "stage": (
            "completed"
            if review.status == MemoirSemanticCacheStatus.READY.value
            else "failed"
        ),
        "processed_chapter_count": progress.get("total_chapter_count", 0),
        "current_chapter_title": "",
        "resolved_ai_model": (
            generation.get("model")
            if isinstance(generation, dict)
            else None
        )
        or payload.get("requested_ai_model"),
        "review_id": review.id,
    }
    if review.status != MemoirSemanticCacheStatus.READY.value:
        return EditingJobResult(
            status=MemoirEditingJobStatus.FAILED,
            result_json=completed,
            error_message=review.error_message or "全书检查没有完成",
        )
    return EditingJobResult(result_json=completed)
