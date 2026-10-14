"""Read-only projection of durable memoir work; polling never advances jobs."""

from sqlalchemy import JSON, cast, func, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import MemoirEditingJobType
from app.models.memoir import (
    MemoirEditingJob,
    MemoirSectionMaterial,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.schemas.memoir_snapshots import MemoirDraftProgressResponse
from app.services.memoir import automatic_book_draft, editing_jobs, story_tree_growth
from app.services.memoir.material_roles import narrator_material_clause
from app.services.memoir.transcript_projection import has_story_content, source_text_hash


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
        MemoirEditingJobType.GENERATE_BOOK_EDITORIAL_COPY.value,
    ):
        query = select(MemoirEditingJob).where(
            MemoirEditingJob.project_id == project_id,
            MemoirEditingJob.job_type == job_type,
        )
        if job_type == MemoirEditingJobType.GENERATE_BOOK_EDITORIAL_COPY.value:
            query = query.where(MemoirEditingJob.dedupe_key == f"automatic-editorial-copy:{project_id}")
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


async def _source_counts_and_needs_user(db: AsyncSession, project_id: str) -> tuple[int, int, int, str]:
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
        .where(MemoirSectionMaterial.project_id == project_id)
        .distinct()
        .cte("assigned")
    )
    material_count = await db.scalar(select(func.count()).select_from(sources)) or 0
    unassigned = (await db.execute(
        select(sources.c.id, sources.c.text).outerjoin(assigned, assigned.c.utterance_id == sources.c.id)
        .where(assigned.c.utterance_id.is_(None))
    )).all()
    # Use the organizer's content rule; saved acknowledgements are not unfinished work.
    pending_ids = {key for key, text in unassigned if has_story_content(text)}
    deferred = func.json_each(
        MemoirEditingJob.result_json["growth"]["deferred"]
    ).table_valued("key", "value").lateral()
    deferred_value = cast(deferred.c.value, JSON)
    rows = (
        await db.execute(
            select(
                deferred.c.key,
                deferred_value["status"].as_string(),
                deferred_value["text_hash"].as_string(),
                sources.c.text,
                deferred_value["reason"].as_string(),
            )
            .select_from(MemoirEditingJob)
            .join(deferred, true())
            .join(sources, sources.c.id == deferred.c.key)
            .outerjoin(assigned, assigned.c.utterance_id == sources.c.id)
            .where(
                MemoirEditingJob.project_id == project_id,
                MemoirEditingJob.job_type == MemoirEditingJobType.ORGANIZE_MATERIAL.value,
                assigned.c.utterance_id.is_(None),
            )
            .order_by(MemoirEditingJob.updated_at)
        )
    ).all()
    current = {key: (status, text, reason) for key, status, text_hash, text, reason in rows
        if key in pending_ids and text_hash == source_text_hash(text)}
    statuses = [value[0] for value in current.values()]
    need = next((value for value in current.values() if value[0] == "needs_user"), None)
    detail = f"关于“{need[1][:60]}”：{need[2] or '还需要一些背景信息'}。补充讲述后会自动继续整理。" if need else ""
    # Answers to the interviewer that are not story stay out of the book; they are not pending.
    return material_count, len(pending_ids) - statuses.count("not_story"), statuses.count("needs_user"), detail


async def read_draft_progress(
    db: AsyncSession,
    *,
    project_id: str,
    voice_session_id: str | None = None,
    jobs: list[MemoirEditingJob] | None = None,
) -> MemoirDraftProgressResponse:
    response, _ = await _draft_progress_projection(db, project_id=project_id,
        voice_session_id=voice_session_id, jobs=jobs)
    return response


async def _draft_progress_projection(
    db: AsyncSession,
    *,
    project_id: str,
    voice_session_id: str | None = None,
    jobs: list[MemoirEditingJob] | None = None,
) -> tuple[MemoirDraftProgressResponse, bool]:
    jobs = await project_jobs(db, project_id, voice_session_id=voice_session_id) if jobs is None else jobs
    parent = next((job for job in jobs if job.job_type == "update_book_draft"), None)
    copy_job = next((job for job in jobs if job.job_type == "generate_book_editorial_copy"), None)
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
    material_count, pending_material_count, needs_user_count, needs_user_detail = await _source_counts_and_needs_user(db, project_id)
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
    copy_failed = bool(copy_job and copy_job.status in {"failed", "cancelled"})
    copy_only_retry = (copy_failed and not failures and pending_material_count <= needs_user_count
        and progress.review_status not in {"failed", "cancelled"})
    if copy_failed:
        failures.append(copy_job.error_message)
    result.can_retry = bool(failures or pending_material_count > needs_user_count
        or progress.review_status in {"failed", "cancelled"})
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
        progress.preserved_manual
        or progress.omitted_chapter_ids
        or any(item.status in {"needs_input", "needs_review"} for item in progress.chapters.values())
    ):
        result.can_retry = False
        result.status, result.title = "needs_input", "有内容需要您确认"
        result.detail = "人工修订和已保存的稿件会保留，请查看章节中的待确认事项"
    elif pending_material_count:
        result.status = "partial_ready" if ready else "no_change"
        result.title = "部分讲述还没整理进书稿"
        result.detail = needs_user_detail or "讲述已经保存，可以继续整理；已有正文仍可阅读。"
    elif (
        voice_session_id
        and growth
        and not (growth.result_json or {}).get("organized_utterance_ids")
        and all(item.status == "not_story" for item in story_tree_growth._progress(growth).deferred.values())
    ):
        result.status, result.title, result.detail = "no_change", "讲述已保存，故事结构没有新增", "已有草稿可以继续阅读"
    elif ready:
        result.status = (
            "ready" if ready == len(progress.chapters) and not result.pending_material_count else "partial_ready"
        )
        result.title = "书稿可阅读" if result.status == "ready" else "已有部分草稿可阅读"
        result.detail = progress.review_error or (
            "全书检查已完成" if progress.review_status == "succeeded" else "草稿尚未完成全书检查"
        )
    elif material_count:
        result.status, result.title = "no_change", "讲述已保存，暂时没有新增正文"
        result.detail = "待整理的讲述会在下次更新时继续处理"
    return result, result.can_retry and copy_only_retry


async def retry_draft_progress(db: AsyncSession, *, project_id: str) -> None:
    jobs = await project_jobs(db, project_id)
    progress, copy_only_retry = await _draft_progress_projection(db, project_id=project_id, jobs=jobs)
    if not progress.can_retry:
        return
    if copy_only_retry:
        from app.models.projects import Project
        from app.schemas.memoir_book import MemoirBookOutputEditorialCopyRegenerateRequest
        from app.services.memoir.book_generation_jobs import schedule_book_editorial_copy_generation
        project = await db.get(Project, project_id)
        await schedule_book_editorial_copy_generation(db, project_id=project_id, user_id=project.user_id,
            req=MemoirBookOutputEditorialCopyRegenerateRequest(), automatic=True, retry=True)
        return
    growth = next((job for job in jobs if job.job_type == "organize_material"), None)
    retry_material = progress.pending_material_count > progress.needs_user_material_count
    if growth and (growth.status in {"failed", "cancelled"} or retry_material):
        await story_tree_growth.schedule_story_tree_growth(
            db, project_id=project_id, voice_session_id=growth.payload_json["voice_session_id"], source="retry"
        )
    elif growth is None and retry_material:
        sessions = (await db.scalars(select(MemoirVoiceSession).where(
            MemoirVoiceSession.project_id == project_id,
            MemoirVoiceSession.session_goal == "open_narration",
        ).order_by(MemoirVoiceSession.created_at.desc()))).all()
        for session in sessions:
            if await story_tree_growth.schedule_story_tree_growth(
                db, project_id=project_id, voice_session_id=session.id, source="retry",
            ) is not None:
                break
    await automatic_book_draft.schedule_book_draft(db, project_id=project_id, source="retry", settled=True)
    await db.commit()
