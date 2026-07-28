from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import MemoirEditingJobStatus, MemoirEditingJobType
from app.models.memoir import (
    MemoirBookOutput,
    MemoirBookOutputVersion,
    MemoirEditingJob,
)
from app.schemas.memoir_book_review import MemoirBookReviewAnswerApplyRequest
from app.services.memoir import (
    book_review_answer_application,
    book_review_answer_state,
    book_reviews,
    editing_jobs,
)
from app.services.memoir.access import load_memoir_project
from app.services.memoir.editing_jobs import EditingJobResult
from app.services.memoir.project_ai_preferences import select_project_ai_model


class _BookReviewAnswerApplicationCancelled(Exception):
    pass


_ACTIVE_STATUSES = {
    MemoirEditingJobStatus.PENDING.value,
    MemoirEditingJobStatus.RUNNING.value,
}


def _job_dedupe_key(project_id: str) -> str:
    return f"apply-book-review-answers:{project_id}"


async def _load_job(
    db: AsyncSession,
    *,
    project_id: str,
    job_id: str | None = None,
) -> MemoirEditingJob | None:
    statement = select(MemoirEditingJob).where(
        MemoirEditingJob.project_id == project_id,
        MemoirEditingJob.job_type
        == MemoirEditingJobType.APPLY_BOOK_REVIEW_ANSWERS.value,
    )
    if job_id:
        statement = statement.where(MemoirEditingJob.id == job_id)
    else:
        statement = statement.where(
            MemoirEditingJob.dedupe_key == _job_dedupe_key(project_id)
        )
    return await db.scalar(statement)


async def _latest_output_version(
    db: AsyncSession,
    *,
    output_id: str,
) -> MemoirBookOutputVersion | None:
    return await db.scalar(
        select(MemoirBookOutputVersion)
        .where(MemoirBookOutputVersion.output_id == output_id)
        .order_by(
            MemoirBookOutputVersion.version.desc(),
            MemoirBookOutputVersion.created_at.desc(),
            MemoirBookOutputVersion.id.desc(),
        )
        .limit(1)
    )


async def _current_answer_state(
    db: AsyncSession,
    *,
    project_id: str,
) -> tuple[int, int, bool]:
    review = await book_reviews.get_current_book_review(db, project_id=project_id)
    if review is None or review.status != "ready":
        return 0, 0, False
    context = await book_review_answer_state.load_answer_context(
        db,
        review=review,
    )
    output = await db.scalar(
        select(MemoirBookOutput).where(MemoirBookOutput.project_id == project_id)
    )
    latest = (
        await _latest_output_version(db, output_id=output.id)
        if output is not None
        else None
    )
    return (
        len(context.answers),
        context.unresolved_count,
        book_review_answer_state.marker_matches_context(
            output=output,
            version=latest,
            context=context,
        ),
    )


async def _job_state(
    db: AsyncSession,
    *,
    project_id: str,
    job: MemoirEditingJob | None,
) -> dict[str, object]:
    answer_count, unresolved_count, already_applied = await _current_answer_state(
        db,
        project_id=project_id,
    )
    if job is None:
        return {
            "project_id": project_id,
            "status": "idle",
            "answer_count": answer_count,
            "unresolved_count": unresolved_count,
            "already_applied": already_applied,
            "can_apply": (
                answer_count > 0
                and unresolved_count == 0
                and not already_applied
            ),
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
    active = status in _ACTIVE_STATUSES
    return {
        **result,
        "job_id": job.id,
        "project_id": project_id,
        "status": status,
        "answer_count": answer_count,
        "unresolved_count": unresolved_count,
        "already_applied": already_applied,
        "can_apply": (
            answer_count > 0
            and unresolved_count == 0
            and not already_applied
            and not active
        ),
        "can_cancel": active,
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


async def enqueue_book_review_answer_application_job(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookReviewAnswerApplyRequest,
    allow_admin: bool = False,
) -> dict[str, object]:
    project = await load_memoir_project(
        db,
        project_id,
        user_id,
        allow_admin=allow_admin,
    )
    existing = await _load_job(db, project_id=project_id)
    if existing is not None and existing.status in _ACTIVE_STATUSES:
        return await _job_state(db, project_id=project_id, job=existing)

    review = await book_reviews.get_current_book_review(db, project_id=project_id)
    if review is None or review.status != "ready":
        raise ValueError("请先完成当前版本的全书检查")
    context = await book_review_answer_state.load_answer_context(
        db,
        review=review,
    )
    if context.unresolved_count:
        raise ValueError("请先处理完所有需要确认的问题")
    if not context.answers:
        raise ValueError("当前没有可以应用到书稿的回答")
    output = await db.scalar(
        select(MemoirBookOutput).where(MemoirBookOutput.project_id == project_id)
    )
    latest = (
        await _latest_output_version(db, output_id=output.id)
        if output is not None
        else None
    )
    if book_review_answer_state.marker_matches_context(
        output=output,
        version=latest,
        context=context,
    ):
        return await _job_state(db, project_id=project_id, job=existing)

    selected_ai_model = select_project_ai_model(project, req.ai_model)
    job = await editing_jobs.enqueue_editing_job(
        db,
        project_id=project_id,
        job_type=MemoirEditingJobType.APPLY_BOOK_REVIEW_ANSWERS,
        dedupe_key=_job_dedupe_key(project_id),
        replace_completed=True,
        payload_json={
            "user_id": user_id,
            "allow_admin": allow_admin,
            "review_input_signature": review.input_signature,
            "answers_signature": context.answers_signature,
            "requested_ai_model": selected_ai_model,
        },
    )
    job.result_json = {
        "stage": "queued",
        "answer_count": len(context.answers),
        "applied_answer_count": 0,
        "changed_chapter_count": 0,
        "summary": "",
        "output_version_id": None,
        "output_version": None,
        "requested_ai_model": selected_ai_model,
        "resolved_ai_model": None,
    }
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return await _job_state(db, project_id=project_id, job=job)


async def get_book_review_answer_application_job(
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
    return await _job_state(
        db,
        project_id=project_id,
        job=await _load_job(db, project_id=project_id),
    )


async def cancel_book_review_answer_application_job(
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
    job = await _load_job(db, project_id=project_id, job_id=job_id)
    if job is None:
        raise ValueError("书稿调整任务不存在")
    if job.status not in _ACTIVE_STATUSES:
        return await _job_state(db, project_id=project_id, job=job)
    result_json = {
        **dict(job.result_json or {}),
        "stage": "cancelled",
        "cancelled": True,
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
    return await _job_state(db, project_id=project_id, job=job)


async def run_book_review_answer_application_job(
    db: AsyncSession,
    job: MemoirEditingJob,
) -> EditingJobResult:
    payload = dict(job.payload_json or {})
    progress = dict(job.result_json or {})
    job_id = job.id
    lease_token = job.lease_token

    async def update_progress(patch: dict[str, object]) -> None:
        progress.update(patch)
        if not await editing_jobs.update_editing_job_progress_for_lease(
            db,
            job_id=job_id,
            lease_token=lease_token,
            result_json=progress,
        ):
            raise _BookReviewAnswerApplicationCancelled

    try:
        await load_memoir_project(
            db,
            job.project_id,
            str(payload.get("user_id") or ""),
            allow_admin=payload.get("allow_admin") is True,
        )
        result = await book_review_answer_application.apply_book_review_answers(
            db,
            project_id=job.project_id,
            expected_review_input_signature=str(
                payload.get("review_input_signature") or ""
            ),
            expected_answers_signature=str(
                payload.get("answers_signature") or ""
            ),
            ai_model=(
                payload["requested_ai_model"]
                if isinstance(payload.get("requested_ai_model"), str)
                else None
            ),
            progress_callback=update_progress,
        )
    except _BookReviewAnswerApplicationCancelled:
        return EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            result_json={
                **progress,
                "stage": "cancelled",
                "cancelled": True,
            },
        )
    except book_review_answer_application.BookReviewAnswerApplicationError as exc:
        return EditingJobResult(
            status=MemoirEditingJobStatus.FAILED,
            result_json={
                **progress,
                "stage": "failed",
                "generation": exc.generation,
            },
            error_message=str(exc),
        )
    except HTTPException as exc:
        return EditingJobResult(
            status=MemoirEditingJobStatus.FAILED,
            result_json={
                **progress,
                "stage": "failed",
            },
            error_message=str(exc.detail),
        )

    return EditingJobResult(
        result_json={
            **progress,
            "stage": "completed",
            "applied_answer_count": result.applied_answer_count,
            "changed_chapter_count": result.changed_chapter_count,
            "summary": result.summary,
            "output_version_id": result.output_version_id,
            "output_version": result.output_version,
            "requested_ai_model": result.requested_ai_model,
            "resolved_ai_model": result.resolved_ai_model,
            "generation": result.generation,
        }
    )
