from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.domain.memoir.types import (
    MemoirAutomationEventStatus,
    MemoirAutomationEventType,
    MemoirEditingJobStatus,
    MemoirEditingJobType,
)
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirEditingJob,
    MemoirBookPlanChapter,
)
from app.models.projects import Project
from app.schemas.memoir_book import MemoirBookChapterDraftGenerateRequest
from app.services.ai_types import AITextClient
from app.services.memoir import automation_event_metadata
from app.services.memoir import automation_event_resolution
from app.services.memoir import automation_events
from app.services.memoir import book_quality_audit
from app.services.memoir.book_drafts import generate_chapter_draft
from app.services.memoir.book_source_links import (
    ChapterSourceChangeState,
    chapter_source_change_state as _chapter_source_change_state,
    chapter_source_ref_dicts as _chapter_source_ref_dicts,
    source_ref_utterance_ids as _source_ref_utterance_ids,
)
from app.services.memoir.editing_jobs import EditingJobResult, enqueue_editing_job


AI_MODEL_METADATA_KEY = "ai_model"


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _project_ai_model(project: Project | None) -> str | None:
    metadata = project.metadata_json if project else {}
    value = (metadata or {}).get(AI_MODEL_METADATA_KEY)
    return value.strip() if isinstance(value, str) and value.strip() else None


def remember_project_ai_model(project: Project, ai_model: str | None) -> None:
    model = (ai_model or "").strip()
    if not model:
        return
    project.metadata_json = {
        **(project.metadata_json or {}),
        AI_MODEL_METADATA_KEY: model,
        "ai_model_updated_at": _utcnow().isoformat(),
    }


async def _chapter_latest_draft(db: AsyncSession, project_id: str, chapter_id: str) -> MemoirBookChapterDraft | None:
    return await db.scalar(
        select(MemoirBookChapterDraft)
        .where(
            MemoirBookChapterDraft.project_id == project_id,
            MemoirBookChapterDraft.chapter_id == chapter_id,
        )
        .options(selectinload(MemoirBookChapterDraft.versions))
    )


async def _current_source_change_state(
    db: AsyncSession,
    chapter: MemoirBookPlanChapter | None,
) -> ChapterSourceChangeState:
    if not chapter:
        return ChapterSourceChangeState(pending_source_ids=set(), removed_source_ids=set())
    refs = await _chapter_source_ref_dicts(db, chapter=chapter)
    current_source_ids = set(_source_ref_utterance_ids(refs))
    if not current_source_ids:
        return ChapterSourceChangeState(pending_source_ids=set(), removed_source_ids=set())
    return await _chapter_source_change_state(
        db,
        chapter=chapter,
        valid_utterance_ids=current_source_ids,
    )


async def schedule_chapter_auto_revision(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    ai_model: str | None = None,
    run_after: datetime | None = None,
) -> MemoirEditingJob | None:
    chapter = await db.get(MemoirBookPlanChapter, chapter_id)
    if not chapter or chapter.project_id != project_id:
        return None
    source_change_state = await _current_source_change_state(db, chapter)
    if not source_change_state.pending_source_ids and not source_change_state.removed_source_ids:
        return None

    draft = await _chapter_latest_draft(db, project_id, chapter_id)
    if not draft or not draft.versions:
        return None

    project = await db.get(Project, project_id)
    model = (ai_model or "").strip() or _project_ai_model(project)
    settings = get_settings()
    scheduled_at = run_after or (_utcnow() + timedelta(seconds=max(0, settings.memoir_chapter_revision_delay_seconds)))
    payload = {
        "base_version_id": draft.versions[-1].id,
        "pending_source_utterance_ids": sorted(source_change_state.pending_source_ids),
        "removed_source_utterance_ids": sorted(source_change_state.removed_source_ids),
        **({"ai_model": model} if model else {}),
    }
    return await enqueue_editing_job(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        job_type=MemoirEditingJobType.REVISE_CHAPTER,
        payload_json=payload,
        run_after=scheduled_at,
        dedupe_key=f"revise_chapter:{chapter_id}",
        replace_completed=True,
    )


async def schedule_chapter_auto_revisions(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_ids: set[str],
    ai_model: str | None = None,
) -> None:
    for chapter_id in sorted(chapter_ids):
        await schedule_chapter_auto_revision(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
            ai_model=ai_model,
        )


async def run_chapter_revision_job(
    db: AsyncSession,
    job: MemoirEditingJob,
    *,
    ai_client: AITextClient | None = None,
) -> EditingJobResult:
    try:
        project = await db.get(Project, job.project_id)
        if not project or not project.user_id:
            return EditingJobResult(status=MemoirEditingJobStatus.SKIPPED, error_message="project unavailable")
        if not job.chapter_id:
            return EditingJobResult(status=MemoirEditingJobStatus.SKIPPED, error_message="chapter unavailable")

        draft = await _chapter_latest_draft(db, job.project_id, job.chapter_id)
        if not draft or not draft.versions:
            return EditingJobResult(status=MemoirEditingJobStatus.SKIPPED, error_message="draft unavailable")
        base_version_id = str((job.payload_json or {}).get("base_version_id") or "")
        if draft.versions[-1].id != base_version_id:
            return EditingJobResult(status=MemoirEditingJobStatus.SKIPPED, error_message="draft changed")

        chapter = await db.get(MemoirBookPlanChapter, job.chapter_id)
        source_change_state = await _current_source_change_state(db, chapter)
        pending_source_ids = source_change_state.pending_source_ids
        removed_source_ids = source_change_state.removed_source_ids
        if not pending_source_ids and not removed_source_ids:
            return EditingJobResult(status=MemoirEditingJobStatus.SKIPPED, error_message="no source changes")

        model = str((job.payload_json or {}).get("ai_model") or _project_ai_model(project) or "").strip() or None
        pending_source_id_list = sorted(pending_source_ids)
        removed_source_id_list = sorted(removed_source_ids)
        changed_source_id_list = sorted(pending_source_ids | removed_source_ids)
        draft_response = await generate_chapter_draft(
            db,
            user_id=project.user_id,
            project_id=job.project_id,
            chapter_id=job.chapter_id,
            req=MemoirBookChapterDraftGenerateRequest(ai_model=model, force_regenerate=False),
            ai_client=ai_client,
        )
        title = chapter.title if chapter else "未命名章节"
        pending_count = len(pending_source_id_list)
        removed_count = len(removed_source_id_list)
        if pending_count and removed_count:
            summary_text = f"「{title}」已自动补写 {pending_count} 条新增/变化讲述，并移除 {removed_count} 条已删除讲述。"
        elif pending_count:
            summary_text = f"「{title}」已自动补写 {pending_count} 条新增讲述。"
        else:
            summary_text = f"「{title}」已自动移除 {removed_count} 条已删除讲述对应内容。"
        signature_source_ids = (
            ",".join(pending_source_id_list)
            if not removed_source_id_list
            else f"pending={','.join(pending_source_id_list)};removed={','.join(removed_source_id_list)}"
        )
        revision_event = await automation_events.record_or_update_automation_event(
            db,
            project_id=job.project_id,
            event_type=MemoirAutomationEventType.CHAPTER_AUTO_REVISED,
            status=MemoirAutomationEventStatus.COMPLETED,
            chapter_id=job.chapter_id,
            utterance_ids=changed_source_id_list,
            summary_text=summary_text,
            metadata_json={
                "source": "book_auto_revision",
                automation_event_metadata.EVENT_SIGNATURE_KEY: (
                    f"book_auto_revision:{job.chapter_id}:{signature_source_ids}"
                ),
                "job_id": job.id,
                "result_version_id": draft_response.latest_version.id if draft_response.latest_version else "",
                "pending_source_utterance_ids": pending_source_id_list,
                "removed_source_utterance_ids": removed_source_id_list,
                **({"ai_model": model} if model else {}),
            },
        )
        await automation_event_resolution.resolve_revised_chapter_audit_events(
            db,
            project_id=job.project_id,
            chapter_id=job.chapter_id,
            revision_event_id=revision_event.id,
            result_version_id=draft_response.latest_version.id if draft_response.latest_version else "",
        )
        await book_quality_audit.schedule_project_quality_audit(
            db,
            project_id=job.project_id,
            reason="chapter_auto_revision_completed",
        )
        return EditingJobResult(
            result_json={
                "result_version_id": draft_response.latest_version.id if draft_response.latest_version else "",
                "pending_source_utterance_ids": pending_source_id_list,
                "removed_source_utterance_ids": removed_source_id_list,
                "completed_at": _utcnow().isoformat(),
            },
        )
    except HTTPException as exc:
        await db.rollback()
        return EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED if exc.status_code == 409 else MemoirEditingJobStatus.FAILED,
            error_message=str(exc.detail),
        )
