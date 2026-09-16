from __future__ import annotations

from collections.abc import Awaitable, Callable
from difflib import SequenceMatcher

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import MemoirEditingJobStatus, MemoirEditingJobType
from app.models.memoir import MemoirBookChapterDraftVersion, MemoirEditingJob
from app.schemas.memoir_narrative import MemoirChapterPreparationJobRequest
from app.services.memoir import chapter_digests, chapter_generation, editing_jobs
from app.services.memoir.access import load_memoir_project
from app.services.memoir.book_output_snapshots import legacy_chapter_version_needs_upgrade
from app.services.memoir.editing_jobs import EditingJobResult
from app.services.memoir.narrative import book_completion
from app.services.memoir.project_ai_preferences import select_project_ai_model


_ACTIVE_STATUSES = editing_jobs.ACTIVE_EDITING_JOB_STATUSES
_CHECKPOINT_KEYS = {
    "checkpoint_input_signature",
    "checkpoint_sections",
    "checkpoint_missing_source_candidates",
    "checkpoint_editor_notes",
    "checkpoint_model",
}
_PRIVATE_RESULT_KEYS = _CHECKPOINT_KEYS | {
    "candidate_diff_segments",
    "candidate_sections",
}
_DIFF_EQUAL_MERGE_MAX_CHARS = 4
_LEGACY_SECTION_UPGRADE_REVISION = "skip-empty-sections-v1"


def _job_dedupe_key(chapter_id: str) -> str:
    return f"prepare-chapter:{chapter_id}"


def _public_result(result_json: dict) -> dict:
    result = {
        key: value
        for key, value in result_json.items()
        if key not in _PRIVATE_RESULT_KEYS
    }
    if result.get("candidate_content"):
        result["candidate_sections"] = [
            {
                "section_id": str(section.get("section_id") or ""),
                "title": str(section.get("title") or ""),
                "content": str(section.get("content") or ""),
            }
            for section in result_json.get("checkpoint_sections", [])
            if isinstance(section, dict)
            and section.get("section_id")
            and section.get("content")
        ]
    return result


def _append_diff_segment(segments: list[dict[str, str]], kind: str, text: str) -> None:
    if not text:
        return
    if segments and segments[-1]["kind"] == kind:
        segments[-1]["text"] += text
    else:
        segments.append({"kind": kind, "text": text})


def _candidate_diff_segments(before: str, after: str) -> list[dict[str, str]]:
    opcodes = SequenceMatcher(None, before, after, autojunk=False).get_opcodes()
    segments: list[dict[str, str]] = []
    index = 0
    while index < len(opcodes):
        tag, before_start, before_end, after_start, after_end = opcodes[index]
        if tag == "equal":
            _append_diff_segment(segments, "equal", after[after_start:after_end])
            index += 1
            continue

        index += 1
        while index + 1 < len(opcodes):
            equal = opcodes[index]
            changed = opcodes[index + 1]
            if (
                equal[0] != "equal"
                or changed[0] == "equal"
                or equal[2] - equal[1] > _DIFF_EQUAL_MERGE_MAX_CHARS
            ):
                break
            before_end = changed[2]
            after_end = changed[4]
            index += 2

        before_text = before[before_start:before_end]
        after_text = after[after_start:after_end]
        if before_text and after_text:
            _append_diff_segment(segments, "deleted", before_text)
            _append_diff_segment(segments, "modified", after_text)
        elif before_text:
            _append_diff_segment(segments, "deleted", before_text)
        else:
            _append_diff_segment(segments, "added", after_text)
    return segments


async def _with_candidate_diff(
    db: AsyncSession,
    *,
    state: dict[str, object],
    project_id: str,
    chapter_id: str,
) -> dict[str, object]:
    candidate_content = state.get("candidate_content")
    base_version_id = state.get("candidate_base_version_id")
    if not isinstance(candidate_content, str) or not candidate_content or not base_version_id:
        return state
    base = await db.get(MemoirBookChapterDraftVersion, str(base_version_id))
    if base is None or base.project_id != project_id or base.chapter_id != chapter_id:
        return state
    return {
        **state,
        "candidate_diff_segments": _candidate_diff_segments(
            base.content,
            candidate_content,
        ),
    }


def _job_state(
    *,
    project_id: str,
    chapter_id: str,
    job: MemoirEditingJob | None,
) -> dict[str, object]:
    return {
        **_public_result(editing_jobs.project_editing_job_state(project_id=project_id, job=job)),
        "chapter_id": chapter_id,
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
    on_enqueued: Callable[[MemoirEditingJob], Awaitable[None]] | None = None,
    automatic: bool = False,
    retry: bool = False,
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
    latest = await chapter_generation.latest_chapter_draft_version(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    needs_legacy_upgrade = legacy_chapter_version_needs_upgrade(latest)
    job_input_signature = (
        f"{context.input_signature}:{_LEGACY_SECTION_UPGRADE_REVISION}"
        if needs_legacy_upgrade
        else context.input_signature
    )
    selected_ai_model = select_project_ai_model(project, req.ai_model)
    existing = await _load_job(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    if existing is not None and existing.status in _ACTIVE_STATUSES:
        if on_enqueued is not None:
            await on_enqueued(existing)
        return _job_state(
            project_id=project_id,
            chapter_id=chapter_id,
            job=existing,
        )

    previous_result = dict(existing.result_json or {}) if existing else {}
    previous_payload = dict(existing.payload_json or {}) if existing else {}
    from app.services.memoir import completion_issues
    decision_resolved = previous_result.get("outcome") == "needs_input" and not any(
        issue.channel == "manual_review" for issue in await completion_issues.list_issues(
            db, project_id=project_id, chapter_id=chapter_id,
        )
    )
    if (
        automatic
        and not retry
        and not decision_resolved
        and existing is not None
        and (
            previous_payload.get("input_signature") == job_input_signature
            or (
                needs_legacy_upgrade
                and existing.status != MemoirEditingJobStatus.FAILED.value
                and previous_payload.get("input_signature")
                == context.input_signature
            )
        )
    ):
        if on_enqueued is not None:
            await on_enqueued(existing)
        return _job_state(project_id=project_id, chapter_id=chapter_id, job=existing)
    can_resume = bool(
        existing is not None
        and existing.status == MemoirEditingJobStatus.FAILED.value
        and previous_payload.get("operation") == req.operation
        and previous_payload.get("requested_ai_model") == selected_ai_model
        and previous_payload.get("input_signature") == job_input_signature
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
    if existing is not None:
        existing.dedupe_key = None
        db.add(existing)
        await db.flush()
    job = await editing_jobs.enqueue_editing_job(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        job_type=MemoirEditingJobType.PREPARE_CHAPTER,
        dedupe_key=_job_dedupe_key(chapter_id),
        payload_json={
            "user_id": user_id,
            "allow_admin": allow_admin,
            "operation": req.operation,
            "requested_ai_model": selected_ai_model,
            "input_signature": job_input_signature,
            "automatic": automatic,
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
    if on_enqueued is not None:
        await on_enqueued(job)
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
    return await _with_candidate_diff(
        db,
        state=_job_state(
            project_id=project_id,
            chapter_id=chapter_id,
            job=job,
        ),
        project_id=project_id,
        chapter_id=chapter_id,
    )


async def current_chapter_candidates(
    db: AsyncSession, *, project_id: str, chapter_ids: set[str],
) -> dict[str, str]:
    """Project actionable candidates without sending manuscript text to the home page."""
    if not chapter_ids:
        return {}
    jobs = (await db.scalars(select(MemoirEditingJob).where(
        MemoirEditingJob.project_id == project_id,
        MemoirEditingJob.chapter_id.in_(chapter_ids),
        MemoirEditingJob.job_type == MemoirEditingJobType.PREPARE_CHAPTER.value,
        MemoirEditingJob.status == MemoirEditingJobStatus.SUCCEEDED.value,
        MemoirEditingJob.dedupe_key.is_not(None),
    ))).all()
    candidates = {}
    for job in jobs:
        result = job.result_json or {}
        if (
            job.dedupe_key != _job_dedupe_key(job.chapter_id)
            or result.get("outcome") != "needs_review" or not result.get("candidate_content")
        ):
            continue
        try:
            context = await chapter_generation.load_chapter_generation_context(
                db, project_id=project_id, chapter_id=job.chapter_id,
            )
        except HTTPException as exc:
            if exc.status_code not in {404, 409, 422}:
                raise
            continue  # Unconfirmed plans or removed source material cannot accept a candidate.
        latest = await chapter_generation.latest_chapter_draft_version(
            db, project_id=project_id, chapter_id=job.chapter_id,
        )
        if (
            chapter_generation.chapter_generation_checkpoint_section_count(context, result) == len(context.sections)
            and result.get("candidate_base_version_id") == (latest.id if latest else None)
        ):
            candidates[job.chapter_id] = job.id
    return candidates


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
    if progress.get("provider_request_pending"):
        return EditingJobResult(status=MemoirEditingJobStatus.FAILED, result_json=progress,
            error_message="服务中断时模型请求尚未确认完成，已保留进度，请确认后重试")

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
                automatic=payload.get("automatic") is True,
            )
    except chapter_generation.ChapterDraftCandidateReady as ready:
        progress.update(ready.candidate)
        progress.update(stage="completed", outcome="needs_review")
        return EditingJobResult(result_json=progress)
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


async def accept_chapter_candidate(
    db: AsyncSession, *, user_id: str, project_id: str, chapter_id: str, job_id: str,
) -> dict[str, object]:
    await load_memoir_project(db, project_id, user_id)
    job = await _load_job(db, project_id=project_id, chapter_id=chapter_id, job_id=job_id)
    if job is None or job.status != "succeeded" or (job.result_json or {}).get("outcome") != "needs_review":
        raise HTTPException(status_code=409, detail="没有可采纳的候选稿")
    from app.services.memoir import completion_issues
    if any(issue.channel == "manual_review" for issue in await completion_issues.list_issues(
        db, project_id=project_id, chapter_id=chapter_id,
    )):
        raise HTTPException(status_code=409, detail="本章还有事实或隐私事项需要先确认")
    await chapter_generation.save_chapter_draft_candidate(
        db, project_id=project_id, chapter_id=chapter_id, checkpoint=job.result_json,
    )
    job.result_json = {**_public_result(job.result_json), "outcome": "ready", "candidate_content": None}
    from app.services.memoir.automatic_book_draft import schedule_book_draft
    await schedule_book_draft(db, project_id=project_id, source="candidate_accepted")
    await db.commit()
    return _job_state(project_id=project_id, chapter_id=chapter_id, job=job)
