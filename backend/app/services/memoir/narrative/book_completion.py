from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import (
    MemoirCompletionIssueChannel,
    MemoirSemanticCacheStatus,
)
from app.models.memoir import (
    MemoirBookChapterDraftVersion,
    MemoirBookOutput,
    MemoirBookPlanChapter,
    MemoirSection,
)
from app.schemas.memoir_narrative import (
    MemoirBookCompletionOverviewResponse,
    MemoirChapterWorkspaceResponse,
)
from app.services.memoir import (
    book_completion_jobs,
    book_review_jobs,
    book_reviews,
    book_revision_jobs,
    chapter_digests,
    chapter_generation,
    completion_issues,
    completion_read_model,
    sections,
)

ChapterPreparationProgressCallback = Callable[
    [dict[str, object]],
    Awaitable[None],
]


async def ensure_chapter_sections(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    user_id: str,
) -> None:
    chapter = await db.scalar(
        select(MemoirBookPlanChapter).where(
            MemoirBookPlanChapter.project_id == project_id,
            MemoirBookPlanChapter.id == chapter_id,
        )
    )
    if chapter is None:
        raise ValueError("章节不存在")
    exists = await db.scalar(
        select(MemoirSection.id)
        .where(
            MemoirSection.project_id == project_id,
            MemoirSection.chapter_id == chapter_id,
        )
        .limit(1)
    )
    if exists is None:
        await sections.bootstrap_sections(
            db,
            user_id=user_id,
            project_id=project_id,
        )
async def prepare_chapter(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    user_id: str,
    ai_model: str | None = None,
    checkpoint: dict[str, object] | None = None,
    progress_callback: ChapterPreparationProgressCallback | None = None,
) -> MemoirChapterWorkspaceResponse:
    await ensure_chapter_sections(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        user_id=user_id,
    )
    digest = await chapter_digests.generate_or_reuse_chapter_digest(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        ai_model=ai_model,
        force_regenerate=True,
        progress_callback=progress_callback,
    )
    if digest.status != MemoirSemanticCacheStatus.READY.value:
        # Keep provider diagnostics and the failed cache row available after
        # the API returns an error instead of losing them to the route rollback.
        await db.commit()
        raise ValueError(digest.error_message or "章节审阅失败")
    issues = await completion_issues.list_issues(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    active_issue = next(
        (
            issue
            for issue in issues
            if issue.channel
            == MemoirCompletionIssueChannel.MANUAL_REVIEW.value
        ),
        None,
    )
    if active_issue is None:
        context = await chapter_generation.load_chapter_generation_context(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
        )
        latest = await db.scalar(
            select(MemoirBookChapterDraftVersion)
            .where(
                MemoirBookChapterDraftVersion.project_id == project_id,
                MemoirBookChapterDraftVersion.chapter_id == chapter_id,
            )
            .order_by(
                MemoirBookChapterDraftVersion.version.desc(),
                MemoirBookChapterDraftVersion.created_at.desc(),
                MemoirBookChapterDraftVersion.id.desc(),
            )
            .limit(1)
        )
        if not chapter_generation.chapter_draft_is_current(context, latest):
            await chapter_generation.generate_and_save_chapter_draft(
                db,
                project_id=project_id,
                chapter_id=chapter_id,
                ai_model=ai_model,
                editorial_guidance=list(
                    digest.digest_json.get("structure_guidance", [])
                ),
                checkpoint=checkpoint,
                progress_callback=progress_callback,
            )
    if progress_callback is not None:
        await progress_callback(
            {
                "stage": "saving",
                "current_section_id": None,
                "current_section_title": "",
            }
        )
    return await completion_read_model.chapter_workspace(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )


async def rewrite_chapter(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    user_id: str,
    ai_model: str | None = None,
    checkpoint: dict[str, object] | None = None,
    progress_callback: ChapterPreparationProgressCallback | None = None,
) -> MemoirChapterWorkspaceResponse:
    await ensure_chapter_sections(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        user_id=user_id,
    )
    issues = await completion_issues.list_issues(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    if any(
        issue.channel == MemoirCompletionIssueChannel.MANUAL_REVIEW.value
        for issue in issues
    ):
        raise ValueError("本章还有一项需要您先确认")
    digest = await chapter_digests.get_current_chapter_digest(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    editorial_guidance = (
        list(digest.digest_json.get("structure_guidance", []))
        if digest is not None
        and digest.status == MemoirSemanticCacheStatus.READY.value
        else []
    )
    # The user has explicitly chosen to write from the current material. Do
    # not generate another section/chapter review here: those auxiliary LLM
    # calls can fail even though the section structure is already complete.
    # Reuse ready guidance when present, then commit the remembered model
    # preference before the long generation call so SQLite is not write-locked
    # while the provider is working.
    await db.commit()
    await chapter_generation.generate_and_save_chapter_draft(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        ai_model=ai_model,
        editorial_guidance=editorial_guidance,
        checkpoint=checkpoint,
        progress_callback=progress_callback,
    )
    if progress_callback is not None:
        await progress_callback(
            {
                "stage": "saving",
                "current_section_id": None,
                "current_section_title": "",
            }
        )
    return await completion_read_model.chapter_workspace(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )


async def revise_chapter_section_with_ai(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    section_id: str,
    expected_version_id: str,
    instruction_text: str,
    user_id: str,
    ai_model: str | None = None,
) -> MemoirChapterWorkspaceResponse:
    await ensure_chapter_sections(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        user_id=user_id,
    )
    digest = await chapter_digests.get_current_chapter_digest(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    editorial_guidance = (
        list(digest.digest_json.get("structure_guidance", []))
        if digest is not None
        and digest.status == MemoirSemanticCacheStatus.READY.value
        else []
    )
    await db.commit()
    await chapter_generation.revise_and_save_chapter_draft_section_with_ai(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        section_id=section_id,
        expected_version_id=expected_version_id,
        instruction_text=instruction_text,
        ai_model=ai_model,
        editorial_guidance=editorial_guidance,
    )
    return await completion_read_model.chapter_workspace(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )


async def get_book_completion_overview(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    allow_admin: bool = False,
) -> MemoirBookCompletionOverviewResponse:
    issue_models = await completion_issues.list_issues(
        db,
        project_id=project_id,
    )
    overview = await completion_read_model.book_overview(
        db,
        project_id=project_id,
        issues=issue_models,
    )
    completion_job = await book_completion_jobs.get_current_book_completion_job(
        db,
        user_id=user_id,
        project_id=project_id,
        allow_admin=allow_admin,
    )
    review = await book_reviews.get_current_book_review(db, project_id=project_id)
    issues = await completion_issues.collection_response(
        db,
        project_id=project_id,
        issues=issue_models,
    )
    return MemoirBookCompletionOverviewResponse.model_validate(
        {
            **overview.model_dump(),
            "completion_job": completion_job,
            "book_review": (
                book_reviews.book_review_response(review) if review else None
            ),
            "book_review_job": await book_review_jobs.book_review_job_state(
                db,
                project_id=project_id,
            ),
            "book_issues": [
                issue for issue in issues.issues if issue.scope == "book"
            ],
            "book_output_available": (
                await db.scalar(
                    select(MemoirBookOutput.id)
                    .where(MemoirBookOutput.project_id == project_id)
                    .limit(1)
                )
                is not None
            ),
            "answer_application_job": await book_revision_jobs.book_revision_job_state(
                db,
                project_id=project_id,
                source="review_answers",
            ),
            "editorial_revision_job": await book_revision_jobs.book_revision_job_state(
                db,
                project_id=project_id,
                source="editorial_actions",
            ),
        }
    )
