"""Story navigation projection: counts and states, without manuscript/history payloads."""

from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memoir import (
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
    MemoirStorySectionResponse,
    MemoirStorySnapshotResponse,
)
from app.services.memoir import automation_events, draft_progress, projects, share_video_jobs, voice_transcripts
from app.services.memoir.access import load_memoir_project
from app.services.memoir.automatic_book_draft import DraftProgress
from app.services.memoir.read_snapshots import _empty_automation_events
from app.services.memoir.story_growth_projection import story_growth_events


async def story_snapshot(db: AsyncSession, user: User) -> MemoirStorySnapshotResponse:
    identity = await projects.memoir_identity_response(db, user)
    result = MemoirStorySnapshotResponse(
        server_revision=0, identity=identity, automation_events=_empty_automation_events()
    )
    if not identity.active_project_id:
        return result
    project = await load_memoir_project(db, identity.active_project_id, user.id)
    result.server_revision = project.memoir_data_revision
    result.updated_at = project.updated_at.isoformat()
    transcripts = await voice_transcripts.list_voice_transcripts(db, user.id, project_id=project.id, limit=1)
    result.latest_transcript = transcripts.items[0] if transcripts.items else None
    jobs = await draft_progress.project_jobs(db, project.id)
    result.progress = await draft_progress.read_draft_progress(db, project_id=project.id, jobs=jobs)
    parent = next((job for job in jobs if job.job_type == "update_book_draft"), None)
    progress = DraftProgress.model_validate(parent.result_json or {}) if parent else DraftProgress()
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
            .where(MemoirBookPlanChapter.project_id == project.id)
            .order_by(MemoirBookPlanChapter.sort_order, MemoirBookPlanChapter.id)
        )
    ).all()
    chapter_views = {}
    for chapter in chapters:
        view = MemoirStoryChapterResponse(**chapter._asdict() | {"char_count": chapter.char_count or 0})
        view.readable = view.char_count > 0
        state = progress.chapters.get(chapter.id)
        if not chapter.include_in_book:
            view.state, view.state_text = "muted", "暂不入书"
        elif state and state.status in {"needs_input", "needs_review", "failed", "cancelled"}:
            view.state, view.state_text = "attention", "查看待处理事项"
        elif state and state.status in {"pending", "running"}:
            view.state, view.state_text = "draft", "后台整理中"
        elif chapter.id in progress.omitted_chapter_ids:
            view.state, view.state_text = "attention", "等待确认后入稿"
        elif view.readable:
            view.state, view.state_text = "settled", "可阅读"
        chapter_views[chapter.id] = view
    section_views = {}
    section_chapters = {}
    for section in (
        await db.execute(
            select(MemoirSection.id, MemoirSection.chapter_id, MemoirSection.title)
            .where(MemoirSection.project_id == project.id, MemoirSection.status == "active")
            .order_by(MemoirSection.sort_order, MemoirSection.id)
        )
    ).all():
        view = MemoirStorySectionResponse(id=section.id, title=section.title)
        section_views[section.id] = view
        section_chapters[section.id] = section.chapter_id
        chapter_views[section.chapter_id].sections.append(view)
    section_by_utterance = {}
    for material in (
        await db.execute(
            select(MemoirSectionMaterial.utterance_id, MemoirSectionMaterial.section_id).where(
                MemoirSectionMaterial.project_id == project.id
            )
        )
    ).all():
        if material.section_id in section_views:
            section_by_utterance[material.utterance_id] = material.section_id
            section_views[material.section_id].material_count += 1
            chapter_views[section_chapters[material.section_id]].material_count += 1
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
    for asset in (
        await db.execute(
            select(MemoirImageAsset.id, MemoirImageAsset.source_utterance_id, MemoirImageAsset.source_chapter_id).where(
                MemoirImageAsset.project_id == project.id, MemoirImageAsset.status == "ready"
            )
        )
    ).all():
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
    videos = await share_video_jobs.list_completed_share_video_counts(db, user_id=user.id, project_id=project.id)
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
    result.automation_events = await automation_events.list_automation_events(
        db, user_id=user.id, project_id=project.id, limit=8, completed_only=True
    )
    result.growth_events = story_growth_events(
        result.automation_events.items,
        chapter_ids=set(chapter_views),
        section_chapters=section_chapters,
        section_by_utterance=section_by_utterance,
    )
    return result
