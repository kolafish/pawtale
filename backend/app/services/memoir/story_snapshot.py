"""Story navigation projection: counts and states, without manuscript/history payloads."""

from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import MemoirCompletionIssueChannel, MemoirCompletionIssueStatus
from app.models.memoir import (
    MemoirCompletionIssue,
    MemoirBookChapterDraftVersion,
    MemoirBookImagePlacement,
    MemoirBookOutputVersion,
    MemoirBookPlanChapter,
    MemoirImageAsset,
    MemoirSection,
    MemoirSectionMaterial,
)
from app.models.users import User
from app.schemas.memoir_snapshots import (
    MemoirStoryChapterResponse,
    MemoirStoryIssueCounts,
    MemoirStorySectionResponse,
    MemoirStorySnapshotResponse,
)
from app.services.memoir import (
    automation_events,
    book_reviews,
    chapter_preparation_jobs,
    draft_progress,
    projects,
    share_video_jobs,
    voice_transcripts,
)
from app.services.memoir.access import load_memoir_project
from app.services.memoir.automatic_book_draft import DraftProgress
from app.services.memoir.project_revisions import project_data_revision
from app.services.memoir.read_snapshots import _empty_automation_events
from app.services.memoir.story_growth_projection import story_growth_events


async def chapter_rows(db: AsyncSession, project_id: str):
    """Chapter titles with the length of each chapter's latest draft."""
    char_count = (
        select(func.length(func.trim(MemoirBookChapterDraftVersion.content)))
        .where(MemoirBookChapterDraftVersion.chapter_id == MemoirBookPlanChapter.id)
        .order_by(MemoirBookChapterDraftVersion.version.desc())
        .limit(1)
        .scalar_subquery()
    )
    chapters = (
        await db.execute(
            select(
                MemoirBookPlanChapter.id,
                MemoirBookPlanChapter.title,
                MemoirBookPlanChapter.include_in_book,
                char_count.label("char_count"),
            )
            .where(MemoirBookPlanChapter.project_id == project_id)
            .order_by(MemoirBookPlanChapter.sort_order, MemoirBookPlanChapter.id)
        )
    ).all()
    return chapters


async def attach_sections(
    db: AsyncSession, project_id: str, chapter_views: dict[str, MemoirStoryChapterResponse]
) -> tuple[dict[str, MemoirStorySectionResponse], dict[str, str]]:
    """Add active sections and their material counts to the chapter views."""
    section_views = {}
    section_chapters = {}
    for section in (
        await db.execute(
            select(MemoirSection.id, MemoirSection.chapter_id, MemoirSection.title)
            .where(MemoirSection.project_id == project_id, MemoirSection.status == "active")
            .order_by(MemoirSection.sort_order, MemoirSection.id)
        )
    ).all():
        view = MemoirStorySectionResponse(id=section.id, title=section.title)
        section_views[section.id] = view
        section_chapters[section.id] = section.chapter_id
        chapter_views[section.chapter_id].sections.append(view)
    for section_id, count in (
        await db.execute(
            select(MemoirSectionMaterial.section_id, func.count())
            .join(MemoirSection, MemoirSection.id == MemoirSectionMaterial.section_id)
            .where(MemoirSectionMaterial.project_id == project_id, MemoirSection.status == "active")
            .group_by(MemoirSectionMaterial.section_id)
        )
    ).all():
        section_views[section_id].material_count = count
        chapter_views[section_chapters[section_id]].material_count += count
    return section_views, section_chapters


async def story_snapshot(db: AsyncSession, user: User) -> MemoirStorySnapshotResponse:
    identity = await projects.memoir_identity_response(db, user)
    result = MemoirStorySnapshotResponse(
        server_revision=0, identity=identity, automation_events=_empty_automation_events()
    )
    if not identity.active_project_id:
        return result
    project = await load_memoir_project(db, identity.active_project_id, user.id)
    result.server_revision = await project_data_revision(db, project)
    result.updated_at = project.updated_at.isoformat()
    transcripts = await voice_transcripts.list_voice_transcripts(db, user.id, project_id=project.id, limit=1)
    result.latest_transcript = transcripts.items[0] if transcripts.items else None
    jobs = await draft_progress.project_jobs(db, project.id)
    result.progress = await draft_progress.read_draft_progress(db, project_id=project.id, jobs=jobs)
    parent = next((job for job in jobs if job.job_type == "update_book_draft"), None)
    progress = DraftProgress.model_validate(parent.result_json or {}) if parent else DraftProgress()
    chapters = await chapter_rows(db, project.id)
    # Match the canonical active issue queue. Book-level decisions are counted
    # once, never copied to every chapter they may temporarily keep out of a book.
    issue_counts = defaultdict(MemoirStoryIssueCounts)
    for chapter_id, channel, count in (
        await db.execute(
            select(MemoirCompletionIssue.chapter_id, MemoirCompletionIssue.channel, func.count())
            .where(
                MemoirCompletionIssue.project_id == project.id,
                MemoirCompletionIssue.status != MemoirCompletionIssueStatus.CLOSED.value,
            )
            .group_by(MemoirCompletionIssue.chapter_id, MemoirCompletionIssue.channel)
        )
    ).all():
        if channel == MemoirCompletionIssueChannel.MANUAL_REVIEW.value:
            issue_counts[chapter_id].review_count = count
        else:
            issue_counts[chapter_id].supplement_count = count
    # The book workspace only offers optional questions from the current ready
    # review. Manual decisions stay active across regenerated reviews.
    if issue_counts[None].supplement_count:
        review = await book_reviews.get_current_book_review(db, project_id=project.id) if any(
            chapter.include_in_book for chapter in chapters
        ) else None
        issue_counts[None].supplement_count = (
            (
                await db.scalar(
                    select(func.count())
                    .select_from(MemoirCompletionIssue)
                    .where(
                        MemoirCompletionIssue.project_id == project.id,
                        MemoirCompletionIssue.scope == "book",
                        MemoirCompletionIssue.channel == MemoirCompletionIssueChannel.INTERVIEW.value,
                        MemoirCompletionIssue.status != MemoirCompletionIssueStatus.CLOSED.value,
                        MemoirCompletionIssue.input_signature == review.input_signature,
                    )
                )
            )
            if review and review.status == "ready"
            else 0
        )
    result.book_issues = issue_counts[None]
    result.book_manual_edit_preserved = progress.preserved_manual
    candidates = await chapter_preparation_jobs.current_chapter_candidates(
        db, project_id=project.id,
        chapter_ids={chapter.id for chapter in chapters if chapter.include_in_book},
    )
    chapter_views = {}
    for chapter in chapters:
        view = MemoirStoryChapterResponse(**chapter._asdict() | {"char_count": chapter.char_count or 0})
        view.readable = view.char_count > 0
        view.candidate_job_id = candidates.get(chapter.id)
        view.issues = issue_counts[chapter.id]
        state = progress.chapters.get(chapter.id)
        if not chapter.include_in_book:
            view.state, view.state_text = "muted", "暂不入书"
        elif state and state.status in {"failed", "cancelled"}:
            view.state, view.state_text = "attention", "整理未完成"
        elif view.issues.review_count:
            view.state, view.state_text = "attention", f"{view.issues.review_count} 项待确认"
        elif view.candidate_job_id:
            view.state, view.state_text = "attention", "新稿待采纳"
        elif view.issues.supplement_count:
            view.state, view.state_text = "attention", f"{view.issues.supplement_count} 项待补充"
        elif state and state.status in {"pending", "running"}:
            view.state, view.state_text = "draft", "整理中"
        elif state and state.status == "needs_material":
            view.state, view.state_text = "attention", "待补充讲述"
        elif view.readable:
            view.state, view.state_text = "settled", "可阅读"
        else:
            view.state, view.state_text = "draft", "待整理"
        chapter_views[chapter.id] = view
    section_views, section_chapters = await attach_sections(db, project.id, chapter_views)
    result.automation_events = await automation_events.list_automation_events(
        db, user_id=user.id, project_id=project.id, limit=8, completed_only=True
    )
    assets = (
        await db.execute(
            select(MemoirImageAsset.id, MemoirImageAsset.source_utterance_id, MemoirImageAsset.source_chapter_id)
            .where(MemoirImageAsset.project_id == project.id, MemoirImageAsset.status == "ready")
        )
    ).all()
    videos = await share_video_jobs.list_completed_share_video_counts(db, user_id=user.id, project_id=project.id)
    linked_utterance_ids = (
        {asset.source_utterance_id for asset in assets if asset.source_utterance_id}
        | {video.utterance_id for video in videos}
        | {
            utterance_id
            for event in result.automation_events.items
            for utterance_id in event.utterance_ids
        }
    )
    section_by_utterance = {}
    if linked_utterance_ids:
        for utterance_id, section_id in (
            await db.execute(
                select(MemoirSectionMaterial.utterance_id, MemoirSectionMaterial.section_id)
                .join(MemoirSection, MemoirSection.id == MemoirSectionMaterial.section_id)
                .where(
                    MemoirSectionMaterial.project_id == project.id,
                    MemoirSection.status == "active",
                    MemoirSectionMaterial.utterance_id.in_(linked_utterance_ids),
                )
            )
        ).all():
            section_by_utterance[utterance_id] = section_id
    placements = defaultdict(set)
    for placement in (
        await db.execute(
            select(MemoirBookImagePlacement.image_asset_id, MemoirBookImagePlacement.chapter_id).where(
                MemoirBookImagePlacement.project_id == project.id, MemoirBookImagePlacement.enabled.is_(True)
            )
        )
    ).all():
        if placement.chapter_id in chapter_views:
            placements[placement.image_asset_id].add(placement.chapter_id)
    for asset in assets:
        section_id = section_by_utterance.get(asset.source_utterance_id)
        if section_id:
            section_views[section_id].image_count += 1
        elif asset.source_chapter_id in chapter_views:
            chapter_views[asset.source_chapter_id].image_count += 1
        elif placements[asset.id]:
            for chapter_id in placements[asset.id]:
                chapter_views[chapter_id].image_count += 1
        else:
            result.unassigned_image_count += 1
    for video in videos:
        section_id = section_by_utterance.get(video.utterance_id)
        if section_id:
            section_views[section_id].video_count += video.count
        else:
            result.unassigned_video_count += video.count
    result.chapters = list(chapter_views.values())
    book = (
        await db.execute(
            select(
                func.length(func.trim(MemoirBookOutputVersion.content)),
                func.json_array_length(MemoirBookOutputVersion.chapter_snapshot_json),
                MemoirBookOutputVersion.created_at,
            )
            .where(MemoirBookOutputVersion.project_id == project.id)
            .order_by(MemoirBookOutputVersion.version.desc())
            .limit(1)
        )
    ).first()
    if book:
        result.book_char_count = book[0] if book[1] else 0
        result.updated_at = max(project.updated_at, book[2]).isoformat()
    result.book_readable = result.book_char_count > 0
    result.book_chapter_count = book[1] if book and result.book_readable else 0
    result.growth_events = story_growth_events(
        result.automation_events.items,
        chapter_ids=set(chapter_views),
        section_chapters=section_chapters,
        section_by_utterance=section_by_utterance,
    )
    return result
