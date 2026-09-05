"""Memoir-specific progression using the existing editing queue and chapter jobs."""

from datetime import timedelta
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow_naive
from app.config import get_settings
from app.domain.memoir.types import MemoirEditingJobStatus, MemoirEditingJobType
from app.models.memoir import MemoirBookPlanChapter, MemoirEditingJob
from app.models.projects import Project
from app.schemas.memoir_narrative import MemoirChapterPreparationJobRequest
from app.schemas.memoir_book_review import MemoirBookReviewGenerateRequest
from app.services.memoir import book_review_jobs, chapter_generation, chapter_preparation_jobs, editing_jobs
from app.services.memoir.editing_jobs import EditingJobResult
from app.services.memoir.narrative.book_output_assembly import assemble_book_output
from app.services.memoir.semantic_state import semantic_json_signature


class ChapterProgress(BaseModel):
    job_id: str | None = None
    input_signature: str = ""
    draft_version_id: str | None = None
    status: Literal["pending", "running", "ready", "failed", "cancelled", "needs_input", "needs_review", "needs_material"] = "pending"
    error_message: str = ""


class DraftProgress(BaseModel):
    stage: str = "queued"
    chapters: dict[str, ChapterProgress] = Field(default_factory=dict)
    output_id: str | None = None
    output_version_id: str | None = None
    omitted_chapter_ids: list[str] = Field(default_factory=list)
    preserved_manual: bool = False
    review_job_id: str | None = None
    review_status: str = "not_started"
    review_error: str = ""


async def schedule_book_draft(db: AsyncSession, *, project_id: str, source: str = "materials") -> MemoirEditingJob:
    return await editing_jobs.enqueue_editing_job(
        db,
        project_id=project_id,
        job_type=MemoirEditingJobType.UPDATE_BOOK_DRAFT,
        dedupe_key=f"update-book-draft:{project_id}",
        payload_json={"source": source},
        run_after=utcnow_naive() + timedelta(seconds=get_settings().memoir_story_settle_seconds),
        replace_completed=True,
        preserve_progress=True,
        reschedule_running=True,
    )


async def run_book_draft_job(db: AsyncSession, job: MemoirEditingJob) -> EditingJobResult:
    project_id = job.project_id
    progress = DraftProgress.model_validate(job.result_json or {})
    dependency = await db.scalar(
        select(MemoirEditingJob.id)
        .where(
            MemoirEditingJob.project_id == project_id,
            MemoirEditingJob.job_type.in_(
                [
                    MemoirEditingJobType.CORRECT_TRANSCRIPT.value,
                    MemoirEditingJobType.ORGANIZE_MATERIAL.value,
                    MemoirEditingJobType.REGENERATE_CHAPTER_SECTIONS.value,
                ]
            ),
            MemoirEditingJob.status.in_(editing_jobs.ACTIVE_EDITING_JOB_STATUSES),
        )
        .limit(1)
    )
    if dependency:
        progress.stage = "organizing"
        return _waiting(job, progress)
    project = await db.get(Project, project_id)
    chapters = (
        await db.scalars(
            select(MemoirBookPlanChapter)
            .where(
                MemoirBookPlanChapter.project_id == project_id,
                MemoirBookPlanChapter.include_in_book.is_(True),
            )
            .order_by(MemoirBookPlanChapter.sort_order, MemoirBookPlanChapter.id)
        )
    ).all()
    progress.chapters = {chapter.id: progress.chapters.get(chapter.id, ChapterProgress()) for chapter in chapters}
    retry = (job.payload_json or {}).get("source") == "retry"
    for chapter in chapters:
        chapter_id = chapter.id
        state = progress.chapters[chapter_id]
        try:
            context = await chapter_generation.load_chapter_generation_context(
                db, project_id=project_id, chapter_id=chapter_id
            )
        except HTTPException as exc:
            if exc.status_code != 422:
                raise
            state.status, state.error_message = "needs_material", str(exc.detail)
            continue
        state.input_signature = context.input_signature
        latest = await chapter_generation.latest_chapter_draft_version(db, project_id=project_id, chapter_id=chapter_id)
        if chapter_generation.chapter_draft_is_current(context, latest):
            state.status, state.error_message = "ready", ""
            state.draft_version_id = latest.id
            continue

        async def record_child(child: MemoirEditingJob) -> None:
            state.job_id = child.id
            job.result_json = progress.model_dump(mode="json")
            db.add(job)

        child = await chapter_preparation_jobs.enqueue_chapter_preparation_job(
            db,
            user_id=project.user_id,
            project_id=project_id,
            chapter_id=chapter_id,
            req=MemoirChapterPreparationJobRequest(operation="prepare"),
            automatic=True,
            retry=retry,
            on_enqueued=record_child,
        )
        state.job_id = str(child["job_id"])
        state.status = (
            ("needs_review" if child.get("outcome") == "needs_review" else "needs_input")
            if child["status"] == "succeeded" else child["status"]
        )
        state.error_message = str(child.get("error_message") or "")
    # An explicit retry authorizes one dispatch, not every dependency poll.
    job.payload_json = {"source": "progress"}
    readable = {chapter_id for chapter_id, item in progress.chapters.items() if item.status == "ready"}
    if readable or progress.output_id:
        assembled = await assemble_book_output(db, project_id=project_id, eligible_chapter_ids=readable, automatic=True)
        progress.output_id = assembled.output_id
        progress.output_version_id = assembled.output_version_id
        progress.omitted_chapter_ids = assembled.omitted_chapter_ids
        progress.preserved_manual = assembled.preserved_manual
        # Persist deterministic assembly before any review dispatch or external request.
        job.result_json = progress.model_dump(mode="json")
        await db.commit()
    if any(item.status in editing_jobs.ACTIVE_EDITING_JOB_STATUSES for item in progress.chapters.values()):
        progress.stage = "writing_chapters"
        return _waiting(job, progress)
    progress.stage = "partial_ready" if readable else "needs_attention"
    if readable and len(readable) == len(progress.chapters):
        review_signature = semantic_json_signature({"output_version_id": progress.output_version_id,
            "chapters": {key: item.draft_version_id for key, item in progress.chapters.items()}})

        async def record_review(child: MemoirEditingJob) -> None:
            progress.review_job_id = child.id
            job.result_json = progress.model_dump(mode="json")
            db.add(job)

        review = await book_review_jobs.enqueue_book_review_job(db, user_id=project.user_id, project_id=project_id,
            req=MemoirBookReviewGenerateRequest(), input_signature=review_signature, automatic=not retry,
            on_enqueued=record_review)
        progress.review_status = str(review["status"])
        progress.review_error = str(review.get("error_message") or "")
        if progress.review_status in editing_jobs.ACTIVE_EDITING_JOB_STATUSES:
            progress.stage = "reviewing_book"
            return _waiting(job, progress)
        progress.stage = "ready" if progress.review_status == "succeeded" else "ready_review_failed"
    if progress.preserved_manual or progress.omitted_chapter_ids:
        progress.stage = "needs_attention"
    return EditingJobResult(result_json=progress.model_dump(mode="json"))


def _waiting(job: MemoirEditingJob, progress: DraftProgress) -> EditingJobResult:
    job.run_after = utcnow_naive() + timedelta(seconds=get_settings().memoir_editing_poll_seconds)
    return EditingJobResult(status=MemoirEditingJobStatus.PENDING, result_json=progress.model_dump(mode="json"))
