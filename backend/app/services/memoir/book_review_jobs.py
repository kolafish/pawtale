from __future__ import annotations

from collections.abc import Awaitable, Callable
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import (
    MemoirEditingJobStatus,
    MemoirEditingJobType,
    MemoirSemanticCacheStatus,
)
from app.models.memoir import MemoirEditingJob
from app.schemas.memoir_book_review import MemoirBookReviewGenerateRequest
from app.services.memoir import book_reviews, editing_jobs
from app.services.memoir.access import load_memoir_project
from app.services.memoir.editing_jobs import EditingJobResult
from app.services.memoir.project_ai_preferences import select_project_ai_model


def _job_dedupe_key(project_id: str) -> str:
    return f"generate-book-review:{project_id}"


def _job_state(
    *,
    project_id: str,
    job: MemoirEditingJob | None,
) -> dict[str, object]:
    return editing_jobs.project_editing_job_state(
        project_id=project_id,
        job=job,
    )


async def _load_job(
    db: AsyncSession,
    *,
    project_id: str,
    job_id: str | None = None,
) -> MemoirEditingJob | None:
    return await editing_jobs.load_project_editing_job(
        db,
        project_id=project_id,
        job_type=MemoirEditingJobType.GENERATE_BOOK_REVIEW,
        dedupe_key=_job_dedupe_key(project_id),
        job_id=job_id,
    )


async def enqueue_book_review_job(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookReviewGenerateRequest,
    allow_admin: bool = False,
    input_signature: str | None = None,
    automatic: bool = False,
    on_enqueued: Callable[[MemoirEditingJob], Awaitable[None]] | None = None,
) -> dict[str, object]:
    project = await load_memoir_project(
        db,
        project_id,
        user_id,
        allow_admin=allow_admin,
    )
    selected_ai_model = select_project_ai_model(project, req.ai_model)
    existing = await _load_job(db, project_id=project_id)
    if (
        existing is not None
        and (existing.status in editing_jobs.ACTIVE_EDITING_JOB_STATUSES
             or (automatic and (existing.payload_json or {}).get("input_signature") == input_signature))
    ):
        if on_enqueued is not None:
            await on_enqueued(existing)
        return _job_state(project_id=project_id, job=existing)

    total_chapter_count = await book_reviews.require_readable_chapter_drafts(
        db,
        project_id=project_id,
    )

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
            "input_signature": input_signature,
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
    if on_enqueued is not None:
        await on_enqueued(job)
    await db.commit()
    await db.refresh(job)
    return _job_state(project_id=project_id, job=job)


async def book_review_job_state(
    db: AsyncSession,
    *,
    project_id: str,
) -> dict[str, object]:
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
    await editing_jobs.cancel_project_editing_job(
        db,
        job=job,
        result_patch={"current_chapter_title": ""},
    )
    return _job_state(project_id=project_id, job=job)


async def run_book_review_job(
    db: AsyncSession,
    job: MemoirEditingJob,
) -> EditingJobResult:
    payload = dict(job.payload_json or {})
    progress = dict(job.result_json or {})
    project_id = job.project_id
    update_progress = editing_jobs.editing_job_progress_callback(
        db,
        job=job,
        progress=progress,
    )
    if progress.get("provider_request_pending"):
        return EditingJobResult(status=MemoirEditingJobStatus.FAILED, result_json=progress,
            error_message="服务中断时全书审阅请求尚未确认完成，请确认后重试；已有书稿可以继续阅读")

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
    except editing_jobs.EditingJobProgressLost:
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
