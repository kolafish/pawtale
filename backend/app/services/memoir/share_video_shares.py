"""Public playback of a share video opened from a forwarded card or its mini program code."""

from __future__ import annotations

import re

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import MemoirShareVideoJobStatus
from app.models.memoir import MemoirMaterialShareVideo
from app.schemas.memoir_voice import MemoirSharedVideoResponse
from app.services.memoir.book_shares import readable_share_token
from app.services.memoir.share_video_jobs import (
    CompletedShareVideo,
    completed_share_video_file,
    share_video_end_card_source,
)

SHARE_CODE_PATTERN = re.compile(r"[0-9a-f]{20}\Z")


async def _shared_job(db: AsyncSession, code: str) -> MemoirMaterialShareVideo:
    # Deleting the video is how the author takes a forwarded card back.
    job = await db.scalar(select(MemoirMaterialShareVideo).where(
        MemoirMaterialShareVideo.share_code == code,
        MemoirMaterialShareVideo.status == MemoirShareVideoJobStatus.SUCCEEDED.value,
    )) if SHARE_CODE_PATTERN.fullmatch(code) else None
    if job is None or job.output is None:
        raise HTTPException(status_code=404, detail="这段视频已被作者删除")
    return job


async def read_shared_video(db: AsyncSession, code: str) -> MemoirSharedVideoResponse:
    job = await _shared_job(db, code)
    title = completed_share_video_file(job).title
    book_title, narrator_name = await share_video_end_card_source(db, job.project_id)
    base_url = f"/api/mobile/memoir/share-video/shares/{code}/video"
    return MemoirSharedVideoResponse(
        title=title,
        book_title=book_title,
        narrator_name=narrator_name,
        duration_ms=max(0, job.end_ms - job.start_ms),
        video_url=base_url,
        poster_url=f"{base_url}?variant=thumbnail",
        book_share_token=await readable_share_token(db, job.project_id),
    )


async def load_shared_video_file(db: AsyncSession, code: str) -> CompletedShareVideo:
    return completed_share_video_file(await _shared_job(db, code))
