from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import MemoirEditingJobStatus, MemoirEditingJobType
from app.models.memoir import (
    MemoirBookOutput,
    MemoirBookRevisionProposal,
    MemoirBookReview,
    MemoirEditingJob,
)
from app.schemas.memoir_book_review import MemoirBookEditorialRevisionRequest
from app.services.memoir import (
    book_editorial_actions,
    book_output_versions,
    book_review_answer_state,
    book_revision_generation,
    book_reviews,
    editing_jobs,
)
from app.services.memoir.access import load_memoir_project
from app.services.memoir.editing_jobs import EditingJobResult
from app.services.memoir.project_ai_preferences import select_project_ai_model


def _job_dedupe_key(project_id: str) -> str:
    return f"generate-book-revision-proposal:{project_id}"


async def _load_job(
    db: AsyncSession,
    *,
    project_id: str,
    job_id: str | None = None,
) -> MemoirEditingJob | None:
    return await editing_jobs.load_project_editing_job(
        db,
        project_id=project_id,
        job_type=MemoirEditingJobType.GENERATE_BOOK_REVISION_PROPOSAL,
        dedupe_key=_job_dedupe_key(project_id),
        job_id=job_id,
    )


async def _job_state(
    db: AsyncSession,
    *,
    project_id: str,
    job: MemoirEditingJob | None,
) -> dict[str, object]:
    state = editing_jobs.project_editing_job_state(
        project_id=project_id,
        job=job,
    )
    proposal = None
    if job is not None:
        proposal_id = str((job.result_json or {}).get("proposal_id") or "")
        if proposal_id:
            proposal = await db.get(MemoirBookRevisionProposal, proposal_id)
    return {
        **state,
        "selected_action_count": int(state.get("selected_action_count") or 0),
        "proposal_id": proposal.id if proposal is not None else None,
        "proposal_status": proposal.status if proposal is not None else None,
    }


async def _require_applied_answers(
    db: AsyncSession,
    *,
    project_id: str,
    review: MemoirBookReview,
) -> None:
    context = await book_review_answer_state.load_answer_context(
        db,
        review=review,
    )
    if context.unresolved_count:
        raise ValueError("请先处理完所有需要确认的问题")
    if not context.answers:
        return
    output = await db.scalar(
        select(MemoirBookOutput).where(MemoirBookOutput.project_id == project_id)
    )
    latest = (
        await book_output_versions.load_latest_book_output_version(
            db,
            output_id=output.id,
        )
        if output is not None
        else None
    )
    if book_review_answer_state.marker_matches_context(
        output=output,
        version=latest,
        context=context,
    ):
        return
    if book_editorial_actions.marker_matches_review(
        output=output,
        version=latest,
        review=review,
        answers_signature=context.answers_signature,
    ):
        return
    raise ValueError("请先确认根据问题回答生成的修改预览")


async def enqueue_editorial_revision_job(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookEditorialRevisionRequest,
    allow_admin: bool = False,
) -> dict[str, object]:
    project = await load_memoir_project(
        db,
        project_id,
        user_id,
        allow_admin=allow_admin,
    )
    existing = await _load_job(db, project_id=project_id)
    if (
        existing is not None
        and existing.status in editing_jobs.ACTIVE_EDITING_JOB_STATUSES
    ):
        return await _job_state(db, project_id=project_id, job=existing)
    review = await book_reviews.get_current_book_review(
        db,
        project_id=project_id,
    )
    if review is None or review.status != "ready":
        raise ValueError("请先完成当前版本的全书检查")
    action_ids = [
        value.strip() for value in dict.fromkeys(req.action_ids) if value.strip()
    ]
    actions = book_editorial_actions.selected_review_actions(
        review.review_json,
        action_ids,
    )
    await _require_applied_answers(
        db,
        project_id=project_id,
        review=review,
    )
    selected_ai_model = select_project_ai_model(project, req.ai_model)
    job = await editing_jobs.enqueue_editing_job(
        db,
        project_id=project_id,
        job_type=MemoirEditingJobType.GENERATE_BOOK_REVISION_PROPOSAL,
        dedupe_key=_job_dedupe_key(project_id),
        replace_completed=True,
        payload_json={
            "user_id": user_id,
            "allow_admin": allow_admin,
            "review_input_signature": review.input_signature,
            "action_ids": action_ids,
            "actions_signature": book_editorial_actions.actions_signature(actions),
            "requested_ai_model": selected_ai_model,
        },
    )
    job.result_json = {
        "stage": "queued",
        "selected_action_count": len(actions),
        "proposal_id": None,
        "proposal_status": None,
        "requested_ai_model": selected_ai_model,
        "resolved_ai_model": None,
    }
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return await _job_state(db, project_id=project_id, job=job)


async def get_editorial_revision_job(
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


async def cancel_editorial_revision_job(
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
        raise ValueError("全书修改预览任务不存在")
    await editing_jobs.cancel_project_editing_job(db, job=job)
    return await _job_state(db, project_id=project_id, job=job)


async def run_editorial_revision_job(
    db: AsyncSession,
    job: MemoirEditingJob,
) -> EditingJobResult:
    payload = dict(job.payload_json or {})
    progress = dict(job.result_json or {})
    raw_action_ids = payload.get("action_ids")
    action_ids = (
        [str(value) for value in raw_action_ids]
        if isinstance(raw_action_ids, list)
        else []
    )
    update_progress = editing_jobs.editing_job_progress_callback(
        db,
        job=job,
        progress=progress,
    )
    try:
        await load_memoir_project(
            db,
            job.project_id,
            str(payload.get("user_id") or ""),
            allow_admin=payload.get("allow_admin") is True,
        )
        result = await book_revision_generation.generate_editorial_revision_proposal(
            db,
            project_id=job.project_id,
            expected_review_input_signature=str(
                payload.get("review_input_signature") or ""
            ),
            expected_actions_signature=str(payload.get("actions_signature") or ""),
            action_ids=action_ids,
            ai_model=(
                payload["requested_ai_model"]
                if isinstance(payload.get("requested_ai_model"), str)
                else None
            ),
            progress_callback=update_progress,
        )
    except editing_jobs.EditingJobProgressLost:
        return EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            result_json={
                **progress,
                "stage": "cancelled",
                "cancelled": True,
            },
        )
    except book_revision_generation.BookRevisionError as exc:
        return EditingJobResult(
            status=MemoirEditingJobStatus.FAILED,
            result_json={
                **progress,
                "stage": "failed",
                "generation": exc.generation,
            },
            error_message=str(exc),
        )
    except (HTTPException, ValueError) as exc:
        return EditingJobResult(
            status=MemoirEditingJobStatus.FAILED,
            result_json={**progress, "stage": "failed"},
            error_message=(
                str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
            ),
        )
    return EditingJobResult(
        result_json={
            **progress,
            "stage": "completed",
            "selected_action_count": result.selected_action_count,
            "proposal_id": result.proposal_id,
            "proposal_status": "ready",
            "requested_ai_model": result.requested_ai_model,
            "resolved_ai_model": result.resolved_ai_model,
            "generation": result.generation,
        }
    )
