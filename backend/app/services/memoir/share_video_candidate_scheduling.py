"""Small queue and invalidation hooks shared by transcript write paths."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow_naive
from app.domain.memoir.types import (
    MemoirEditingJobType,
    MemoirShareVideoCandidateStatus,
    MemoirVoiceSessionStatus,
)
from app.models.memoir import MemoirShareVideoCandidate, MemoirVoiceSession
from app.services.memoir import editing_jobs


async def invalidate_share_video_candidate(
    db: AsyncSession,
    *,
    utterance_id: str,
) -> None:
    await db.execute(
        update(MemoirShareVideoCandidate)
        .where(
            MemoirShareVideoCandidate.utterance_id == utterance_id,
            MemoirShareVideoCandidate.status.in_((
                MemoirShareVideoCandidateStatus.READY.value,
                MemoirShareVideoCandidateStatus.ACCEPTED.value,
            )),
        )
        .values(status=MemoirShareVideoCandidateStatus.STALE.value)
    )


async def schedule_share_video_candidate_evaluation(
    db: AsyncSession,
    *,
    project_id: str,
    voice_session_id: str,
) -> None:
    session_status = await db.scalar(
        select(MemoirVoiceSession.status).where(
            MemoirVoiceSession.id == voice_session_id,
            MemoirVoiceSession.project_id == project_id,
        )
    )
    if session_status != MemoirVoiceSessionStatus.STOPPED.value:
        return
    candidate_status = await db.scalar(
        select(MemoirShareVideoCandidate.status).where(
            MemoirShareVideoCandidate.voice_session_id == voice_session_id,
        )
    )
    if candidate_status == MemoirShareVideoCandidateStatus.DISMISSED.value:
        return
    await editing_jobs.enqueue_editing_job(
        db,
        project_id=project_id,
        job_type=MemoirEditingJobType.EVALUATE_SHARE_VIDEO_CANDIDATE,
        payload_json={"voice_session_id": voice_session_id},
        run_after=utcnow_naive() + timedelta(seconds=3),
        dedupe_key=f"share_video_candidate:{voice_session_id}",
        replace_completed=True,
        reschedule_running=True,
    )
