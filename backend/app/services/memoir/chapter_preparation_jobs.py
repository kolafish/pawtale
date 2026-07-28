from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import MemoirEditingJobStatus, MemoirEditingJobType
from app.models.memoir import MemoirEditingJob
from app.schemas.memoir_narrative import MemoirChapterPreparationJobRequest
from app.services.memoir import chapter_digests, chapter_generation, editing_jobs
from app.services.memoir.access import load_memoir_project
from app.services.memoir.editing_jobs import EditingJobResult
from app.services.memoir.narrative import book_completion
from app.services.memoir.project_ai_preferences import select_project_ai_model


_ACTIVE_STATUSES = {
    MemoirEditingJobStatus.PENDING.value,
    MemoirEditingJobStatus.RUNNING.value,
}
_CHECKPOINT_KEYS = {
    "checkpoint_input_signature",
    "checkpoint_sections",
    "checkpoint_missing_source_candidates",
    "checkpoint_editor_notes",
    "checkpoint_model",
}


def _job_dedupe_key(chapter_id: str) -> str:
    return f"prepare-chapter:{chapter_id}"


def _public_result(result_json: dict) -> dict:
    return {
        key: value
        for key, value in result_json.items()
        if key not in _CHECKPOINT_KEYS
    }


def _job_state(
    *,
    project_id: str,
    chapter_id: str,
    job: MemoirEditingJob | None,
) -> dict[str, object]:
    if job is None:
        return {
            "project_id": project_id,
            "chapter_id": chapter_id,
            "status": "idle",
        }
    result = _public_result(dict(job.result_json or {}))
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
        "chapter_id": chapter_id,
        "status": status,
        "can_cancel": status in _ACTIVE_STATUSES,
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
    chapter_id: str,
    job_id: str | None = None,
) -> MemoirEditingJob | None:
    statement = select(MemoirEditingJob).where(
        MemoirEditingJob.project_id == project_id,
        MemoirEditingJob.chapter_id == chapter_id,
        MemoirEditingJob.job_type == MemoirEditingJobType.PREPARE_CHAPTER.value,
    )
    if job_id:
        statement = statement.where(MemoirEditingJob.id == job_id)
    else:
        statement = statement.where(
            MemoirEditingJob.dedupe_key == _job_dedupe_key(chapter_id)
        )
    return await db.scalar(statement)


async def enqueue_chapter_preparation_job(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    chapter_id: str,
    req: MemoirChapterPreparationJobRequest,
    allow_admin: bool = False,
) -> dict[str, object]:
    project = await load_memoir_project(
        db,
        project_id,
        user_id,
        allow_admin=allow_admin,
    )
    await book_completion.ensure_chapter_sections(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        user_id=user_id,
    )
    context = await chapter_generation.load_chapter_generation_context(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    generation_plan = await chapter_generation.load_chapter_draft_generation_plan(
        db,
        context=context,
    )
    selected_ai_model = select_project_ai_model(project, req.ai_model)
    existing = await _load_job(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    if existing is not None and existing.status in _ACTIVE_STATUSES:
        return _job_state(
            project_id=project_id,
            chapter_id=chapter_id,
            job=existing,
        )

    previous_result = dict(existing.result_json or {}) if existing else {}
    previous_payload = dict(existing.payload_json or {}) if existing else {}
    can_resume = bool(
        existing is not None
        and existing.status == MemoirEditingJobStatus.FAILED.value
        and previous_payload.get("operation") == req.operation
        and previous_payload.get("requested_ai_model") == selected_ai_model
        and previous_payload.get("input_signature") == context.input_signature
        and previous_result.get("checkpoint_input_signature")
        == context.input_signature
    )
    candidate_checkpoint = (
        {
            key: previous_result[key]
            for key in _CHECKPOINT_KEYS
            if key in previous_result
        }
        if can_resume
        else {}
    )
    resumed_section_count = (
        chapter_generation.chapter_generation_checkpoint_section_count(
            generation_plan.target_context,
            candidate_checkpoint,
        )
    )
    checkpoint = candidate_checkpoint if resumed_section_count else {}
    queued_section_count = (
        len(generation_plan.target_context.sections)
        if req.operation == "rewrite"
        else len(context.sections)
    )
    job = await editing_jobs.enqueue_editing_job(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        job_type=MemoirEditingJobType.PREPARE_CHAPTER,
        dedupe_key=_job_dedupe_key(chapter_id),
        replace_completed=True,
        payload_json={
            "user_id": user_id,
            "allow_admin": allow_admin,
            "operation": req.operation,
            "requested_ai_model": selected_ai_model,
            "input_signature": context.input_signature,
        },
    )
    job.result_json = {
        "stage": "queued",
        "operation": req.operation,
        "total_section_count": queued_section_count,
        "checked_section_count": 0,
        "completed_section_count": resumed_section_count,
        "resumed_section_count": resumed_section_count,
        "requested_ai_model": selected_ai_model,
        **checkpoint,
    }
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return _job_state(
        project_id=project_id,
        chapter_id=chapter_id,
        job=job,
    )


async def get_chapter_preparation_job(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    chapter_id: str,
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
        chapter_id=chapter_id,
    )
    return _job_state(
        project_id=project_id,
        chapter_id=chapter_id,
        job=job,
    )


async def cancel_chapter_preparation_job(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    chapter_id: str,
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
        chapter_id=chapter_id,
        job_id=job_id,
    )
    if job is None:
        raise ValueError("章节处理任务不存在")
    await editing_jobs.cancel_project_editing_job(db, job=job)
    return _job_state(
        project_id=project_id,
        chapter_id=chapter_id,
        job=job,
    )


async def run_chapter_preparation_job(
    db: AsyncSession,
    job: MemoirEditingJob,
) -> EditingJobResult:
    project_id = job.project_id
    chapter_id = str(job.chapter_id or "")
    payload = dict(job.payload_json or {})
    operation = str(payload.get("operation") or "prepare")
    if operation not in {"prepare", "rewrite"}:
        raise ValueError("章节处理任务类型无效")
    progress = dict(job.result_json or {})
    update_progress = editing_jobs.editing_job_progress_callback(
        db,
        job=job,
        progress=progress,
    )

    try:
        if operation == "rewrite":
            workspace = await book_completion.rewrite_chapter(
                db,
                project_id=project_id,
                chapter_id=chapter_id,
                user_id=str(payload.get("user_id") or ""),
                ai_model=(
                    payload["requested_ai_model"]
                    if isinstance(payload.get("requested_ai_model"), str)
                    else None
                ),
                checkpoint=progress,
                progress_callback=update_progress,
            )
        else:
            workspace = await book_completion.prepare_chapter(
                db,
                project_id=project_id,
                chapter_id=chapter_id,
                user_id=str(payload.get("user_id") or ""),
                ai_model=(
                    payload["requested_ai_model"]
                    if isinstance(payload.get("requested_ai_model"), str)
                    else None
                ),
                checkpoint=progress,
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

    digest = await chapter_digests.get_current_chapter_digest(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    generation = (
        digest.digest_json.get("generation", {})
        if digest is not None and isinstance(digest.digest_json, dict)
        else {}
    )
    resolved_model = (
        progress.get("checkpoint_model")
        or (
            generation.get("model")
            if isinstance(generation, dict)
            else None
        )
        or payload.get("requested_ai_model")
    )
    completed = _public_result(progress)
    outcome = workspace.completion_summary.state
    if outcome == "preparing":
        outcome = "completed"
    completed.update(
        {
            "stage": "completed",
            "operation": operation,
            "outcome": outcome,
            "current_section_id": None,
            "current_section_title": "",
            "resolved_ai_model": resolved_model,
        }
    )
    return EditingJobResult(result_json=completed)
