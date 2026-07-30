from __future__ import annotations

from typing import Literal

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


BookRevisionJobSource = Literal["review_answers", "editorial_actions"]
_JOB_TYPES = {
    "review_answers": MemoirEditingJobType.APPLY_BOOK_REVIEW_ANSWERS,
    "editorial_actions": MemoirEditingJobType.GENERATE_BOOK_REVISION_PROPOSAL,
}
_DEDUPE_PREFIXES = {
    "review_answers": "apply-book-review-answers",
    "editorial_actions": "generate-book-revision-proposal",
}


def _source_for_job(job: MemoirEditingJob) -> BookRevisionJobSource:
    if job.job_type == MemoirEditingJobType.APPLY_BOOK_REVIEW_ANSWERS.value:
        return "review_answers"
    if job.job_type == MemoirEditingJobType.GENERATE_BOOK_REVISION_PROPOSAL.value:
        return "editorial_actions"
    raise ValueError("不支持的全书修改任务")


async def _load_job(
    db: AsyncSession,
    *,
    project_id: str,
    source: BookRevisionJobSource,
    job_id: str | None = None,
) -> MemoirEditingJob | None:
    return await editing_jobs.load_project_editing_job(
        db,
        project_id=project_id,
        job_type=_JOB_TYPES[source],
        dedupe_key=f"{_DEDUPE_PREFIXES[source]}:{project_id}",
        job_id=job_id,
    )


async def _answer_state(
    db: AsyncSession,
    *,
    project_id: str,
) -> tuple[int, int, bool]:
    review = await book_reviews.get_current_book_review(db, project_id=project_id)
    if review is None or review.status != "ready":
        return 0, 0, False
    context = await book_review_answer_state.load_answer_context(db, review=review)
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
    applied = book_review_answer_state.marker_matches_context(
        output=output,
        version=latest,
        context=context,
    ) or book_editorial_actions.marker_matches_review(
        output=output,
        version=latest,
        review=review,
        answers_signature=context.answers_signature,
    )
    return len(context.answers), context.unresolved_count, applied


async def _job_state(
    db: AsyncSession,
    *,
    project_id: str,
    source: BookRevisionJobSource,
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
    result = {
        **state,
        "proposal_id": proposal.id if proposal is not None else None,
        "proposal_status": proposal.status if proposal is not None else None,
    }
    if source == "editorial_actions":
        return {
            **result,
            "selected_action_count": int(state.get("selected_action_count") or 0),
        }

    answer_count, unresolved_count, already_applied = await _answer_state(
        db,
        project_id=project_id,
    )
    result["summary"] = book_revision_generation.user_visible_application_summary(
        state.get("summary"),
        changed_chapter_count=state.get("changed_chapter_count"),
    )
    active = state.get("can_cancel") is True
    return {
        **result,
        "answer_count": answer_count,
        "unresolved_count": unresolved_count,
        "already_applied": already_applied,
        "can_apply": (
            answer_count > 0
            and unresolved_count == 0
            and not already_applied
            and not active
            and result["proposal_status"] != "ready"
        ),
    }


async def _require_applied_answers(
    db: AsyncSession,
    *,
    project_id: str,
    review: MemoirBookReview,
) -> None:
    context = await book_review_answer_state.load_answer_context(db, review=review)
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
    if (
        book_review_answer_state.marker_matches_context(
            output=output,
            version=latest,
            context=context,
        )
        or book_editorial_actions.marker_matches_review(
            output=output,
            version=latest,
            review=review,
            answers_signature=context.answers_signature,
        )
    ):
        return
    raise ValueError("请先确认根据问题回答生成的修改预览")


async def enqueue_book_revision_job(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    source: BookRevisionJobSource,
    ai_model: str | None,
    action_ids: list[str] | None = None,
    allow_admin: bool = False,
) -> dict[str, object]:
    project = await load_memoir_project(
        db,
        project_id,
        user_id,
        allow_admin=allow_admin,
    )
    existing = await _load_job(db, project_id=project_id, source=source)
    if (
        existing is not None
        and existing.status in editing_jobs.ACTIVE_EDITING_JOB_STATUSES
    ):
        return await _job_state(
            db,
            project_id=project_id,
            source=source,
            job=existing,
        )

    review = await book_reviews.get_current_book_review(db, project_id=project_id)
    if review is None or review.status != "ready":
        raise ValueError("请先完成当前版本的全书检查")
    selected_ai_model = select_project_ai_model(project, ai_model)
    payload: dict[str, object] = {
        "user_id": user_id,
        "allow_admin": allow_admin,
        "review_input_signature": review.input_signature,
        "requested_ai_model": selected_ai_model,
    }
    result: dict[str, object] = {
        "stage": "queued",
        "proposal_id": None,
        "proposal_status": None,
        "requested_ai_model": selected_ai_model,
        "resolved_ai_model": None,
    }
    if source == "review_answers":
        context = await book_review_answer_state.load_answer_context(
            db,
            review=review,
        )
        if context.unresolved_count:
            raise ValueError("请先处理完所有需要确认的问题")
        if not context.answers:
            raise ValueError("当前没有可以应用到书稿的回答")
        _, _, already_applied = await _answer_state(db, project_id=project_id)
        if already_applied:
            return await _job_state(
                db,
                project_id=project_id,
                source=source,
                job=existing,
            )
        payload["answers_signature"] = context.answers_signature
        result.update(
            answer_count=len(context.answers),
            changed_chapter_count=0,
            summary="",
        )
    else:
        selected_ids = [
            value.strip()
            for value in dict.fromkeys(action_ids or [])
            if value.strip()
        ]
        actions = book_editorial_actions.selected_review_actions(
            review.review_json,
            selected_ids,
        )
        await _require_applied_answers(
            db,
            project_id=project_id,
            review=review,
        )
        payload.update(
            action_ids=selected_ids,
            actions_signature=book_editorial_actions.actions_signature(actions),
        )
        result["selected_action_count"] = len(actions)

    job = await editing_jobs.enqueue_editing_job(
        db,
        project_id=project_id,
        job_type=_JOB_TYPES[source],
        dedupe_key=f"{_DEDUPE_PREFIXES[source]}:{project_id}",
        replace_completed=True,
        payload_json=payload,
    )
    job.result_json = result
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return await _job_state(
        db,
        project_id=project_id,
        source=source,
        job=job,
    )


async def get_book_revision_job(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    source: BookRevisionJobSource,
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
        source=source,
        job=await _load_job(db, project_id=project_id, source=source),
    )


async def cancel_book_revision_job(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    source: BookRevisionJobSource,
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
        source=source,
        job_id=job_id,
    )
    if job is None:
        message = (
            "书稿调整任务不存在"
            if source == "review_answers"
            else "全书修改预览任务不存在"
        )
        raise ValueError(message)
    await editing_jobs.cancel_project_editing_job(db, job=job)
    return await _job_state(
        db,
        project_id=project_id,
        source=source,
        job=job,
    )


async def run_book_revision_job(
    db: AsyncSession,
    job: MemoirEditingJob,
) -> EditingJobResult:
    source = _source_for_job(job)
    payload = dict(job.payload_json or {})
    progress = dict(job.result_json or {})
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
        ai_model = (
            payload["requested_ai_model"]
            if isinstance(payload.get("requested_ai_model"), str)
            else None
        )
        if source == "review_answers":
            generated = await book_revision_generation.apply_book_review_answers(
                db,
                project_id=job.project_id,
                expected_review_input_signature=str(
                    payload.get("review_input_signature") or ""
                ),
                expected_answers_signature=str(
                    payload.get("answers_signature") or ""
                ),
                ai_model=ai_model,
                progress_callback=update_progress,
            )
        else:
            raw_action_ids = payload.get("action_ids")
            generated = await (
                book_revision_generation.generate_editorial_revision_proposal(
                    db,
                    project_id=job.project_id,
                    expected_review_input_signature=str(
                        payload.get("review_input_signature") or ""
                    ),
                    expected_actions_signature=str(
                        payload.get("actions_signature") or ""
                    ),
                    action_ids=(
                        [str(value) for value in raw_action_ids]
                        if isinstance(raw_action_ids, list)
                        else []
                    ),
                    ai_model=ai_model,
                    progress_callback=update_progress,
                )
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

    result = {
        **progress,
        "stage": "completed",
        "proposal_id": generated.proposal_id,
        "proposal_status": "ready" if generated.proposal_id else None,
        "requested_ai_model": generated.requested_ai_model,
        "resolved_ai_model": generated.resolved_ai_model,
        "generation": generated.generation,
    }
    if source == "review_answers":
        result.update(
            changed_chapter_count=generated.changed_chapter_count,
            summary=generated.summary,
        )
    else:
        result["selected_action_count"] = generated.selected_action_count
    return EditingJobResult(result_json=result)
