"""Read-only projection of durable memoir work; polling never advances jobs."""

from sqlalchemy import JSON, cast, func, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import MemoirEditingJobType
from app.models.memoir import (
    MemoirEditingJob,
    MemoirSection,
    MemoirSectionMaterial,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.schemas.memoir_snapshots import MemoirDraftProgressResponse
from app.services.memoir import automatic_book_draft, editing_jobs, story_tree_growth
from app.services.memoir.material_roles import narrator_material_clause
from app.services.memoir.transcript_projection import source_text_hash


async def project_jobs(
    db: AsyncSession,
    project_id: str,
    *,
    voice_session_id: str | None = None,
) -> list[MemoirEditingJob]:
    jobs: list[MemoirEditingJob] = []
    for job_type in (
        MemoirEditingJobType.UPDATE_BOOK_DRAFT.value,
        MemoirEditingJobType.ORGANIZE_MATERIAL.value,
    ):
        query = select(MemoirEditingJob).where(
            MemoirEditingJob.project_id == project_id,
            MemoirEditingJob.job_type == job_type,
        )
        if voice_session_id and job_type == MemoirEditingJobType.ORGANIZE_MATERIAL.value:
            query = query.where(
                MemoirEditingJob.payload_json["voice_session_id"].as_string() == voice_session_id
            )
        job = await db.scalar(
            query.order_by(MemoirEditingJob.updated_at.desc(), MemoirEditingJob.id.desc()).limit(1)
        )
        if job:
            jobs.append(job)
    active = await db.scalar(
        select(MemoirEditingJob)
        .where(
            MemoirEditingJob.project_id == project_id,
            MemoirEditingJob.job_type.in_(
                [MemoirEditingJobType.UPDATE_BOOK_DRAFT.value, MemoirEditingJobType.ORGANIZE_MATERIAL.value]
            ),
            MemoirEditingJob.status.in_(editing_jobs.ACTIVE_EDITING_JOB_STATUSES),
        )
        .limit(1)
    )
    if active and all(job.id != active.id for job in jobs):
        jobs.append(active)
    return jobs


async def _source_counts_and_needs_user(db: AsyncSession, project_id: str) -> tuple[int, int, int]:
    source_text = func.coalesce(
        func.nullif(MemoirVoiceUtterance.transcript_edited, ""),
        MemoirVoiceUtterance.transcript_raw,
    )
    sources = (
        select(MemoirVoiceUtterance.id.label("id"), source_text.label("text"))
        .join(MemoirVoiceSession)
        .where(
            MemoirVoiceSession.project_id == project_id,
            narrator_material_clause(),
            func.length(func.trim(source_text)) > 0,
        )
        .cte("sources")
    )
    assigned = (
        select(MemoirSectionMaterial.utterance_id)
        .join(MemoirSection, MemoirSectionMaterial.section_id == MemoirSection.id)
        .where(MemoirSectionMaterial.project_id == project_id, MemoirSection.status == "active")
        .distinct()
        .cte("assigned")
    )
    material_count = await db.scalar(select(func.count()).select_from(sources)) or 0
    pending_count = await db.scalar(
        select(func.count()).select_from(sources).outerjoin(assigned, assigned.c.utterance_id == sources.c.id)
        .where(assigned.c.utterance_id.is_(None))
    ) or 0
    deferred = func.json_each(
        MemoirEditingJob.result_json["growth"]["deferred"]
    ).table_valued("key", "value").lateral()
    deferred_value = cast(deferred.c.value, JSON)
    rows = (
        await db.execute(
            select(deferred.c.key, deferred_value["text_hash"].as_string(), sources.c.text)
            .select_from(MemoirEditingJob)
            .join(deferred, true())
            .join(sources, sources.c.id == deferred.c.key)
            .outerjoin(assigned, assigned.c.utterance_id == sources.c.id)
            .where(
                MemoirEditingJob.project_id == project_id,
                MemoirEditingJob.job_type == MemoirEditingJobType.ORGANIZE_MATERIAL.value,
                deferred_value["status"].as_string() == "needs_user",
                assigned.c.utterance_id.is_(None),
            )
        )
    ).all()
    needs_user = {key for key, text_hash, text in rows if text_hash == source_text_hash(text)}
    return material_count, pending_count, len(needs_user)


async def read_draft_progress(
    db: AsyncSession,
    *,
    project_id: str,
    voice_session_id: str | None = None,
    jobs: list[MemoirEditingJob] | None = None,
) -> MemoirDraftProgressResponse:
    jobs = await project_jobs(db, project_id, voice_session_id=voice_session_id) if jobs is None else jobs
    parent = next((job for job in jobs if job.job_type == "update_book_draft"), None)
    growth_jobs = [job for job in jobs if job.job_type == "organize_material"]
    growth = next(
        (
            job
            for job in growth_jobs
            if not voice_session_id or (job.payload_json or {}).get("voice_session_id") == voice_session_id
        ),
        None,
    )
    progress = (
        automatic_book_draft.DraftProgress.model_validate(parent.result_json or {})
        if parent
        else automatic_book_draft.DraftProgress()
    )
    material_count, pending_material_count, needs_user_count = await _source_counts_and_needs_user(db, project_id)
    ready = sum(
        (item.status == "ready" or item.draft_version_id is not None)
        and key not in progress.omitted_chapter_ids for key, item in progress.chapters.items()
    )
    result = MemoirDraftProgressResponse(
        project_id=project_id,
        stage=progress.stage,
        readable_chapter_count=ready,
        total_chapter_count=len(progress.chapters),
        material_count=material_count,
        pending_material_count=pending_material_count,
        needs_user_material_count=needs_user_count,
        review_status=progress.review_status,
        review_error=progress.review_error,
    )
    failures = [job.error_message for job in (growth, parent) if job and job.status in {"failed", "cancelled"}]
    failures += [item.error_message for item in progress.chapters.values() if item.status in {"failed", "cancelled"}]
    result.can_retry = bool(failures or progress.review_status in {"failed", "cancelled"})
    if any(job.status in editing_jobs.ACTIVE_EDITING_JOB_STATUSES for job in jobs):
        result.can_retry = False
        result.status, result.title = "processing", "正在后台整理书稿"
        result.detail = {
            "organizing": "正在整理讲述和故事结构",
            "writing_chapters": "正在更新章节正文",
            "reviewing_book": "草稿已可阅读，正在检查全书",
        }.get(progress.stage, "讲述已经保存，离开页面后也会继续处理")
    elif failures:
        result.status, result.title, result.detail = (
            "failed",
            "本次整理需要继续处理",
            failures[0] or "后台处理失败，已保存的讲述和草稿会保留",
        )
    elif (
        needs_user_count
        or progress.preserved_manual
        or progress.omitted_chapter_ids
        or any(item.status in {"needs_input", "needs_review"} for item in progress.chapters.values())
    ):
        result.status, result.title = "needs_input", "有内容需要您确认"
        result.detail = "人工修订和已保存的稿件会保留，请查看章节中的待确认事项"
    elif (
        voice_session_id
        and growth
        and not (growth.result_json or {}).get("organized_utterance_ids")
        and not story_tree_growth._progress(growth).deferred
    ):
        result.status, result.title, result.detail = "no_change", "讲述已保存，故事结构没有新增", "已有草稿可以继续阅读"
    elif ready:
        result.status = (
            "ready" if ready == len(progress.chapters) and not result.pending_material_count else "partial_ready"
        )
        result.title = "可读草稿已更新" if result.status == "ready" else "已有部分草稿可阅读"
        result.detail = progress.review_error or (
            "全书检查已完成" if progress.review_status == "succeeded" else "草稿尚未完成全书检查"
        )
    elif material_count:
        result.status, result.title = "no_change", "讲述已保存，暂时没有新增正文"
        result.detail = "待整理的讲述会在下次更新时继续处理"
    return result


async def retry_draft_progress(db: AsyncSession, *, project_id: str) -> None:
    jobs = await project_jobs(db, project_id)
    growth = next((job for job in jobs if job.job_type == "organize_material"), None)
    if growth and growth.status in {"failed", "cancelled"}:
        await story_tree_growth.schedule_story_tree_growth(
            db, project_id=project_id, voice_session_id=growth.payload_json["voice_session_id"], source="retry"
        )
    await automatic_book_draft.schedule_book_draft(db, project_id=project_id, source="retry")
    await db.commit()
