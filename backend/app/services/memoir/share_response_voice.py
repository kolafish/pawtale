"""Visitor-scoped dictation uses the shared gateway and recording buffer."""
from __future__ import annotations

from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow_naive
from app.database import async_session_maker
from app.models.memoir import MemoirExcerptShare, MemoirShareResponse, MemoirShareResponseVoice
from app.models.memoir_common import AudioFile
from app.models.projects import Project
from app.models.users import User
from app.schemas.memoir_share_likes import MemoirShareLikeTarget
from app.services.memoir import audio_files, book_shares, share_video_shares, share_responses
from app.services.memoir.voice_gateway_persistence import PendingAudioFileBuffer
from app.services.asr.gateway import VoiceGatewayClientError
from app.services.asr.streaming import StreamingASREvent


async def new_capture(db: AsyncSession, user_id: str, target: MemoirShareLikeTarget) -> MemoirShareResponseVoice:
    resolved = await share_responses.response_target(db, target)
    capture = MemoirShareResponseVoice(project_id=resolved.project_id, user_id=user_id,
        target_kind=resolved.kind, target_id=resolved.id, share_key=resolved.share_key,
        expires_at=utcnow_naive() + timedelta(minutes=10))
    db.add(capture)
    await db.commit()
    return capture


async def valid_capture(db: AsyncSession, capture_id: str, user_id: str) -> MemoirShareResponseVoice:
    capture = await db.get(MemoirShareResponseVoice, capture_id, populate_existing=True)
    if capture is None or capture.user_id != user_id or capture.expires_at <= utcnow_naive():
        raise HTTPException(status_code=404, detail="录音已失效，请重新录制")
    if capture.audio_file_id:
        raise HTTPException(status_code=409, detail="这段录音已完成")
    if capture.target_kind == "excerpt":
        excerpt = await db.get(MemoirExcerptShare, capture.target_id, populate_existing=True)
        if not excerpt or excerpt.token_hash != capture.share_key:
            raise HTTPException(404, "选文分享已停止")
    elif capture.target_kind == "video":
        await share_video_shares.validate_share_key(db, capture.target_id, capture.share_key)
    else:
        await book_shares.validate_share_key(db, capture.target_kind, capture.target_id, capture.share_key)
    return capture


async def active_capture_ticket(db: AsyncSession, capture_id: str, user_id: str) -> bool:
    """Check only ticket and publication state before forwarding each audio frame."""
    capture = await db.get(MemoirShareResponseVoice, capture_id)
    if (capture is None or capture.user_id != user_id or capture.audio_file_id
            or capture.expires_at <= utcnow_naive()):
        return False
    if capture.target_kind == "excerpt":
        key = await db.scalar(select(MemoirExcerptShare.token_hash).where(MemoirExcerptShare.id == capture.target_id))
    elif capture.target_kind == "video":
        key = await share_video_shares.active_share_key(db, capture.target_id)
    else:
        key = await book_shares.active_share_key(db, capture.target_kind, capture.target_id)
    return key is not None and key == capture.share_key


class ResponseCapturePersistence:
    def __init__(self, *, capture_id: str, user_id: str):
        self.capture_id = capture_id
        self.user_id = user_id
        self.pending = PendingAudioFileBuffer(user_id=user_id, voice_session_id=capture_id)

    async def authorized(self) -> bool:
        async with async_session_maker() as db:
            return await active_capture_ticket(db, self.capture_id, self.user_id)

    async def record_audio_frame(self, audio: bytes) -> None:
        # VoiceGatewaySession authorizes each audio.frame before invoking this callback.
        await self.pending.append(audio)

    async def persist_final(self, event: StreamingASREvent) -> dict:
        raw = (event.text or "").strip()
        if not raw:
            raise VoiceGatewayClientError("asr_no_text")
        staged = None
        try:
            async with async_session_maker() as db:
                preliminary = await db.get(MemoirShareResponseVoice, self.capture_id)
                if preliminary is None or preliminary.user_id != self.user_id:
                    raise VoiceGatewayClientError("voice_capture_unavailable")
                user_id = await db.scalar(select(User.id).where(
                    User.id == self.user_id,
                ).with_for_update(read=True, key_share=True))
                if user_id is None:
                    raise VoiceGatewayClientError("voice_capture_unavailable")
                # Project deletion takes this lock before collecting audio files.
                # A completed recording is therefore visible to its cleanup pass.
                project_id = await db.scalar(select(Project.id).where(
                    Project.id == preliminary.project_id,
                ).with_for_update(read=True))
                if project_id is None:
                    raise VoiceGatewayClientError("voice_capture_unavailable")
                capture = await db.scalar(select(MemoirShareResponseVoice).where(
                    MemoirShareResponseVoice.id == self.capture_id,
                ).with_for_update().execution_options(populate_existing=True))
                if capture is None or capture.audio_file_id:
                    raise VoiceGatewayClientError("voice_capture_unavailable")
                await valid_capture(db, self.capture_id, self.user_id)
                staged = await self.pending.stage(db)
                if staged is None:
                    raise VoiceGatewayClientError("audio_missing")
                await db.flush()
                capture.audio_file_id = staged.audio_file.id
                capture.asr_text = raw
                capture.provider_payload_json = dict(event.raw or {})
                capture.provider = "volc_streaming_asr"
                capture.start_ms = max(0, event.start_ms or 0)
                capture.end_ms = max(capture.start_ms, event.end_ms or int(self.pending.size_bytes / 32))
                await db.commit()
            await self.pending.mark_committed()
            return {"voice_capture_id": self.capture_id}
        except Exception:
            await audio_files.discard_staged_audio(staged)
            raise

    async def finish_audio(self, _speech_detected: bool | None) -> None:
        await self.pending.discard()

    async def stop(self, _error: str | None, _reason: str) -> None:
        await self.pending.discard()


async def capture_audio(db: AsyncSession, response_id: str, user_id: str) -> AudioFile:
    response = await share_responses.private_response(db, response_id, user_id)
    capture = await db.get(MemoirShareResponseVoice, response.voice_capture_id) if response.voice_capture_id else None
    audio = await db.get(AudioFile, capture.audio_file_id) if capture and capture.audio_file_id else None
    if audio is None:
        raise HTTPException(status_code=404, detail="这条回应没有原音")
    return audio


async def cleanup_expired_unsubmitted_captures(db: AsyncSession) -> int:
    """Remove abandoned recordings a day after the ten-minute capture ticket expires."""
    cutoff = utcnow_naive() - timedelta(days=1)
    submitted = exists(select(MemoirShareResponse.id).where(
        MemoirShareResponse.voice_capture_id == MemoirShareResponseVoice.id,
    ))
    captures = list(await db.scalars(
        select(MemoirShareResponseVoice).where(
            MemoirShareResponseVoice.expires_at < cutoff, ~submitted,
        ).order_by(MemoirShareResponseVoice.expires_at, MemoirShareResponseVoice.id)
        .limit(100).with_for_update(skip_locked=True)
    ))
    if not captures:
        return 0
    audio_ids = [capture.audio_file_id for capture in captures if capture.audio_file_id]
    audio_rows = list(await db.scalars(select(AudioFile).where(AudioFile.id.in_(audio_ids)))) if audio_ids else []
    await db.execute(delete(MemoirShareResponseVoice).where(
        MemoirShareResponseVoice.id.in_([capture.id for capture in captures]),
    ))
    if audio_ids:
        await db.execute(delete(AudioFile).where(AudioFile.id.in_(audio_ids)))
    await db.commit()
    await audio_files.delete_audio_file_paths(audio_rows)
    return len(captures)
