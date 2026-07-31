from __future__ import annotations

from collections import Counter

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow_naive
from app.domain.memoir.types import (
    MemoirBookCompletionJobStatus,
    MemoirEditingJobStatus,
)
from app.models.memoir import MemoirBookCompletionJob, MemoirEditingJob
from app.schemas.memoir_narrative import (
    MemoirBookCompletionJobRequest,
    MemoirChapterPreparationJobRequest,
)
from app.services.memoir import chapter_preparation_jobs
from app.services.memoir.narrative import book_completion


_ACTIVE_BATCH_STATUSES = {
    MemoirBookCompletionJobStatus.PENDING.value,
    MemoirBookCompletionJobStatus.RUNNING.value,
}
_TERMINAL_BATCH_STATUSES = {
    MemoirBookCompletionJobStatus.SUCCEEDED.value,
    MemoirBookCompletionJobStatus.FAILED.value,
    MemoirBookCompletionJobStatus.CANCELLED.value,
}
_ACTIVE_CHAPTER_STATUSES = {
    MemoirEditingJobStatus.PENDING.value,
    MemoirEditingJobStatus.RUNNING.value,
}
_TERMINAL_CHAPTER_STATUSES = {"succeeded", "failed", "cancelled"}


def _active_key(project_id: str) -> str:
    return f"book-completion:{project_id}"


def _targets(job: MemoirBookCompletionJob) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for item in job.target_chapters_json or []:
        if not isinstance(item, dict):
            continue
        chapter_id = str(item.get("chapter_id") or "").strip()
        if not chapter_id:
            continue
        result.append({
            "chapter_id": chapter_id,
            "title": str(item.get("title") or "").strip(),
        })
    return result


def _stored_chapter_results(
    job: MemoirBookCompletionJob,
    targets: list[dict[str, str]],
) -> list[dict[str, object]] | None:
    if job.status not in _TERMINAL_BATCH_STATUSES:
        return None
    stored_by_chapter: dict[str, dict] = {}
    for item in job.chapter_results_json or []:
        if not isinstance(item, dict):
            continue
        chapter_id = str(item.get("chapter_id") or "").strip()
        status = str(item.get("status") or "").strip()
        if chapter_id and status in _TERMINAL_CHAPTER_STATUSES:
            stored_by_chapter[chapter_id] = item
    if (
        len(stored_by_chapter) != len(targets)
        or any(target["chapter_id"] not in stored_by_chapter for target in targets)
    ):
        return None
    return [
        {
            **target,
            "job_id": (
                str(stored_by_chapter[target["chapter_id"]].get("job_id"))
                if stored_by_chapter[target["chapter_id"]].get("job_id")
                else None
            ),
            "status": str(
                stored_by_chapter[target["chapter_id"]].get("status")
            ),
            "error_message": str(
                stored_by_chapter[target["chapter_id"]].get("error_message")
                or ""
            ),
        }
        for target in targets
    ]


async def _chapter_jobs(
    db: AsyncSession,
    job_ids: list[str],
) -> dict[str, MemoirEditingJob]:
    if not job_ids:
        return {}
    rows = (
        await db.scalars(
            select(MemoirEditingJob).where(MemoirEditingJob.id.in_(job_ids))
        )
    ).all()
    return {row.id: row for row in rows}


def _chapter_item(
    *,
    target: dict[str, str],
    batch_status: str,
    stored_item: dict[str, object] | None,
    child_job_id: str | None,
    child_job: MemoirEditingJob | None,
    enqueue_error: str,
) -> dict[str, object]:
    error_message = enqueue_error
    if stored_item is not None:
        child_job_id = (
            str(stored_item["job_id"])
            if stored_item.get("job_id")
            else None
        )
        status = str(stored_item["status"])
        error_message = str(stored_item["error_message"])
    elif error_message:
        status = "failed"
    elif (
        batch_status == MemoirBookCompletionJobStatus.CANCELLED.value
        and child_job is None
    ):
        status = "cancelled"
    elif child_job is None:
        status = (
            "pending"
            if batch_status == MemoirBookCompletionJobStatus.PENDING.value
            else "failed"
        )
        if status == "failed":
            error_message = "章节检查任务未成功提交"
    elif child_job.status == MemoirEditingJobStatus.SUCCEEDED.value:
        status = "succeeded"
    elif child_job.status == MemoirEditingJobStatus.FAILED.value:
        status = "failed"
        error_message = child_job.error_message or "章节检查失败"
    elif child_job.status == MemoirEditingJobStatus.SKIPPED.value:
        status = "cancelled"
    else:
        status = (
            "running"
            if child_job.status == MemoirEditingJobStatus.RUNNING.value
            else "pending"
        )
    return {
        **target,
        "job_id": child_job_id,
        "status": status,
        "error_message": error_message,
    }


def _batch_status(
    job_status: str,
    chapter_items: list[dict[str, object]],
    *,
    stored_results: bool,
) -> str:
    if stored_results:
        return job_status
    if job_status == MemoirBookCompletionJobStatus.CANCELLED.value:
        return MemoirBookCompletionJobStatus.CANCELLED.value
    if any(
        item["status"] not in _TERMINAL_CHAPTER_STATUSES
        for item in chapter_items
    ):
        return (
            MemoirBookCompletionJobStatus.PENDING.value
            if any(item["job_id"] is None for item in chapter_items)
            else MemoirBookCompletionJobStatus.RUNNING.value
        )
    statuses = {str(item["status"]) for item in chapter_items}
    if "failed" in statuses:
        return MemoirBookCompletionJobStatus.FAILED.value
    if "cancelled" in statuses:
        return MemoirBookCompletionJobStatus.CANCELLED.value
    return MemoirBookCompletionJobStatus.SUCCEEDED.value


async def _job_response(
    db: AsyncSession,
    *,
    project_id: str,
    job: MemoirBookCompletionJob | None,
    persist_status: bool = True,
) -> dict[str, object]:
    if job is None:
        return {
            "project_id": project_id,
            "status": "idle",
        }

    targets = _targets(job)
    stored_chapter_items = _stored_chapter_results(job, targets)
    job_ids = {
        str(chapter_id): str(job_id)
        for chapter_id, job_id in dict(job.chapter_job_ids_json or {}).items()
        if str(chapter_id).strip() and str(job_id).strip()
    }
    enqueue_errors = {
        str(chapter_id): str(error)
        for chapter_id, error in dict(job.enqueue_errors_json or {}).items()
        if str(chapter_id).strip() and str(error).strip()
    }
    child_jobs = (
        {}
        if stored_chapter_items is not None
        else await _chapter_jobs(db, list(job_ids.values()))
    )
    stored_by_chapter = {
        str(item["chapter_id"]): item
        for item in stored_chapter_items or []
    }
    chapter_items = [
        _chapter_item(
            target=target,
            batch_status=job.status,
            stored_item=stored_by_chapter.get(target["chapter_id"]),
            child_job_id=job_ids.get(target["chapter_id"]),
            child_job=child_jobs.get(job_ids.get(target["chapter_id"], "")),
            enqueue_error=enqueue_errors.get(target["chapter_id"], ""),
        )
        for target in targets
    ]
    status_counts = Counter(str(item["status"]) for item in chapter_items)
    succeeded_count = status_counts["succeeded"]
    failed_count = status_counts["failed"]
    cancelled_count = status_counts["cancelled"]
    total_count = len(chapter_items)
    processed_count = succeeded_count + failed_count + cancelled_count
    current = next(
        (
            item
            for item in chapter_items
            if item["status"] not in _TERMINAL_CHAPTER_STATUSES
        ),
        None,
    )
    batch_status = _batch_status(
        job.status,
        chapter_items,
        stored_results=stored_chapter_items is not None,
    )
    terminal = batch_status not in _ACTIVE_BATCH_STATUSES
    error_message = next(
        (
            str(item["error_message"])
            for item in chapter_items
            if item["status"] == "failed" and item["error_message"]
        ),
        "",
    )
    chapter_results_changed = terminal and job.chapter_results_json != chapter_items
    if persist_status and (
        job.status != batch_status
        or job.error_message != error_message
        or (terminal and job.active_key is not None)
        or chapter_results_changed
    ):
        job.status = batch_status
        job.error_message = error_message
        job.active_key = None if terminal else _active_key(project_id)
        job.completed_at = utcnow_naive() if terminal else None
        if terminal:
            job.chapter_results_json = chapter_items
        db.add(job)
        await db.commit()

    return {
        "job_id": job.id,
        "project_id": project_id,
        "status": batch_status,
        "total_chapter_count": total_count,
        "processed_chapter_count": processed_count,
        "succeeded_chapter_count": succeeded_count,
        "failed_chapter_count": failed_count,
        "cancelled_chapter_count": cancelled_count,
        "current_chapter_id": current["chapter_id"] if current else None,
        "current_chapter_title": current["title"] if current else "",
        "requested_ai_model": job.requested_ai_model,
        "chapters": chapter_items,
        "can_cancel": batch_status in _ACTIVE_BATCH_STATUSES,
        "can_retry": batch_status in {
            MemoirBookCompletionJobStatus.FAILED.value,
            MemoirBookCompletionJobStatus.CANCELLED.value,
        },
        "error_message": error_message,
    }


async def _load_latest_job(
    db: AsyncSession,
    *,
    project_id: str,
    active_only: bool = False,
) -> MemoirBookCompletionJob | None:
    query = select(MemoirBookCompletionJob).where(
        MemoirBookCompletionJob.project_id == project_id
    )
    if active_only:
        query = query.where(
            MemoirBookCompletionJob.status.in_(_ACTIVE_BATCH_STATUSES)
        )
    return await db.scalar(
        query.order_by(
            MemoirBookCompletionJob.created_at.desc(),
            MemoirBookCompletionJob.id.desc(),
        )
        .limit(1)
    )


async def _enqueue_missing_chapter_jobs(
    db: AsyncSession,
    *,
    job: MemoirBookCompletionJob,
    user_id: str,
    allow_admin: bool,
) -> None:
    chapter_job_ids = dict(job.chapter_job_ids_json or {})
    enqueue_errors = dict(job.enqueue_errors_json or {})
    for target in _targets(job):
        await db.refresh(job, attribute_names=["status"])
        if job.status not in _ACTIVE_BATCH_STATUSES:
            return
        chapter_id = target["chapter_id"]
        if chapter_job_ids.get(chapter_id) or enqueue_errors.get(chapter_id):
            continue
        child_job_id = ""
        try:
            child = await chapter_preparation_jobs.enqueue_chapter_preparation_job(
                db,
                user_id=user_id,
                project_id=job.project_id,
                chapter_id=chapter_id,
                req=MemoirChapterPreparationJobRequest(
                    operation="prepare",
                    ai_model=job.requested_ai_model,
                ),
                allow_admin=allow_admin,
            )
            child_job_id = str(child.get("job_id") or "").strip()
            if not child_job_id:
                raise ValueError("章节检查任务未成功提交")
            chapter_job_ids[chapter_id] = child_job_id
        except (HTTPException, ValueError) as exc:
            enqueue_errors[chapter_id] = (
                str(exc.detail)
                if isinstance(exc, HTTPException)
                else str(exc)
            )
        job.chapter_job_ids_json = dict(chapter_job_ids)
        job.enqueue_errors_json = dict(enqueue_errors)
        db.add(job)
        await db.commit()
        await db.refresh(job, attribute_names=["status"])
        if (
            job.status not in _ACTIVE_BATCH_STATUSES
            and child_job_id
        ):
            await chapter_preparation_jobs.cancel_chapter_preparation_job(
                db,
                user_id=user_id,
                project_id=job.project_id,
                chapter_id=chapter_id,
                job_id=child_job_id,
                allow_admin=allow_admin,
            )
            return


async def enqueue_book_completion_job(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookCompletionJobRequest,
    allow_admin: bool = False,
) -> dict[str, object]:
    active = await _load_latest_job(
        db,
        project_id=project_id,
        active_only=True,
    )
    if active is not None:
        await _enqueue_missing_chapter_jobs(
            db,
            job=active,
            user_id=user_id,
            allow_admin=allow_admin,
        )
        return await _job_response(db, project_id=project_id, job=active)

    overview = await book_completion.get_book_completion_overview(
        db,
        project_id=project_id,
    )
    if req.retry_job_id:
        retry_source = await db.scalar(
            select(MemoirBookCompletionJob).where(
                MemoirBookCompletionJob.id == req.retry_job_id,
                MemoirBookCompletionJob.project_id == project_id,
            )
        )
        retry_response = await _job_response(
            db,
            project_id=project_id,
            job=retry_source,
        )
        if retry_response["status"] not in {
            MemoirBookCompletionJobStatus.FAILED.value,
            MemoirBookCompletionJobStatus.CANCELLED.value,
        }:
            raise ValueError("这次全书检查没有可重试的章节")
        retry_chapter_ids = {
            str(item["chapter_id"])
            for item in retry_response["chapters"]
            if item["status"] != "succeeded"
        }
        targets = [
            {
                "chapter_id": chapter.chapter_id,
                "title": chapter.title,
            }
            for chapter in overview.chapters
            if chapter.chapter_id in retry_chapter_ids
        ]
    else:
        targets = [
            {
                "chapter_id": chapter.chapter_id,
                "title": chapter.title,
            }
            for chapter in overview.chapters
            if (
                chapter.state == "preparing"
                or chapter.next_action.kind == "prepare"
            )
        ]
    if not targets:
        raise ValueError("没有需要检查的章节")

    job = MemoirBookCompletionJob(
        project_id=project_id,
        active_key=_active_key(project_id),
        status=MemoirBookCompletionJobStatus.PENDING.value,
        requested_ai_model=req.ai_model,
        target_chapters_json=targets,
        chapter_job_ids_json={},
        enqueue_errors_json={},
    )
    db.add(job)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        active = await _load_latest_job(
            db,
            project_id=project_id,
            active_only=True,
        )
        if active is None:
            raise
        job = active

    await _enqueue_missing_chapter_jobs(
        db,
        job=job,
        user_id=user_id,
        allow_admin=allow_admin,
    )
    return await _job_response(db, project_id=project_id, job=job)


async def get_current_book_completion_job(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    allow_admin: bool = False,
) -> dict[str, object]:
    job = await _load_latest_job(db, project_id=project_id)
    if job is not None and job.status == MemoirBookCompletionJobStatus.PENDING.value:
        await _enqueue_missing_chapter_jobs(
            db,
            job=job,
            user_id=user_id,
            allow_admin=allow_admin,
        )
    return await _job_response(db, project_id=project_id, job=job)


async def cancel_book_completion_job(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    job_id: str,
    allow_admin: bool = False,
) -> dict[str, object]:
    job = await db.scalar(
        select(MemoirBookCompletionJob).where(
            MemoirBookCompletionJob.id == job_id,
            MemoirBookCompletionJob.project_id == project_id,
        )
    )
    if job is None:
        raise ValueError("全书检查任务不存在")
    if job.status not in _ACTIVE_BATCH_STATUSES:
        return await _job_response(db, project_id=project_id, job=job)

    for chapter_id, child_job_id in dict(job.chapter_job_ids_json or {}).items():
        child_job = await db.get(MemoirEditingJob, str(child_job_id))
        if child_job is None or child_job.status not in _ACTIVE_CHAPTER_STATUSES:
            continue
        await chapter_preparation_jobs.cancel_chapter_preparation_job(
            db,
            user_id=user_id,
            project_id=project_id,
            chapter_id=str(chapter_id),
            job_id=str(child_job_id),
            allow_admin=allow_admin,
        )

    job.status = MemoirBookCompletionJobStatus.CANCELLED.value
    job.active_key = None
    job.completed_at = utcnow_naive()
    db.add(job)
    await db.commit()
    return await _job_response(db, project_id=project_id, job=job)
