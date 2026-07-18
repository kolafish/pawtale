from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import (
    MemoirCompletionIssueChannel,
    MemoirCompletionIssueOutcome,
    MemoirCompletionIssueStatus,
    MemoirSemanticCacheStatus,
)
from app.models.memoir import (
    MemoirBookChapterDraftVersion,
    MemoirBookPlanChapter,
    MemoirSection,
)
from app.schemas.memoir_narrative import (
    MemoirBookCompletionOverviewResponse,
    MemoirChapterWorkspaceResponse,
)
from app.services.memoir import (
    chapter_digests,
    chapter_generation,
    completion_issues,
    completion_read_model,
    sections,
)


async def _ensure_sections(
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
) -> MemoirChapterWorkspaceResponse:
    await _ensure_sections(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        user_id=user_id,
    )
    digest = await chapter_digests.generate_or_reuse_chapter_digest(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        force_regenerate=True,
    )
    if digest.status != MemoirSemanticCacheStatus.READY.value:
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
            if issue.status != MemoirCompletionIssueStatus.CLOSED.value
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
        if latest is None or latest.input_signature != context.input_signature:
            await chapter_generation.generate_and_save_chapter_draft(
                db,
                project_id=project_id,
                chapter_id=chapter_id,
                editorial_guidance=list(
                    digest.digest_json.get("structure_guidance", [])
                ),
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
) -> MemoirChapterWorkspaceResponse:
    await _ensure_sections(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        user_id=user_id,
    )
    digest = await chapter_digests.generate_or_reuse_chapter_digest(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        force_regenerate=False,
    )
    if digest.status != MemoirSemanticCacheStatus.READY.value:
        raise ValueError(digest.error_message or "章节审阅失败")
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
    for issue in issues:
        if issue.status == MemoirCompletionIssueStatus.CLOSED.value:
            continue
        await completion_issues.close_issue(
            db,
            project_id=project_id,
            issue_id=issue.id,
            outcome=MemoirCompletionIssueOutcome.DEFERRED,
            note="用户选择先按现有素材重新生成正文",
            answered_material_id=None,
        )
    await chapter_generation.generate_and_save_chapter_draft(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        editorial_guidance=list(
            digest.digest_json.get("structure_guidance", [])
        ),
    )
    return await completion_read_model.chapter_workspace(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )


async def get_book_completion_overview(
    db: AsyncSession,
    *,
    project_id: str,
) -> MemoirBookCompletionOverviewResponse:
    return await completion_read_model.book_overview(
        db,
        project_id=project_id,
    )
