from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirCompletionIssueChannel,
    MemoirCompletionIssueStatus,
    MemoirEditingJobStatus,
    MemoirEditingJobType,
    MemoirSectionStatus,
    MemoirSemanticCacheStatus,
)
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirChapterDigest,
    MemoirCompletionIssue,
    MemoirEditingJob,
    MemoirSection,
    MemoirSectionDigest,
    MemoirSectionMaterial,
)
from app.models.projects import Project
from app.schemas.memoir_narrative import (
    MemoirBookCompletionOverviewResponse,
    MemoirChapterCompletionIssue,
    MemoirChapterCompletionNextAction,
    MemoirChapterCompletionSummary,
    MemoirChapterWorkspaceResponse,
)
from app.services.memoir import (
    book_source_links,
    chapter_digests,
    chapter_generation,
    completion_issues,
    section_digests,
    sections,
)
from app.services.memoir.project_revisions import project_data_revision
from app.services.memoir.project_ai_preferences import (
    load_project_transcript_polish_level,
)


@dataclass(frozen=True)
class _ChapterSummarySnapshot:
    active_job: bool
    section_exists: bool
    issues: list[MemoirCompletionIssue]
    draft: MemoirBookChapterDraft | None
    version: MemoirBookChapterDraftVersion | None
    context: chapter_generation.ChapterGenerationContext | None
    digest: MemoirChapterDigest | None


async def _plan_chapters(
    db: AsyncSession,
    *,
    project_id: str,
) -> list[MemoirBookPlanChapter]:
    plan = await db.scalar(
        select(MemoirBookPlan).where(MemoirBookPlan.project_id == project_id)
    )
    if plan is None:
        return []
    return list(
        (
            await db.scalars(
                select(MemoirBookPlanChapter)
                .where(
                    MemoirBookPlanChapter.project_id == project_id,
                    MemoirBookPlanChapter.plan_id == plan.id,
                    MemoirBookPlanChapter.include_in_book.is_(True),
                )
                .options(
                    selectinload(MemoirBookPlanChapter.plan),
                    selectinload(MemoirBookPlanChapter.sections)
                    .selectinload(MemoirSection.materials)
                    .selectinload(MemoirSectionMaterial.utterance),
                    selectinload(MemoirBookPlanChapter.sections)
                    .selectinload(MemoirSection.materials)
                    .selectinload(MemoirSectionMaterial.transcript_version),
                )
                .execution_options(populate_existing=True)
                .order_by(
                    MemoirBookPlanChapter.sort_order,
                    MemoirBookPlanChapter.id,
                )
            )
        ).all()
    )


async def _latest_version(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> tuple[MemoirBookChapterDraft | None, MemoirBookChapterDraftVersion | None]:
    draft = await db.scalar(
        select(MemoirBookChapterDraft).where(
            MemoirBookChapterDraft.project_id == project_id,
            MemoirBookChapterDraft.chapter_id == chapter_id,
        )
    )
    version = await db.scalar(
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
    return draft, version


async def chapter_summary(
    db: AsyncSession,
    *,
    project_id: str,
    chapter: MemoirBookPlanChapter,
    snapshot: _ChapterSummarySnapshot | None = None,
) -> MemoirChapterCompletionSummary:
    active_job = (
        snapshot.active_job
        if snapshot is not None
        else await db.scalar(
            select(MemoirEditingJob.id)
            .where(
                MemoirEditingJob.project_id == project_id,
                MemoirEditingJob.chapter_id == chapter.id,
                MemoirEditingJob.job_type
                == MemoirEditingJobType.PREPARE_CHAPTER.value,
                MemoirEditingJob.status.in_(
                    {
                        MemoirEditingJobStatus.PENDING.value,
                        MemoirEditingJobStatus.RUNNING.value,
                    }
                ),
            )
            .limit(1)
        )
    )
    if active_job:
        return MemoirChapterCompletionSummary(
            chapter_id=chapter.id,
            title=chapter.title,
            sort_order=chapter.sort_order,
            state="preparing",
            next_action=MemoirChapterCompletionNextAction(
                kind="wait",
                title="正在处理本章",
                detail="可以进入本章查看逐节进度。",
                action_text="查看进度",
                disabled=False,
            ),
            headline="正在检查并整理本章",
            detail="每个小节完成后都会保存进度。",
        )
    section_exists = (
        snapshot.section_exists
        if snapshot is not None
        else (
            await db.scalar(
                select(MemoirSection.id)
                .where(
                    MemoirSection.project_id == project_id,
                    MemoirSection.chapter_id == chapter.id,
                )
                .limit(1)
            )
            is not None
        )
    )
    issues = (
        snapshot.issues
        if snapshot is not None
        else await completion_issues.list_issues(
            db,
            project_id=project_id,
            chapter_id=chapter.id,
        )
    )
    issue = next(
        (
            issue
            for issue in issues
            if issue.channel
            == MemoirCompletionIssueChannel.MANUAL_REVIEW.value
        ),
        None,
    )
    optional_count = sum(
        issue.channel == MemoirCompletionIssueChannel.INTERVIEW.value
        for issue in issues
    )
    optional_note = (
        f"另有 {optional_count} 条可选补充建议。"
        if optional_count
        else ""
    )
    draft, version = (
        (snapshot.draft, snapshot.version)
        if snapshot is not None
        else await _latest_version(
            db,
            project_id=project_id,
            chapter_id=chapter.id,
        )
    )
    draft_current = False
    if section_exists:
        try:
            context = (
                snapshot.context
                if snapshot is not None
                else await chapter_generation.load_chapter_generation_context(
                    db,
                    project_id=project_id,
                    chapter_id=chapter.id,
                )
            )
            if context is not None:
                draft_current = chapter_generation.chapter_draft_is_current(
                    context,
                    version,
                )
        except HTTPException:
            draft_current = False
    digest = (
        snapshot.digest
        if snapshot is not None
        else await chapter_digests.get_current_chapter_digest(
            db,
            project_id=project_id,
            chapter_id=chapter.id,
        )
    )
    digest_ready = bool(
        digest
        and digest.status == MemoirSemanticCacheStatus.READY.value
    )
    review_ready = bool(
        digest_ready
        or (
            draft_current
            and version is not None
            and version.prompt_snapshot
            == chapter_generation.CHAPTER_GENERATION_ENGINE
        )
    )
    if not review_ready:
        if (
            digest is not None
            and digest.status == MemoirSemanticCacheStatus.FAILED.value
        ):
            return MemoirChapterCompletionSummary(
                chapter_id=chapter.id,
                title=chapter.title,
                sort_order=chapter.sort_order,
                state="failed",
                next_action=MemoirChapterCompletionNextAction(
                    kind="prepare",
                    title="上次检查没有完成",
                    detail="原始素材没有丢失，可以重新检查。",
                    action_text="重新检查",
                ),
                headline="上次检查没有完成",
                detail="原始素材没有丢失。",
            )
        return MemoirChapterCompletionSummary(
            chapter_id=chapter.id,
            title=chapter.title,
            sort_order=chapter.sort_order,
            state="not_started",
            next_action=MemoirChapterCompletionNextAction(
                kind="prepare",
                title="检查本章是否需要补充",
                detail="系统会根据当前素材判断本章最需要处理的一步。",
                action_text="检查本章",
            ),
            headline="尚未检查是否需要补充",
            detail="检查后只会给出一个明确的下一步。",
        )
    if issue is not None:
        item_status = (
            "in_progress"
            if issue.status == MemoirCompletionIssueStatus.IN_PROGRESS.value
            else "pending"
        )
        return MemoirChapterCompletionSummary(
            chapter_id=chapter.id,
            title=chapter.title,
            sort_order=chapter.sort_order,
            state="needs_input",
            next_action=MemoirChapterCompletionNextAction(
                kind="confirm",
                title="需要您确认一件事",
                detail=issue.question,
                action_text="确认",
                issue_id=issue.id,
            ),
            active_issue=MemoirChapterCompletionIssue(
                id=issue.id,
                question=issue.question,
                status=item_status,
            ),
            headline="需要确认",
            detail=issue.reason,
        )
    if (
        draft is not None
        and version is not None
        and draft.status == MemoirBookChapterDraftStatus.CONFIRMED.value
        and draft_current
    ):
        return MemoirChapterCompletionSummary(
            chapter_id=chapter.id,
            title=chapter.title,
            sort_order=chapter.sort_order,
            state="confirmed",
            next_action=MemoirChapterCompletionNextAction(
                kind="complete",
                title="本章已确认",
                detail="正文已经收录进书稿，仍可继续修改。",
                action_text="查看正文",
            ),
            headline="本章已确认",
            detail=f"正文已经收录进书稿。{optional_note}",
        )
    if draft is not None and version is not None and draft_current:
        return MemoirChapterCompletionSummary(
            chapter_id=chapter.id,
            title=chapter.title,
            sort_order=chapter.sort_order,
            state="draft_ready",
            next_action=MemoirChapterCompletionNextAction(
                kind="review_draft",
                title="正文已经整理好",
                detail="请通读、修改并确认本章。",
                action_text="通读并确认",
            ),
            headline="正文可以通读",
            detail=f"现有素材已经整理成完整章节。{optional_note}",
        )
    return MemoirChapterCompletionSummary(
        chapter_id=chapter.id,
        title=chapter.title,
        sort_order=chapter.sort_order,
        state="not_started",
        next_action=MemoirChapterCompletionNextAction(
            kind="prepare",
            title="根据检查结果整理正文",
            detail="当前检查已经完成，正文需要按现有素材更新。",
            action_text="整理正文",
        ),
        headline="正文需要更新" if section_exists else "素材尚未归入小章节",
        detail="点击后只给出一个明确的下一步。",
    )


async def _current_chapter_digests(
    db: AsyncSession,
    *,
    project_id: str,
    chapters: list[MemoirBookPlanChapter],
) -> dict[str, MemoirChapterDigest]:
    section_contexts = {
        section.id: section_digests.section_digest_context(section)
        for chapter in chapters
        for section in chapter.sections
        if (
            section.status == MemoirSectionStatus.ACTIVE.value
            and section.include_in_book
        )
    }
    if not section_contexts:
        return {}
    current_section_digests = {
        digest.section_id: digest
        for digest in (
            await db.scalars(
                select(MemoirSectionDigest).where(
                    MemoirSectionDigest.project_id == project_id,
                    MemoirSectionDigest.section_id.in_(section_contexts),
                    MemoirSectionDigest.engine
                    == section_digests.SECTION_DIGEST_ENGINE,
                    MemoirSectionDigest.status
                    == MemoirSemanticCacheStatus.READY.value,
                    MemoirSectionDigest.input_signature.in_(
                        context.input_signature
                        for context in section_contexts.values()
                    ),
                )
            )
        ).all()
        if digest.input_signature
        == section_contexts[digest.section_id].input_signature
    }
    chapter_contexts = {}
    for chapter in chapters:
        try:
            chapter_contexts[chapter.id] = chapter_digests.chapter_digest_context(
                chapter,
                section_digests_by_id=current_section_digests,
            )
        except HTTPException:
            continue
    if not chapter_contexts:
        return {}
    return {
        digest.chapter_id: digest
        for digest in (
            await db.scalars(
                select(MemoirChapterDigest).where(
                    MemoirChapterDigest.project_id == project_id,
                    MemoirChapterDigest.chapter_id.in_(chapter_contexts),
                    MemoirChapterDigest.engine
                    == chapter_digests.CHAPTER_DIGEST_ENGINE,
                    MemoirChapterDigest.input_signature.in_(
                        context.input_signature
                        for context in chapter_contexts.values()
                    ),
                )
            )
        ).all()
        if digest.input_signature
        == chapter_contexts[digest.chapter_id].input_signature
    }


async def _chapter_summary_snapshots(
    db: AsyncSession,
    *,
    project_id: str,
    chapters: list[MemoirBookPlanChapter],
    issues: list[MemoirCompletionIssue] | None = None,
) -> dict[str, _ChapterSummarySnapshot]:
    chapter_ids = [chapter.id for chapter in chapters]
    active_chapter_ids = set(
        (
            await db.scalars(
                select(MemoirEditingJob.chapter_id).where(
                    MemoirEditingJob.project_id == project_id,
                    MemoirEditingJob.chapter_id.in_(chapter_ids),
                    MemoirEditingJob.job_type
                    == MemoirEditingJobType.PREPARE_CHAPTER.value,
                    MemoirEditingJob.status.in_(
                        {
                            MemoirEditingJobStatus.PENDING.value,
                            MemoirEditingJobStatus.RUNNING.value,
                        }
                    ),
                )
            )
        ).all()
    )
    section_chapter_ids = set(
        (
            await db.scalars(
                select(MemoirSection.chapter_id).where(
                    MemoirSection.project_id == project_id,
                    MemoirSection.chapter_id.in_(chapter_ids),
                )
            )
        ).all()
    )
    issues_by_chapter: dict[str, list[MemoirCompletionIssue]] = {}
    for issue in (
        issues
        if issues is not None
        else await completion_issues.list_issues(db, project_id=project_id)
    ):
        if issue.chapter_id in chapter_ids:
            issues_by_chapter.setdefault(issue.chapter_id, []).append(issue)
    drafts = {
        draft.chapter_id: draft
        for draft in (
            await db.scalars(
                select(MemoirBookChapterDraft).where(
                    MemoirBookChapterDraft.project_id == project_id,
                    MemoirBookChapterDraft.chapter_id.in_(chapter_ids),
                )
            )
        ).all()
    }
    latest_version_numbers = (
        select(
            MemoirBookChapterDraftVersion.chapter_id.label("chapter_id"),
            func.max(MemoirBookChapterDraftVersion.version).label("version"),
        )
        .where(
            MemoirBookChapterDraftVersion.project_id == project_id,
            MemoirBookChapterDraftVersion.chapter_id.in_(chapter_ids),
        )
        .group_by(MemoirBookChapterDraftVersion.chapter_id)
        .subquery()
    )
    versions = {
        version.chapter_id: version
        for version in (
            await db.scalars(
                select(MemoirBookChapterDraftVersion)
                .join(
                    latest_version_numbers,
                    and_(
                        latest_version_numbers.c.chapter_id
                        == MemoirBookChapterDraftVersion.chapter_id,
                        latest_version_numbers.c.version
                        == MemoirBookChapterDraftVersion.version,
                    ),
                )
                .where(MemoirBookChapterDraftVersion.project_id == project_id)
            )
        ).all()
    }
    current_digests = await _current_chapter_digests(
        db,
        project_id=project_id,
        chapters=chapters,
    )
    polish_level = await load_project_transcript_polish_level(db, project_id)
    snapshots: dict[str, _ChapterSummarySnapshot] = {}
    for chapter in chapters:
        issues = issues_by_chapter.get(chapter.id, [])
        context = None
        if chapter.id not in active_chapter_ids:
            try:
                context = chapter_generation.chapter_generation_context(
                    chapter,
                    polish_level=polish_level,
                )
            except HTTPException:
                pass
        snapshots[chapter.id] = _ChapterSummarySnapshot(
            active_job=chapter.id in active_chapter_ids,
            section_exists=chapter.id in section_chapter_ids,
            issues=issues,
            draft=drafts.get(chapter.id),
            version=versions.get(chapter.id),
            context=context,
            digest=current_digests.get(chapter.id),
        )
    return snapshots


async def book_overview(
    db: AsyncSession,
    *,
    project_id: str,
    issues: list[MemoirCompletionIssue] | None = None,
) -> MemoirBookCompletionOverviewResponse:
    chapters = await _plan_chapters(db, project_id=project_id)
    if not chapters:
        return MemoirBookCompletionOverviewResponse(
            project_id=project_id,
            headline="先确定这本书准备写哪些章节",
            detail="有了章节后，可以逐章整理、通读并确认正文。",
            total_chapters=0,
            completed_chapters=0,
            chapters=[],
        )
    snapshots = await _chapter_summary_snapshots(
        db,
        project_id=project_id,
        chapters=chapters,
        issues=issues,
    )
    items = [
        await chapter_summary(
            db,
            project_id=project_id,
            chapter=chapter,
            snapshot=snapshots[chapter.id],
        )
        for chapter in chapters
    ]
    completed = sum(item.state == "confirmed" for item in items)
    needs_input = sum(item.state == "needs_input" for item in items)
    needs_check = sum(
        item.state in {"not_started", "failed"} for item in items
    )
    ready_to_review = sum(item.state == "draft_ready" for item in items)
    unfinished = len(items) - completed
    if unfinished:
        progress = []
        if needs_check:
            progress.append(f"{needs_check} 章待检查")
        if needs_input:
            progress.append(f"{needs_input} 章待确认")
        if ready_to_review:
            progress.append(f"{ready_to_review} 章待通读")
        headline = f"还有 {unfinished} 章未完成"
        detail = "，".join(progress) + "。每章只显示当前最重要的一步。"
    else:
        headline = "全书正文已经确认"
        detail = "需要时仍可按当前素材重新检查。"
    return MemoirBookCompletionOverviewResponse(
        project_id=project_id,
        headline=headline,
        detail=detail,
        total_chapters=len(items),
        completed_chapters=completed,
        chapters=items,
    )


async def chapter_workspace(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> MemoirChapterWorkspaceResponse:
    project = await db.get(Project, project_id)
    server_revision = await project_data_revision(db, project) if project else 0
    chapter = await db.scalar(
        select(MemoirBookPlanChapter).where(
            MemoirBookPlanChapter.project_id == project_id,
            MemoirBookPlanChapter.id == chapter_id,
        )
    )
    if chapter is None:
        raise HTTPException(status_code=404, detail="章节不存在")
    summary = await chapter_summary(
        db,
        project_id=project_id,
        chapter=chapter,
    )
    draft, version = await _latest_version(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    source_changes = await book_source_links.chapter_source_change_state(
        db,
        chapter=chapter,
    )
    signature_changed = False
    if version is not None:
        try:
            context = await chapter_generation.load_chapter_generation_context(
                db,
                project_id=project_id,
                chapter_id=chapter_id,
            )
            signature_changed = not chapter_generation.chapter_draft_is_current(
                context,
                version,
            )
        except HTTPException:
            signature_changed = False
    draft_needs_rewrite = bool(
        version is not None
        and (
            signature_changed
            or not version.section_snapshot_json
            or source_changes.pending_source_count
            or source_changes.removed_source_count
        )
    )
    active_issues = await completion_issues.list_issues(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    has_manual_review = any(
        issue.channel == MemoirCompletionIssueChannel.MANUAL_REVIEW.value
        for issue in active_issues
    )
    sections_by_chapter = await sections.section_summaries_by_chapter(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    chapter_sections = sections_by_chapter.get(chapter_id, [])
    pending_assignment_count = sum(
        section.material_count
        for section in chapter_sections
        if section.requires_user_assignment
    )
    return MemoirChapterWorkspaceResponse(
        server_revision=server_revision or 0,
        project_id=project_id,
        chapter_id=chapter_id,
        chapter_title=chapter.title,
        completion_summary=summary,
        draft_needs_rewrite=draft_needs_rewrite,
        can_rewrite_draft=(
            draft_needs_rewrite
            and not has_manual_review
        ),
        pending_assignment_material_count=pending_assignment_count,
        pending_completion_source_count=(
            source_changes.pending_completion_source_count
        ),
        pending_new_source_count=source_changes.pending_new_source_count,
        sections=chapter_sections,
        draft_id=draft.id if draft else None,
        draft_version_id=version.id if version else None,
        draft_version=version.version if version else None,
        draft_status=draft.status if draft else None,
        draft_content=version.content if version else "",
        draft_sections=(
            list(version.section_snapshot_json) if version else []
        ),
    )
