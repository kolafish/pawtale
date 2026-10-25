from __future__ import annotations

from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import MemoirEditingJobType
from app.models.memoir import (
    MemoirBookOutput,
    MemoirBookRevisionProposal,
    MemoirEditingJob,
)
from app.services.memoir import (
    book_editorial_actions,
    book_output_versions,
    book_review_answer_state,
    book_revision_generation,
    book_revision_proposals,
    book_reviews,
    editing_jobs,
    revision_job_workflow,
)
from app.services.memoir.access import load_memoir_project
from app.services.memoir.project_ai_preferences import select_project_ai_model


BookRevisionJobSource = Literal[
    "review_selection",
    "proposal_feedback",
    "proposal_draft",
]
_DEDUPE_PREFIXES = {
    "review_selection": "generate-book-review-revision",
    "proposal_feedback": "refine-book-revision-proposal",
    "proposal_draft": "generate-book-revision-draft",
}


def _source_for_job(job: MemoirEditingJob) -> BookRevisionJobSource:
    if job.job_type == MemoirEditingJobType.GENERATE_BOOK_REVISION_PROPOSAL.value:
        payload_source = (job.payload_json or {}).get("source")
        if payload_source in {
            "review_selection",
            "proposal_feedback",
            "proposal_draft",
        }:
            return payload_source
    raise ValueError("不支持的全书修改任务")


def _dedupe_key(
    source: BookRevisionJobSource,
    project_id: str,
    proposal_id: str | None,
) -> str:
    suffix = f":{proposal_id}" if source in {"proposal_feedback", "proposal_draft"} else ""
    return f"{_DEDUPE_PREFIXES[source]}:{project_id}{suffix}"


async def _load_job(
    db: AsyncSession,
    *,
    project_id: str,
    source: BookRevisionJobSource,
    proposal_id: str | None = None,
    job_id: str | None = None,
) -> MemoirEditingJob | None:
    return await editing_jobs.load_project_editing_job(
        db,
        project_id=project_id,
        job_type=MemoirEditingJobType.GENERATE_BOOK_REVISION_PROPOSAL,
        dedupe_key=_dedupe_key(source, project_id, proposal_id),
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
    output = await db.scalar(select(MemoirBookOutput).where(MemoirBookOutput.project_id == project_id))
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
        "proposal_phase": proposal.phase if proposal is not None else None,
    }
    if source in {"proposal_feedback", "proposal_draft"}:
        return {
            **result,
            "changed_chapter_count": int(state.get("changed_chapter_count") or 0),
            "summary": str(state.get("summary") or ""),
        }

    answer_count, unresolved_count, already_applied = await _answer_state(
        db,
        project_id=project_id,
    )
    return {
        **result,
        "include_answers": state.get("include_answers") is True,
        "answer_count": answer_count,
        "unresolved_count": unresolved_count,
        "action_ids": (
            [str(value) for value in state.get("action_ids", [])]
            if isinstance(state.get("action_ids"), list)
            else []
        ),
        "selected_action_count": int(state.get("selected_action_count") or 0),
        "changed_chapter_count": int(state.get("changed_chapter_count") or 0),
        "summary": str(state.get("summary") or ""),
        "already_applied": already_applied,
    }


async def enqueue_book_revision_job(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    source: BookRevisionJobSource,
    ai_model: str | None,
    include_answers: bool = False,
    action_ids: list[str] | None = None,
    proposal_id: str | None = None,
    feedback: str | None = None,
    allow_admin: bool = False,
) -> dict[str, object]:
    project = await load_memoir_project(
        db,
        project_id,
        user_id,
        allow_admin=allow_admin,
    )
    existing = await _load_job(
        db,
        project_id=project_id,
        source=source,
        proposal_id=proposal_id,
    )
    if existing is not None and existing.status in editing_jobs.ACTIVE_EDITING_JOB_STATUSES:
        return await _job_state(
            db,
            project_id=project_id,
            source=source,
            job=existing,
        )

    review = await book_reviews.get_current_book_review(
        db,
        project_id=project_id,
    )
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
    if source == "review_selection":
        payload["source"] = "review_selection"
        context = await book_review_answer_state.load_answer_context(
            db,
            review=review,
        )
        _, _, already_applied = await _answer_state(db, project_id=project_id)
        if include_answers and not context.answers:
            raise ValueError("当前没有可以应用到书稿的回答")
        if include_answers and already_applied:
            raise ValueError("这些回答已经应用到当前书稿")
        selected_ids = [value.strip() for value in dict.fromkeys(action_ids or []) if value.strip()]
        actions = (
            book_editorial_actions.selected_review_actions(
                review.review_json,
                selected_ids,
            )
            if selected_ids
            else []
        )
        if not include_answers and not actions:
            raise ValueError("请至少选择回答或一条编辑建议")
        related_ids = (
            (context.related_chapter_ids if include_answers else set())
            | {
                str(chapter_id)
                for action in actions
                for chapter_id in action.get("chapter_ids", [])
            }
        )
        if await book_revision_generation.selected_revision_has_pending_decision(
            db, project_id=project_id,
            review_input_signature=review.input_signature,
            chapter_ids=set(related_ids),
        ):
            raise ValueError("请先处理所选章节需要确认的问题")
        payload.update(
            include_answers=include_answers,
            answers_signature=(context.answers_signature if include_answers else ""),
            action_ids=selected_ids,
            actions_signature=book_editorial_actions.actions_signature(actions),
        )
        result.update(
            include_answers=include_answers,
            answer_count=len(context.answers),
            action_ids=selected_ids,
            selected_action_count=len(actions),
            changed_chapter_count=0,
            summary="",
        )
    elif source == "proposal_feedback":
        feedback_text = (feedback or "").strip()
        if not proposal_id or not feedback_text:
            raise ValueError("请先输入对这版修改的调整意见")
        proposal = await book_revision_proposals.load_proposal(
            db,
            project_id=project_id,
            proposal_id=proposal_id,
        )
        proposal_response = await book_revision_proposals.proposal_response(
            db,
            proposal=proposal,
            preview="none",
        )
        if (
            proposal_response.status != "ready"
            or not proposal_response.can_dismiss
            or not proposal.revision_plan_json
        ):
            raise ValueError("这个修改思路已经不能继续调整")
        payload.update(
            source="proposal_feedback",
            proposal_id=proposal.id,
            feedback=feedback_text,
            plan_signature=book_revision_proposals.revision_plan_signature(proposal),
        )
        result.update(
            proposal_id=proposal.id,
            proposal_status=proposal.status,
            changed_chapter_count=0,
            summary="",
        )
    else:
        if not proposal_id:
            raise ValueError("缺少要生成修改稿的修改思路")
        proposal = await book_revision_proposals.load_proposal(
            db,
            project_id=project_id,
            proposal_id=proposal_id,
        )
        proposal_response = await book_revision_proposals.proposal_response(
            db,
            proposal=proposal,
            preview="none",
        )
        if proposal_response.status != "ready" or proposal.phase != "plan_ready" or not proposal.revision_plan_json:
            raise ValueError("当前修改思路不能生成修改稿")
        payload.update(
            source="proposal_draft",
            proposal_id=proposal.id,
            plan_signature=book_revision_proposals.revision_plan_signature(proposal),
        )
        result.update(
            proposal_id=proposal.id,
            proposal_status=proposal.status,
            proposal_phase=proposal.phase,
            changed_chapter_count=0,
            summary="",
        )

    job = await editing_jobs.enqueue_editing_job(
        db,
        project_id=project_id,
        job_type=MemoirEditingJobType.GENERATE_BOOK_REVISION_PROPOSAL,
        dedupe_key=_dedupe_key(source, project_id, proposal_id),
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
    proposal_id: str | None = None,
    allow_admin: bool = False,
) -> dict[str, object]:
    await load_memoir_project(
        db,
        project_id,
        user_id,
        allow_admin=allow_admin,
    )
    return await book_revision_job_state(
        db,
        project_id=project_id,
        source=source,
        proposal_id=proposal_id,
    )


async def book_revision_job_state(
    db: AsyncSession,
    *,
    project_id: str,
    source: BookRevisionJobSource,
    proposal_id: str | None = None,
) -> dict[str, object]:
    return await _job_state(
        db,
        project_id=project_id,
        source=source,
        job=await _load_job(
            db,
            project_id=project_id,
            source=source,
            proposal_id=proposal_id,
        ),
    )


async def cancel_book_revision_job(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    source: BookRevisionJobSource,
    job_id: str,
    proposal_id: str | None = None,
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
        proposal_id=proposal_id,
        job_id=job_id,
    )
    if job is None:
        raise ValueError("全书修改任务不存在")
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
) -> editing_jobs.EditingJobResult:
    source = _source_for_job(job)

    async def execute(
        payload: dict[str, object],
        update_progress: revision_job_workflow.ProgressCallback,
    ) -> dict[str, object]:
        await load_memoir_project(
            db,
            job.project_id,
            str(payload.get("user_id") or ""),
            allow_admin=payload.get("allow_admin") is True,
        )
        ai_model = payload["requested_ai_model"] if isinstance(payload.get("requested_ai_model"), str) else None
        if source == "review_selection":
            raw_action_ids = payload.get("action_ids")
            generated = await book_revision_generation.generate_book_revision_proposal(
                db,
                project_id=job.project_id,
                expected_review_input_signature=str(payload.get("review_input_signature") or ""),
                expected_answers_signature=str(payload.get("answers_signature") or ""),
                expected_actions_signature=str(payload.get("actions_signature") or ""),
                include_answers=payload.get("include_answers") is True,
                action_ids=([str(value) for value in raw_action_ids] if isinstance(raw_action_ids, list) else []),
                ai_model=ai_model,
                progress_callback=update_progress,
            )
        elif source == "proposal_feedback":
            generated = await book_revision_generation.refine_book_revision_proposal(
                db,
                project_id=job.project_id,
                proposal_id=str(payload.get("proposal_id") or ""),
                feedback=str(payload.get("feedback") or ""),
                expected_plan_signature=str(
                    payload.get("plan_signature") or ""
                ),
                ai_model=ai_model,
                progress_callback=update_progress,
            )
        else:
            generated = await book_revision_generation.generate_book_revision_draft(
                db,
                project_id=job.project_id,
                proposal_id=str(payload.get("proposal_id") or ""),
                expected_plan_signature=str(payload.get("plan_signature") or ""),
                ai_model=ai_model,
                progress_callback=update_progress,
            )
        return {
            "proposal_id": generated.proposal_id,
            "proposal_status": "ready" if generated.proposal_id else None,
            "proposal_phase": generated.proposal_phase,
            "requested_ai_model": generated.requested_ai_model,
            "resolved_ai_model": generated.resolved_ai_model,
            "generation": generated.generation,
            "changed_chapter_count": generated.changed_chapter_count,
            "selected_action_count": generated.selected_action_count,
            "summary": generated.summary,
        }

    return await revision_job_workflow.run(db, job, execute)
