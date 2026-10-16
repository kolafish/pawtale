"""Memoir-specific progression using the existing editing queue and chapter jobs."""

from datetime import timedelta
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow_naive
from app.config import get_settings
from app.domain.memoir.types import (
    MemoirBookPlanStatus,
    MemoirCompletionIssueChannel,
    MemoirCompletionIssueStatus,
    MemoirEditingJobStatus,
    MemoirEditingJobType,
)
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirBookOutput,
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirCompletionIssue,
    MemoirEditingJob,
)
from app.models.projects import Project
from app.schemas.memoir_narrative import MemoirChapterPreparationJobRequest
from app.schemas.memoir_book import MemoirBookOutputEditorialCopyRegenerateRequest
from app.schemas.memoir_book_review import MemoirBookReviewGenerateRequest
from app.services.memoir import book_reviews, book_review_jobs, chapter_generation, chapter_preparation_jobs, completion_issues, editing_jobs
from app.services.memoir.book_output_snapshots import (
    legacy_chapter_version_needs_upgrade,
    load_book_plan_drafts,
    protected_legacy_chapter_version,
    readable_chapter_versions,
)
from app.services.memoir.book_output_versions import load_latest_book_output_version
from app.services.memoir.book_plan_sources import included_book_chapters
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
    retry_stages: list[Literal["chapters", "review"]] = Field(default_factory=list)

    @field_validator("retry_stages", mode="before")
    @classmethod
    def discard_legacy_copy_stage(cls, value: list[str]) -> list[str]:
        # The worker transfers old copy retry intent to the copy job payload.
        return [stage for stage in value if stage != "editorial_copy"]

    def clear_review(self) -> None:
        self.review_job_id = None
        self.review_status = "not_started"
        self.review_error = ""


async def schedule_book_draft(
    db: AsyncSession, *, project_id: str, source: str = "materials", settled: bool = False,
) -> MemoirEditingJob:
    existing = await db.scalar(select(MemoirEditingJob).where(
        MemoirEditingJob.dedupe_key == f"update-book-draft:{project_id}",
    ).with_for_update().execution_options(populate_existing=True))
    # Upstream completions and new material may coalesce with a user retry.
    # Keep that intent until the worker has dispatched it, even while waiting.
    if (existing is not None and existing.status in editing_jobs.ACTIVE_EDITING_JOB_STATUSES
            and (existing.payload_json or {}).get("source") == "retry"):
        source = "retry"
    # Incoming source changes retain their debounce. A completed upstream step or
    # explicit user decision can advance immediately; the worker still checks dependencies.
    return await editing_jobs.enqueue_editing_job(
        db,
        project_id=project_id,
        job_type=MemoirEditingJobType.UPDATE_BOOK_DRAFT,
        dedupe_key=f"update-book-draft:{project_id}",
        payload_json={"source": source, "retry_editorial_copy": source == "retry" or bool(
            existing and existing.status in editing_jobs.ACTIVE_EDITING_JOB_STATUSES
            and (existing.payload_json or {}).get("retry_editorial_copy")
        )},
        run_after=utcnow_naive() + timedelta(seconds=0 if settled else get_settings().memoir_story_settle_seconds),
        replace_completed=True,
        preserve_progress=True,
        reschedule_running=True,
    )


async def reconcile_books_omitted_by_editorial_review(db: AsyncSession) -> int:
    """Requeue completed drafts that predate the nonblocking editorial policy."""
    project_ids = (await db.scalars(
        select(MemoirCompletionIssue.project_id)
        .where(
            MemoirCompletionIssue.channel == MemoirCompletionIssueChannel.MANUAL_REVIEW.value,
            MemoirCompletionIssue.status != MemoirCompletionIssueStatus.CLOSED.value,
            MemoirCompletionIssue.issue_type.not_in(completion_issues.BLOCKING_BOOK_ISSUE_TYPES),
        )
        .distinct()
    )).all()
    draft_projects = (await db.scalars(select(MemoirBookPlan.project_id).where(
        MemoirBookPlan.status == MemoirBookPlanStatus.DRAFT.value,
    ))).all()
    scheduled = 0
    for project_id in set(project_ids) | set(draft_projects):
        job = await db.scalar(select(MemoirEditingJob).where(
            MemoirEditingJob.project_id == project_id,
            MemoirEditingJob.job_type == MemoirEditingJobType.UPDATE_BOOK_DRAFT.value,
        ).order_by(MemoirEditingJob.updated_at.desc()).limit(1))
        if job is None or job.status not in {MemoirEditingJobStatus.SUCCEEDED.value, MemoirEditingJobStatus.FAILED.value}:
            continue
        if project_id in draft_projects:
            await schedule_book_draft(db, project_id=project_id, source="plan_activation")
            scheduled += 1
            continue
        if job.status != MemoirEditingJobStatus.SUCCEEDED.value:
            continue
        plan = await load_book_plan_drafts(db, project_id=project_id)
        if plan.status != MemoirBookPlanStatus.CONFIRMED.value:
            continue
        included = included_book_chapters(plan)
        blocked = await completion_issues.open_blocking_book_chapter_ids(
            db, project_id=project_id, all_chapter_ids=(chapter.id for chapter in included),
        )
        previous = DraftProgress.model_validate(job.result_json or {})
        unblocked = any(
            previous.chapters.get(chapter.id, ChapterProgress()).status == "needs_input"
            and chapter.id not in blocked
            for chapter in included
        )
        if unblocked:
            await schedule_book_draft(db, project_id=project_id, source="assembly_policy")
            scheduled += 1
            continue
        output = await db.scalar(select(MemoirBookOutput).where(MemoirBookOutput.project_id == project_id))
        if output is None:
            continue
        latest = await load_latest_book_output_version(db, output_id=output.id)
        if latest is None:
            continue
        safe_readable = {
            chapter.id for chapter, _ in readable_chapter_versions(plan, allow_partial=True)
            if chapter.id not in blocked
        }
        stored = {
            item.get("chapter_id") for item in (latest.chapter_snapshot_json or [])
            if isinstance(item, dict)
        }
        if not (safe_readable - stored or stored & blocked):
            continue
        await schedule_book_draft(db, project_id=project_id, source="assembly_policy")
        scheduled += 1
    await db.commit()
    return scheduled


async def run_book_draft_job(db: AsyncSession, job: MemoirEditingJob) -> EditingJobResult:
    project_id = job.project_id
    legacy = job.result_json or {}
    progress = DraftProgress.model_validate(legacy)
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
    if "editorial_copy" in legacy.get("retry_stages", []):
        job.payload_json = {**(job.payload_json or {}), "retry_editorial_copy": True}
    if dependency:
        progress.stage = "organizing"
        return _waiting(job, progress)
    from app.services.memoir.book_plan_write_workflow import activate_existing_plan_for_automatic_book
    # Do not flush the legacy retry payload (and lock the job row) before the
    # directory lock used by copy completion and other upstream writers.
    with db.no_autoflush:
        await activate_existing_plan_for_automatic_book(db, project_id=project_id)
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
    source = (job.payload_json or {}).get("source")
    if source == "retry":
        progress.retry_stages = ["chapters", "review"]
    retry_chapters = "chapters" in progress.retry_stages
    previous_draft_ids = {chapter_id: item.draft_version_id for chapter_id, item in progress.chapters.items()}
    readable = set()
    legacy_upgrade_pending = False
    for chapter in chapters:
        chapter_id = chapter.id
        state = progress.chapters[chapter_id]
        state.draft_version_id = None
        latest = await chapter_generation.latest_chapter_draft_version(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
        )
        legacy_upgrade = legacy_chapter_version_needs_upgrade(latest)
        if legacy_upgrade:
            legacy_upgrade_pending = True
            draft = await db.scalar(
                select(MemoirBookChapterDraft).where(
                    MemoirBookChapterDraft.project_id == project_id,
                    MemoirBookChapterDraft.chapter_id == chapter_id,
                )
            )
            if protected_legacy_chapter_version(draft, latest):
                # A confirmed or manually edited flat draft remains readable
                # while its structured rewrite waits for review.
                readable.add(chapter_id)
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
        if chapter_generation.chapter_draft_sources_still_included(context, latest):
            readable.add(chapter_id)
            state.draft_version_id = latest.id
        if (
            chapter_generation.chapter_draft_is_current(context, latest)
            and chapter_generation.chapter_draft_render_policy_current(context, latest)
        ):
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
            retry=retry_chapters,
            on_enqueued=record_child,
        )
        # Child dispatch may commit its checkpoint and release the directory.
        # Reacquire it before any later parent payload/output write.
        with db.no_autoflush:
            await completion_issues.lock_project_chapters(db, project_id=project_id)
        state.job_id = str(child["job_id"])
        state.status = (
            ("needs_review" if child.get("outcome") == "needs_review" else "needs_input")
            if child["status"] == "succeeded" else child["status"]
        )
        if child.get("outcome") == "stale":
            # The next poll retries against the latest version and material.
            state.status = "pending"
        state.error_message = str(child.get("error_message") or "")
    # Consume once per stage; asynchronous chapter work must not consume the
    # later review retry, and dependency polls must not redispatch it.
    progress.retry_stages = [stage for stage in progress.retry_stages if stage != "chapters"]
    job.payload_json = {"source": "progress", "retry_editorial_copy": (job.payload_json or {}).get("retry_editorial_copy") is True}
    current = {chapter_id for chapter_id, item in progress.chapters.items() if item.status == "ready"}
    if readable or progress.output_id:
        assembled = await assemble_book_output(db, project_id=project_id, eligible_chapter_ids=readable, automatic=True)
        progress.output_id = assembled.output_id
        progress.output_version_id = assembled.output_version_id
        progress.omitted_chapter_ids = assembled.omitted_chapter_ids
        progress.preserved_manual = assembled.preserved_manual
        # Persist deterministic assembly before any review dispatch or external request.
        job.result_json = progress.model_dump(mode="json")
        await db.commit()
    review_signature = semantic_json_signature({
        "book_metadata": await book_reviews.load_book_review_metadata(db, project_id=project_id),
        "engine": book_reviews.BOOK_REVIEW_ENGINE,
        "chapters": [(chapter.id, chapter.title, chapter.sort_order,
            progress.chapters[chapter.id].draft_version_id, progress.chapters[chapter.id].input_signature)
            for chapter in chapters]})
    previous_review = await db.get(MemoirEditingJob, progress.review_job_id) if progress.review_job_id else None
    if (not current or len(current) != len(progress.chapters) or previous_review is None
            or (previous_review.payload_json or {}).get("input_signature") != review_signature):
        # Metadata changes invalidate the same review as chapter changes.
        progress.clear_review()
    if any(item.status in editing_jobs.ACTIVE_EDITING_JOB_STATUSES for item in progress.chapters.values()):
        progress.stage = "writing_chapters"
        return _waiting(job, progress)
    if readable and progress.output_id and not progress.preserved_manual:
        from app.services.memoir.book_generation_jobs import schedule_book_editorial_copy_generation
        await schedule_book_editorial_copy_generation(
            db, user_id=project.user_id, project_id=project_id,
            req=MemoirBookOutputEditorialCopyRegenerateRequest(), automatic=True,
            retry=(job.payload_json or {}).get("retry_editorial_copy") is True,
        )
        job.payload_json = {"source": "progress"}
    progress.stage = "partial_ready" if readable else "needs_attention"
    if current and len(current) == len(progress.chapters):
        policy_only = source == "assembly_policy" and all(
            previous_draft_ids.get(chapter_id) == item.draft_version_id
            for chapter_id, item in progress.chapters.items()
        )
        if policy_only:
            # Reassembling unchanged chapters must not supersede unanswered decisions.
            progress.stage = "ready" if progress.review_status == "succeeded" else "ready_review_failed"
        else:
            async def record_review(child: MemoirEditingJob) -> None:
                progress.review_job_id = child.id
                job.result_json = progress.model_dump(mode="json")
                db.add(job)

            retry_review = "review" in progress.retry_stages
            progress.retry_stages = [stage for stage in progress.retry_stages if stage != "review"]
            review = await book_review_jobs.enqueue_book_review_job(db, user_id=project.user_id, project_id=project_id,
                req=MemoirBookReviewGenerateRequest(), input_signature=review_signature, automatic=not retry_review,
                apply_structure_guidance=True,
                on_enqueued=record_review)
            progress.review_status = str(review["status"])
            progress.review_error = str(review.get("error_message") or "")
            if progress.review_status in editing_jobs.ACTIVE_EDITING_JOB_STATUSES:
                progress.stage = "reviewing_book"
                return _waiting(job, progress)
            progress.stage = "ready" if progress.review_status == "succeeded" else "ready_review_failed"
    if legacy_upgrade_pending or progress.preserved_manual or progress.omitted_chapter_ids:
        progress.stage = "needs_attention"
    progress.retry_stages = []
    return EditingJobResult(result_json=progress.model_dump(mode="json"))


def _waiting(job: MemoirEditingJob, progress: DraftProgress) -> EditingJobResult:
    job.run_after = utcnow_naive() + timedelta(seconds=get_settings().memoir_editing_poll_seconds)
    return EditingJobResult(status=MemoirEditingJobStatus.PENDING, result_json=progress.model_dump(mode="json"))
