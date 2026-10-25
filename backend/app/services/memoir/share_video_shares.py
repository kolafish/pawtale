"""Public playback of a share video opened from a forwarded card or its mini program code."""

from __future__ import annotations

import re
import secrets

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import session_token_hash
from app.domain.memoir.types import MemoirShareVideoJobStatus
from app.models.memoir import MemoirMaterialShareVideo
from app.schemas.memoir_voice import MemoirSharedVideoResponse, MemoirShareVideoJobResponse
from app.services.memoir.share_likes import ShareLikeTarget, video_target
from app.services.memoir.share_video_jobs import (
    CompletedShareVideo,
    _load_owned_job, _job_response, _share_video_mini_program_code, rendering,
    completed_share_video_file,
    share_video_end_card_source,
)

SHARE_CODE_PATTERN = re.compile(r"[0-9a-f]{20}\Z")


async def shared_job(db: AsyncSession, code: str) -> MemoirMaterialShareVideo:
    # Every public entry, including images and byte ranges, checks publication.
    job = await db.scalar(select(MemoirMaterialShareVideo).where(
        MemoirMaterialShareVideo.share_code == code,
        MemoirMaterialShareVideo.share_enabled.is_(True),
        MemoirMaterialShareVideo.status == MemoirShareVideoJobStatus.SUCCEEDED.value,
    )) if SHARE_CODE_PATTERN.fullmatch(code) else None
    if job is None or job.output is None:
        raise HTTPException(status_code=404, detail="作者已停止分享或删除这段视频")
    return job


async def active_share_key(db: AsyncSession, job_id: str) -> str | None:
    code = await db.scalar(select(MemoirMaterialShareVideo.share_code).where(
        MemoirMaterialShareVideo.id == job_id,
        MemoirMaterialShareVideo.share_enabled.is_(True),
        MemoirMaterialShareVideo.status == MemoirShareVideoJobStatus.SUCCEEDED.value,
    ))
    return session_token_hash(code) if code is not None else None


async def validate_share_key(db: AsyncSession, job_id: str, key: str) -> None:
    job = await db.get(MemoirMaterialShareVideo, job_id, populate_existing=True)
    if job is None or session_token_hash(job.share_code) != key:
        raise HTTPException(status_code=404, detail="视频分享已停止")
    await shared_job(db, job.share_code)


async def read_shared_video(db: AsyncSession, code: str) -> MemoirSharedVideoResponse:
    job = await shared_job(db, code)
    return await _video_response(db, job, public=True)


async def preview_video(db: AsyncSession, job_id: str, user_id: str) -> MemoirSharedVideoResponse:
    return await _video_response(db, await _load_owned_job(db, job_id, user_id), public=False)


async def _video_response(db: AsyncSession, job: MemoirMaterialShareVideo, *, public: bool) -> MemoirSharedVideoResponse:
    title = completed_share_video_file(job).title
    book_title, narrator_name = await share_video_end_card_source(db, job.project_id)
    base_url = f"/api/mobile/memoir/share-video/shares/{job.share_code}/video" if public else f"/api/mobile/memoir/share-video/jobs/{job.id}/download"
    return MemoirSharedVideoResponse(
        title=title,
        book_title=book_title,
        narrator_name=narrator_name,
        duration_ms=max(0, job.end_ms - job.start_ms),
        video_url=base_url,
        poster_url=f"{base_url}?variant=poster",
        end_frame_url=f"{base_url}?variant=end",
    )


async def video_like_target(db: AsyncSession, code: str) -> ShareLikeTarget:
    return video_target(await shared_job(db, code))


async def load_shared_video_file(db: AsyncSession, code: str) -> CompletedShareVideo:
    return completed_share_video_file(await shared_job(db, code))


async def set_video_share(
    db: AsyncSession, job_id: str, user_id: str, *, enabled: bool,
) -> MemoirShareVideoJobResponse:
    job = await _load_owned_job(db, job_id, user_id)
    # Lock the job, not its joined optional output, through publication/revocation.
    job = await db.scalar(select(MemoirMaterialShareVideo).where(
        MemoirMaterialShareVideo.id == job.id,
    ).with_for_update(of=MemoirMaterialShareVideo).execution_options(populate_existing=True))
    if job.status != MemoirShareVideoJobStatus.SUCCEEDED.value:
        raise HTTPException(status_code=409, detail="请先完成视频制作")
    if enabled and not job.share_enabled:
        video = completed_share_video_file(job)
        if job.rendered_share_code != job.share_code:
            code_image = await _share_video_mini_program_code(job.share_code)
            book_title, narrator = await share_video_end_card_source(db, job.project_id)
            await rendering.replace_share_video_end_card(
                video.path, book_title=book_title, narrator_name=narrator,
                mini_program_code=code_image, body_seconds=(job.end_ms - job.start_ms) / 1000,
                hook_text=video.hook_text,
            )
            job.output.size_bytes = video.path.stat().st_size
            job.rendered_share_code = job.share_code
        job.share_enabled = True
    elif not enabled and job.share_enabled:
        job.share_enabled = False
        job.share_code = secrets.token_hex(10)
    await db.commit()
    return _job_response(job)
