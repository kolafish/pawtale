from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified

from app.clock import utcnow_naive
from app.config import DATA_DIR, get_settings
from app.database import async_session_maker
from app.domain.memoir.types import MemoirShareVideoJobStatus
from app.models.memoir import MemoirMaterialShareVideo, MemoirShareVideoOutput
from app.models.projects import Project
from app.schemas.memoir_voice import (
    MemoirShareVideoJobCreateRequest,
    MemoirShareVideoJobResponse,
    MemoirShareVideoOutputResponse,
    MemoirShareVideoCountResponse,
)
from app.services.memoir import share_video_background_orchestration as background_orchestration
from app.services.memoir import share_video_rendering as rendering
from app.services.memoir.audio_files import audio_file_path
from app.services.memoir.job_leases import (
    JobLease,
    JobLeaseLostError,
    lease_deadline,
    new_job_lease,
    run_with_lease_heartbeat,
)
from app.services.memoir.share_video_backgrounds import (
    SHARE_VIDEO_BACKGROUND_MODE_SINGLE,
    normalize_share_video_background_mode,
    normalize_share_video_background_style,
)
from app.services.memoir.share_video_custom_backgrounds import (
    SHARE_VIDEO_BACKGROUND_SOURCE_CLASSIC,
    SHARE_VIDEO_BACKGROUND_SOURCE_CUSTOM,
    load_custom_share_video_background_asset,
    normalize_share_video_background_source,
)
from app.services.memoir.share_video_alignment import (
    MAX_SELECT_MS,
    MIN_SELECT_MS,
    build_share_video_alignment,
    share_video_char_range_ms,
)
from app.services.memoir.share_video_metadata import ShareVideoJobMetadata
from app.services.memoir.project_ai_preferences import select_project_ai_model
from app.services.memoir.voice_transcripts import _load_owned_utterance


MEMOIR_SHARE_VIDEO_DIR = DATA_DIR / "memoir" / "share-videos"
_VIDEO_MIME_TYPE = "video/mp4"
_COMPLETION_MARKER_SUFFIX = ".complete"
_COMPLETION_RECOVERY_POLL_SECONDS = 1.0
logger = logging.getLogger(__name__)


class ShareVideoLeaseLostError(RuntimeError):
    pass


def _set_job_metadata(job: MemoirMaterialShareVideo, metadata: ShareVideoJobMetadata) -> None:
    job.metadata_json = metadata.to_json()
    flag_modified(job, "metadata_json")


def _storage_path(user_id: str, job_id: str) -> str:
    return f"memoir/share-videos/{user_id}/{job_id}.mp4"


def share_video_file_path(
    job: MemoirMaterialShareVideo,
    output: MemoirShareVideoOutput | None = None,
) -> Path:
    storage_path = Path(
        output.storage_path if output else _storage_path(job.user_id, job.id)
    )
    try:
        relative_path = storage_path.relative_to(Path("memoir") / "share-videos")
    except ValueError:
        return DATA_DIR / storage_path
    return MEMOIR_SHARE_VIDEO_DIR / relative_path


def _completion_marker_path(video_path: Path) -> Path:
    return video_path.with_name(f"{video_path.name}{_COMPLETION_MARKER_SUFFIX}")


def _publish_completed_video(video_path: Path) -> int:
    size_bytes = video_path.stat().st_size
    if size_bytes <= 0:
        raise rendering.ShareVideoGenerationError("生成的视频文件为空")
    marker_path = _completion_marker_path(video_path)
    temporary_marker_path = marker_path.with_name(f"{marker_path.name}.tmp")
    temporary_marker_path.write_text(str(size_bytes), encoding="ascii")
    temporary_marker_path.replace(marker_path)
    return size_bytes


def _published_video_size(video_path: Path) -> int | None:
    marker_path = _completion_marker_path(video_path)
    try:
        expected_size = int(marker_path.read_text(encoding="ascii").strip())
        actual_size = video_path.stat().st_size
    except (OSError, ValueError):
        return None
    if expected_size <= 0 or actual_size != expected_size:
        return None
    return actual_size


def _remove_video_completion_marker(video_path: Path) -> None:
    try:
        _completion_marker_path(video_path).unlink(missing_ok=True)
    except OSError:
        logger.exception("failed to remove share video completion marker path=%s", video_path)


def _trim_selection(text: str, start_char: int, end_char: int) -> tuple[int, int]:
    if start_char < 0 or end_char > len(text) or end_char <= start_char:
        raise HTTPException(status_code=400, detail="选择的文字范围无效")
    while start_char < end_char and text[start_char].isspace():
        start_char += 1
    while end_char > start_char and text[end_char - 1].isspace():
        end_char -= 1
    if end_char <= start_char:
        raise HTTPException(status_code=400, detail="请选择要分享的文字")
    return start_char, end_char


def _job_response(job: MemoirMaterialShareVideo) -> MemoirShareVideoJobResponse:
    metadata = ShareVideoJobMetadata.from_json(job.metadata_json)
    output = job.output
    succeeded = job.status == MemoirShareVideoJobStatus.SUCCEEDED.value
    if succeeded != (output is not None):
        raise RuntimeError(
            f"share video job/output invariant violated for job {job.id}"
        )
    return MemoirShareVideoJobResponse(
        id=job.id,
        project_id=job.project_id,
        utterance_id=job.utterance_id,
        audio_file_id=job.audio_file_id,
        status=MemoirShareVideoJobStatus(job.status),
        selected_text=job.selected_text,
        start_char=job.start_char,
        end_char=job.end_char,
        start_ms=job.start_ms,
        end_ms=job.end_ms,
        duration_ms=max(0, job.end_ms - job.start_ms),
        background_source=metadata.background_source,
        background_style=metadata.background_style,
        background_mode=metadata.background_mode,
        background_effective_mode=metadata.background_effective_mode,
        custom_background_asset_id=metadata.custom_background_asset_id,
        background_scene_count=metadata.background_scene_count,
        background_status=metadata.background_status,
        background_provider=metadata.background_provider,
        background_model=metadata.background_model,
        background_cache_hit=metadata.background_cache_hit,
        background_error_message=metadata.background_error_message,
        output=(
            MemoirShareVideoOutputResponse(
                mime_type=output.mime_type,
                size_bytes=output.size_bytes,
                download_url=f"/api/mobile/memoir/share-video/jobs/{job.id}/download",
            )
            if output is not None
            else None
        ),
        error_message=job.error_message,
        created_at=job.created_at,
        updated_at=job.updated_at,
        completed_at=job.completed_at,
    )


async def create_share_video_job(
    db: AsyncSession,
    utterance_id: str,
    user_id: str,
    req: MemoirShareVideoJobCreateRequest,
    *,
    allow_admin: bool = False,
) -> MemoirShareVideoJobResponse:
    utterance = await _load_owned_utterance(db, utterance_id, user_id, allow_admin=allow_admin)
    selected_ai_model = select_project_ai_model(
        utterance.voice_session.project,
        req.ai_model,
    )
    alignment = build_share_video_alignment(utterance)
    start_char, end_char = _trim_selection(alignment.transcript_text, req.start_char, req.end_char)
    start_ms, end_ms = share_video_char_range_ms(alignment, start_char, end_char)
    try:
        background_source = normalize_share_video_background_source(req.background_source)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        background_style = normalize_share_video_background_style(req.background_style)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        background_mode = normalize_share_video_background_mode(req.background_mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    duration_ms = end_ms - start_ms
    if duration_ms < MIN_SELECT_MS:
        raise HTTPException(status_code=400, detail=f"分享视频至少需要 {MIN_SELECT_MS // 1000} 秒音频")
    if duration_ms > MAX_SELECT_MS:
        raise HTTPException(status_code=400, detail=f"分享视频不能超过 {MAX_SELECT_MS // 1000} 秒")
    audio_file = utterance.audio_file
    if not audio_file:
        raise HTTPException(status_code=400, detail="素材没有可分享的音频")
    custom_background_asset_id: str | None = None
    if background_source == SHARE_VIDEO_BACKGROUND_SOURCE_CUSTOM:
        if not req.custom_background_asset_id:
            raise HTTPException(status_code=400, detail="请选择自定义背景图")
        custom_asset, _custom_path = await load_custom_share_video_background_asset(
            db,
            user_id=user_id,
            project_id=utterance.voice_session.project_id,
            asset_id=req.custom_background_asset_id,
            allow_admin=allow_admin,
        )
        custom_background_asset_id = custom_asset.id
    elif req.custom_background_asset_id:
        raise HTTPException(status_code=400, detail="只有自定义背景图模式可以选择背景图片")

    effective_background_mode = (
        SHARE_VIDEO_BACKGROUND_MODE_SINGLE
        if background_source in {SHARE_VIDEO_BACKGROUND_SOURCE_CUSTOM, SHARE_VIDEO_BACKGROUND_SOURCE_CLASSIC}
        else background_orchestration.effective_background_mode(background_mode, duration_ms)
    )

    job = MemoirMaterialShareVideo(
        project_id=utterance.voice_session.project_id,
        utterance_id=utterance.id,
        audio_file_id=audio_file.id,
        user_id=user_id,
        status=MemoirShareVideoJobStatus.PENDING.value,
        run_after=utcnow_naive(),
        text_source=alignment.text_source,
        text_hash=alignment.text_hash,
        selected_text=alignment.transcript_text[start_char:end_char],
        start_char=start_char,
        end_char=end_char,
        start_ms=start_ms,
        end_ms=end_ms,
        metadata_json=ShareVideoJobMetadata(
            alignment_strategy=alignment.strategy,
            alignment_confidence=alignment.confidence,
            char_timings=alignment.char_timings[start_char:end_char],
            ai_model=selected_ai_model,
            background_source=background_source,
            background_style=background_style,
            background_mode=background_mode,
            background_effective_mode=effective_background_mode,
            custom_background_asset_id=custom_background_asset_id,
            background_scene_count=1 if background_source == SHARE_VIDEO_BACKGROUND_SOURCE_CUSTOM else 0,
            background_status=background_orchestration.initial_background_status(
                background_source,
                ai_background_enabled=get_settings().memoir_share_video_ai_background_enabled,
            ),
        ).to_json(),
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return _job_response(job)


async def _load_owned_job(
    db: AsyncSession,
    job_id: str,
    user_id: str,
    *,
    allow_admin: bool = False,
) -> MemoirMaterialShareVideo:
    filters = [MemoirMaterialShareVideo.id == job_id]
    if not allow_admin:
        filters.append(or_(MemoirMaterialShareVideo.user_id == user_id, Project.user_id == user_id))
    result = await db.execute(
        select(MemoirMaterialShareVideo)
        .join(Project, MemoirMaterialShareVideo.project_id == Project.id)
        .where(*filters)
        .options(
            selectinload(MemoirMaterialShareVideo.audio_file),
            selectinload(MemoirMaterialShareVideo.utterance),
        )
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="分享视频任务不存在")
    return job


async def get_share_video_job(
    db: AsyncSession,
    job_id: str,
    user_id: str,
    *,
    allow_admin: bool = False,
) -> MemoirShareVideoJobResponse:
    job = await _load_owned_job(db, job_id, user_id, allow_admin=allow_admin)
    return _job_response(job)


async def list_completed_share_video_counts(
    db: AsyncSession,
    *,
    project_id: str,
    user_id: str,
) -> list[MemoirShareVideoCountResponse]:
    rows = (
        await db.execute(
            select(
                MemoirMaterialShareVideo.utterance_id,
                func.count(MemoirShareVideoOutput.id),
            )
            .join(
                MemoirShareVideoOutput,
                MemoirShareVideoOutput.job_id == MemoirMaterialShareVideo.id,
            )
            .where(
                MemoirMaterialShareVideo.project_id == project_id,
                MemoirMaterialShareVideo.user_id == user_id,
                MemoirMaterialShareVideo.status == MemoirShareVideoJobStatus.SUCCEEDED.value,
            )
            .group_by(MemoirMaterialShareVideo.utterance_id)
            .order_by(MemoirMaterialShareVideo.utterance_id)
        )
    ).all()
    return [
        MemoirShareVideoCountResponse(utterance_id=utterance_id, count=count)
        for utterance_id, count in rows
    ]


async def load_completed_share_video_job(
    db: AsyncSession,
    job_id: str,
    user_id: str,
    *,
    allow_admin: bool = False,
) -> tuple[MemoirShareVideoOutput, Path]:
    job = await _load_owned_job(db, job_id, user_id, allow_admin=allow_admin)
    output = job.output
    if job.status != MemoirShareVideoJobStatus.SUCCEEDED.value or output is None:
        raise HTTPException(status_code=409, detail="分享视频还没有生成完成")
    path = share_video_file_path(job, output)
    if not path.exists():
        raise HTTPException(status_code=404, detail="分享视频文件不存在")
    return output, path


def _claimable_share_video_job(now: datetime, max_attempts: int):
    attempts_available = MemoirMaterialShareVideo.attempts < max(1, max_attempts)
    return and_(
        attempts_available,
        or_(
            and_(
                MemoirMaterialShareVideo.status == MemoirShareVideoJobStatus.PENDING.value,
                or_(
                    MemoirMaterialShareVideo.run_after.is_(None),
                    MemoirMaterialShareVideo.run_after <= now,
                ),
            ),
            and_(
                MemoirMaterialShareVideo.status == MemoirShareVideoJobStatus.RUNNING.value,
                or_(
                    MemoirMaterialShareVideo.lease_expires_at.is_(None),
                    MemoirMaterialShareVideo.lease_expires_at <= now,
                ),
            ),
        ),
    )


def _leased_share_video_job(lease: JobLease):
    return and_(
        MemoirMaterialShareVideo.id == lease.job_id,
        MemoirMaterialShareVideo.status == MemoirShareVideoJobStatus.RUNNING.value,
        MemoirMaterialShareVideo.lease_token == lease.token,
    )


async def _fail_exhausted_share_video_jobs(
    db: AsyncSession,
    *,
    now: datetime,
    max_attempts: int,
) -> None:
    use_skip_locked = db.get_bind().dialect.name == "postgresql"
    statement = select(MemoirMaterialShareVideo).where(
        MemoirMaterialShareVideo.status == MemoirShareVideoJobStatus.RUNNING.value,
        MemoirMaterialShareVideo.attempts >= max(1, max_attempts),
        or_(
            MemoirMaterialShareVideo.lease_expires_at.is_(None),
            MemoirMaterialShareVideo.lease_expires_at <= now,
        ),
    )
    if use_skip_locked:
        statement = statement.with_for_update(
            skip_locked=True,
            of=MemoirMaterialShareVideo,
        )
    exhausted = (
        await db.scalars(statement)
    ).all()
    if not use_skip_locked:
        await db.commit()
    for job in exhausted:
        result = await db.execute(
            update(MemoirMaterialShareVideo)
            .where(
                MemoirMaterialShareVideo.id == job.id,
                MemoirMaterialShareVideo.status == MemoirShareVideoJobStatus.RUNNING.value,
                MemoirMaterialShareVideo.attempts >= max(1, max_attempts),
                or_(
                    MemoirMaterialShareVideo.lease_expires_at.is_(None),
                    MemoirMaterialShareVideo.lease_expires_at <= now,
                ),
            )
            .values(
                status=MemoirShareVideoJobStatus.FAILED.value,
                locked_at=None,
                lease_token=None,
                lease_expires_at=None,
                error_message="分享视频任务执行中断，已达到最大重试次数",
                completed_at=now,
            )
        )
        if result.rowcount == 1:
            try:
                target_path = share_video_file_path(job)
                target_path.unlink(missing_ok=True)
                _completion_marker_path(target_path).unlink(missing_ok=True)
            except OSError:
                logger.exception("failed to remove abandoned share video job_id=%s", job.id)
    await db.commit()


async def _claim_share_video_job_ids(
    db: AsyncSession,
    job_ids: list[str],
    *,
    now: datetime,
    lease_seconds: int,
    max_attempts: int,
) -> list[JobLease]:
    claimable = _claimable_share_video_job(now, max_attempts)
    leases: list[JobLease] = []
    for job_id in job_ids:
        lease = new_job_lease(job_id)
        result = await db.execute(
            update(MemoirMaterialShareVideo)
            .where(MemoirMaterialShareVideo.id == job_id, claimable)
            .values(
                status=MemoirShareVideoJobStatus.RUNNING.value,
                run_after=None,
                locked_at=now,
                lease_token=lease.token,
                lease_expires_at=lease_deadline(now, lease_seconds),
                attempts=MemoirMaterialShareVideo.attempts + 1,
                error_message="",
                completed_at=None,
            )
        )
        if result.rowcount == 1:
            leases.append(lease)
    await db.commit()
    return leases


async def claim_due_share_video_jobs(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int | None = None,
    lease_seconds: int | None = None,
    max_attempts: int | None = None,
) -> list[JobLease]:
    settings = get_settings()
    claimed_at = now or utcnow_naive()
    batch_size = limit or max(1, settings.memoir_share_video_batch_size)
    lease_duration = lease_seconds or max(1, settings.memoir_share_video_lease_seconds)
    attempt_limit = max_attempts or max(1, settings.memoir_share_video_max_attempts)
    await _fail_exhausted_share_video_jobs(
        db,
        now=claimed_at,
        max_attempts=attempt_limit,
    )
    use_skip_locked = db.get_bind().dialect.name == "postgresql"
    statement = (
        select(MemoirMaterialShareVideo.id)
        .where(_claimable_share_video_job(claimed_at, attempt_limit))
        .order_by(
            MemoirMaterialShareVideo.run_after,
            MemoirMaterialShareVideo.created_at,
        )
        .limit(batch_size)
    )
    if use_skip_locked:
        statement = statement.with_for_update(skip_locked=True)
    candidate_ids = (await db.scalars(statement)).all()
    if not use_skip_locked:
        await db.commit()
    return await _claim_share_video_job_ids(
        db,
        candidate_ids,
        now=claimed_at,
        lease_seconds=lease_duration,
        max_attempts=attempt_limit,
    )


async def claim_share_video_job(
    db: AsyncSession,
    job_id: str,
    *,
    now: datetime | None = None,
    lease_seconds: int | None = None,
    max_attempts: int | None = None,
) -> JobLease | None:
    settings = get_settings()
    claimed_at = now or utcnow_naive()
    lease_duration = lease_seconds or max(1, settings.memoir_share_video_lease_seconds)
    attempt_limit = max_attempts or max(1, settings.memoir_share_video_max_attempts)
    await db.commit()
    leases = await _claim_share_video_job_ids(
        db,
        [job_id],
        now=claimed_at,
        lease_seconds=lease_duration,
        max_attempts=attempt_limit,
    )
    return leases[0] if leases else None


async def renew_share_video_job_lease(
    db: AsyncSession,
    lease: JobLease,
    *,
    now: datetime | None = None,
    lease_seconds: int | None = None,
) -> bool:
    settings = get_settings()
    renewed_at = now or utcnow_naive()
    lease_duration = lease_seconds or max(1, settings.memoir_share_video_lease_seconds)
    result = await db.execute(
        update(MemoirMaterialShareVideo)
        .where(_leased_share_video_job(lease))
        .values(lease_expires_at=lease_deadline(renewed_at, lease_duration))
    )
    await db.commit()
    return result.rowcount == 1


async def _commit_share_video_progress(
    db: AsyncSession,
    lease: JobLease,
    *,
    lease_seconds: int,
) -> None:
    with db.no_autoflush:
        result = await db.execute(
            update(MemoirMaterialShareVideo)
            .where(_leased_share_video_job(lease))
            .values(lease_expires_at=lease_deadline(utcnow_naive(), lease_seconds))
        )
    if result.rowcount != 1:
        await db.rollback()
        raise ShareVideoLeaseLostError("share video job lease lost")
    await db.commit()


async def _complete_share_video_job(
    db: AsyncSession,
    lease: JobLease,
    *,
    storage_path: str,
    size_bytes: int,
) -> bool:
    completed_at = utcnow_naive()
    result = await db.execute(
        update(MemoirMaterialShareVideo)
        .where(_leased_share_video_job(lease))
        .values(
            status=MemoirShareVideoJobStatus.SUCCEEDED.value,
            run_after=None,
            locked_at=None,
            lease_token=None,
            lease_expires_at=None,
            error_message="",
            completed_at=completed_at,
        )
        .returning(MemoirMaterialShareVideo.project_id)
    )
    project_id = result.scalar_one_or_none()
    if project_id is None:
        await db.rollback()
        return False
    db.add(
        MemoirShareVideoOutput(
            job_id=lease.job_id,
            project_id=project_id,
            storage_path=storage_path,
            mime_type=_VIDEO_MIME_TYPE,
            size_bytes=size_bytes,
            created_at=completed_at,
        )
    )
    await db.commit()
    return True


async def _recover_published_share_video(
    session_maker: async_sessionmaker[AsyncSession],
    lease: JobLease,
) -> bool:
    async with session_maker() as db:
        job = await db.get(MemoirMaterialShareVideo, lease.job_id)
        if job is None:
            return False
        if job.status == MemoirShareVideoJobStatus.SUCCEEDED.value and job.output is not None:
            _remove_video_completion_marker(share_video_file_path(job, job.output))
            return True
        if (
            job.status != MemoirShareVideoJobStatus.RUNNING.value
            or job.lease_token != lease.token
        ):
            return False
        target_path = share_video_file_path(job)
        size_bytes = _published_video_size(target_path)
        if size_bytes is None:
            return False
        completed = await _complete_share_video_job(
            db,
            lease,
            storage_path=_storage_path(job.user_id, job.id),
            size_bytes=size_bytes,
        )
    if completed:
        _remove_video_completion_marker(target_path)
        logger.warning("recovered published share video job_id=%s", lease.job_id)
    return completed


async def _wait_for_published_share_video(
    session_maker: async_sessionmaker[AsyncSession],
    lease: JobLease,
    *,
    poll_seconds: float,
) -> None:
    async with session_maker() as db:
        job = await db.scalar(
            select(MemoirMaterialShareVideo).where(_leased_share_video_job(lease))
        )
        target_path = share_video_file_path(job) if job is not None else None
    if target_path is None:
        await asyncio.Event().wait()
    interval = max(0.01, poll_seconds)
    while _published_video_size(target_path) is None:
        await asyncio.sleep(interval)


async def _fail_share_video_job(
    db: AsyncSession,
    lease: JobLease,
    *,
    target_path: Path,
    error: Exception,
    max_attempts: int,
    retry_seconds: int,
    metadata: ShareVideoJobMetadata | None = None,
) -> None:
    job = await db.scalar(
        select(MemoirMaterialShareVideo).where(_leased_share_video_job(lease))
    )
    if job is None:
        return
    failed_at = utcnow_naive()
    should_retry = job.attempts < max(1, max_attempts)
    retry_delay = min(300, max(1, retry_seconds) * (2 ** max(0, job.attempts - 1)))
    next_attempt_at = failed_at + timedelta(seconds=retry_delay) if should_retry else None
    status = (
        MemoirShareVideoJobStatus.PENDING.value
        if should_retry
        else MemoirShareVideoJobStatus.FAILED.value
    )
    error_message = " ".join(str(error or "").split())[:1000] or "分享视频生成失败"
    with db.no_autoflush:
        result = await db.execute(
            update(MemoirMaterialShareVideo)
            .where(_leased_share_video_job(lease))
            .values(
                status=status,
                run_after=next_attempt_at,
                locked_at=None,
                lease_token=None,
                lease_expires_at=None,
                error_message=error_message,
                **({"metadata_json": metadata.to_json()} if metadata else {}),
                completed_at=None if should_retry else failed_at,
            )
        )
    if result.rowcount != 1:
        await db.rollback()
        return
    try:
        target_path.unlink(missing_ok=True)
        _completion_marker_path(target_path).unlink(missing_ok=True)
    except OSError:
        logger.exception("failed to remove partial share video job_id=%s", lease.job_id)
    await db.commit()


async def run_share_video_job_with_session(
    db: AsyncSession,
    lease: JobLease,
    *,
    lease_seconds: int | None = None,
    max_attempts: int | None = None,
    retry_seconds: int | None = None,
) -> bool:
    settings = get_settings()
    lease_duration = lease_seconds or max(1, settings.memoir_share_video_lease_seconds)
    attempt_limit = max_attempts or max(1, settings.memoir_share_video_max_attempts)
    retry_delay = retry_seconds or max(1, settings.memoir_share_video_retry_seconds)
    job = await db.scalar(
        select(MemoirMaterialShareVideo)
        .where(_leased_share_video_job(lease))
        .options(
            selectinload(MemoirMaterialShareVideo.audio_file),
            selectinload(MemoirMaterialShareVideo.utterance),
        )
    )
    if job is None:
        return False
    target_path = share_video_file_path(job)
    try:
        metadata = ShareVideoJobMetadata.from_json(job.metadata_json)

        async def persist_background_progress(updated_metadata: ShareVideoJobMetadata) -> None:
            _set_job_metadata(job, updated_metadata)
            await _commit_share_video_progress(db, lease, lease_seconds=lease_duration)

        prepared_background = await background_orchestration.prepare_share_video_background(
            db,
            job=job,
            metadata=metadata,
            persist_progress=persist_background_progress,
            ai_background_enabled=settings.memoir_share_video_ai_background_enabled,
            volc_ark_api_key=str(getattr(settings, "volc_ark_api_key", "") or ""),
        )
        background_image_path = prepared_background.image_path
        background_segments = prepared_background.segments
        target_path = share_video_file_path(job)
        render_request = rendering.ShareVideoRenderRequest(
            source_audio_path=audio_file_path(job.audio_file),
            source_audio_mime_type=job.audio_file.mime_type,
            target_path=target_path,
            selected_text=job.selected_text,
            char_timings=metadata.char_timing_dicts(),
            start_ms=job.start_ms,
            end_ms=job.end_ms,
            background_image_path=background_image_path,
            background_segments=background_segments,
        )
        try:
            await rendering.render_share_video_clip(render_request)
        except rendering.ShareVideoGenerationError as exc:
            if background_image_path is None and not background_segments:
                raise
            metadata.background_status = "fallback"
            metadata.background_error_message = f"背景图渲染失败，已回退经典样式：{str(exc)[:400]}"
            _set_job_metadata(job, metadata)
            await _commit_share_video_progress(db, lease, lease_seconds=lease_duration)
            await rendering.render_share_video_clip(
                rendering.ShareVideoRenderRequest(
                    source_audio_path=render_request.source_audio_path,
                    source_audio_mime_type=render_request.source_audio_mime_type,
                    target_path=render_request.target_path,
                    selected_text=render_request.selected_text,
                    char_timings=render_request.char_timings,
                    start_ms=render_request.start_ms,
                    end_ms=render_request.end_ms,
                    background_image_path=None,
                    background_segments=None,
                )
            )
        size_bytes = _publish_completed_video(target_path)
        completed = await _complete_share_video_job(
            db,
            lease,
            storage_path=_storage_path(job.user_id, job.id),
            size_bytes=size_bytes,
        )
        if completed:
            _remove_video_completion_marker(target_path)
        return completed
    except ShareVideoLeaseLostError:
        await db.rollback()
        logger.warning("share video job lease lost job_id=%s", lease.job_id)
        return False
    except Exception as exc:
        await db.rollback()
        logger.exception("share video job failed job_id=%s", lease.job_id)
        await _fail_share_video_job(
            db,
            lease,
            target_path=target_path,
            error=exc,
            max_attempts=attempt_limit,
            retry_seconds=retry_delay,
        )
        return False


async def run_share_video_job(
    lease: JobLease,
    *,
    session_maker: async_sessionmaker[AsyncSession] = async_session_maker,
    completion_recovery_poll_seconds: float = _COMPLETION_RECOVERY_POLL_SECONDS,
    timeout_seconds: float | None = None,
) -> bool:
    settings = get_settings()
    lease_seconds = max(1, settings.memoir_share_video_lease_seconds)
    heartbeat_seconds = min(
        max(1, settings.memoir_share_video_heartbeat_seconds),
        max(1, lease_seconds // 3),
    )
    deadline_seconds = max(
        0.01,
        timeout_seconds
        if timeout_seconds is not None
        else settings.memoir_share_video_job_deadline_seconds,
    )

    async def renew() -> bool:
        try:
            async with session_maker() as heartbeat_db:
                return await renew_share_video_job_lease(
                    heartbeat_db,
                    lease,
                    lease_seconds=lease_seconds,
                )
        except Exception:
            logger.exception("share video job lease heartbeat failed job_id=%s", lease.job_id)
            return False

    async def work() -> bool:
        async with session_maker() as db:
            return await run_share_video_job_with_session(
                db,
                lease,
                lease_seconds=lease_seconds,
                max_attempts=max(1, settings.memoir_share_video_max_attempts),
                retry_seconds=max(1, settings.memoir_share_video_retry_seconds),
            )

    try:
        if await _recover_published_share_video(session_maker, lease):
            return True
    except Exception:
        logger.exception("share video preflight completion recovery failed job_id=%s", lease.job_id)

    work_task = asyncio.create_task(run_with_lease_heartbeat(
        work,
        renew,
        interval_seconds=heartbeat_seconds,
    ))
    recovery_task = asyncio.create_task(_wait_for_published_share_video(
        session_maker,
        lease,
        poll_seconds=completion_recovery_poll_seconds,
    ))
    try:
        done, _pending = await asyncio.wait(
            {work_task, recovery_task},
            return_when=asyncio.FIRST_COMPLETED,
            timeout=deadline_seconds,
        )
        if not done:
            work_task.cancel()
            recovery_task.cancel()
            await asyncio.gather(work_task, recovery_task, return_exceptions=True)
            if await _recover_published_share_video(session_maker, lease):
                return True
            error_message = (
                f"分享视频生成超过 {max(1, round(deadline_seconds / 60))} 分钟，"
                "已停止，请重新生成。"
            )
            logger.warning(
                "share video job deadline exceeded job_id=%s timeout_seconds=%s",
                lease.job_id,
                deadline_seconds,
            )
            async with session_maker() as db:
                job = await db.scalar(
                    select(MemoirMaterialShareVideo).where(
                        _leased_share_video_job(lease)
                    )
                )
                if job is not None:
                    metadata = ShareVideoJobMetadata.from_json(job.metadata_json)
                    if metadata.background_status in {"pending", "planning", "generating"}:
                        metadata.background_status = "failed"
                        metadata.background_error_message = error_message
                    metadata.error_type = "job_deadline_exceeded"
                    metadata.timeout_seconds = deadline_seconds
                    metadata.elapsed_seconds = deadline_seconds
                    await _fail_share_video_job(
                        db,
                        lease,
                        target_path=share_video_file_path(job),
                        error=TimeoutError(error_message),
                        max_attempts=1,
                        retry_seconds=max(1, settings.memoir_share_video_retry_seconds),
                        metadata=metadata,
                    )
            return False
        if recovery_task in done:
            await recovery_task
            work_task.cancel()
            await asyncio.gather(work_task, return_exceptions=True)
            return await _recover_published_share_video(session_maker, lease)
        return await work_task
    except JobLeaseLostError:
        logger.warning("share video job lease lost job_id=%s", lease.job_id)
        return False
    except Exception:
        logger.exception("share video job worker failed job_id=%s", lease.job_id)
        return False
    finally:
        recovery_task.cancel()
        if not work_task.done():
            work_task.cancel()
        await asyncio.gather(recovery_task, work_task, return_exceptions=True)


async def run_due_share_video_jobs(
    session_maker: async_sessionmaker[AsyncSession] = async_session_maker,
    *,
    limit: int | None = None,
) -> int:
    async with session_maker() as db:
        leases = await claim_due_share_video_jobs(db, limit=limit)
    results = await asyncio.gather(
        *(run_share_video_job(lease, session_maker=session_maker) for lease in leases)
    )
    return sum(results)


async def share_video_worker_loop(stop_event: asyncio.Event) -> None:
    settings = get_settings()
    poll_seconds = max(1, settings.memoir_share_video_poll_seconds)
    while not stop_event.is_set():
        try:
            await run_due_share_video_jobs()
        except Exception:
            logger.exception("share video worker tick failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_seconds)
        except asyncio.TimeoutError:
            pass
