"""Private responses reuse share authorization and the existing material pipeline."""
from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException
from sqlalchemy import func, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import WECHAT_PLACEHOLDER_DISPLAY_NAME
from app.clock import utcnow_naive
from app.models.memoir import (
    MemoirShareResponse, MemoirShareResponseVoice, MemoirVoiceSession, MemoirVoiceUtterance,
    MemoirTranscriptVersion, MemoirChatMessage,
)
from app.models.projects import Project
from app.models.users import User
from app.schemas.memoir_share_likes import MemoirShareLikeTarget
from app.schemas.memoir_share_responses import ShareResponseCreate, ShareResponseItem, ShareResponseList
from app.services.memoir import audio_files, book_shares, share_video_shares
from app.models.memoir_common import AudioFile
from app.services.memoir.access import load_memoir_project
from app.services.memoir.share_video_jobs import completed_share_video_file
from app.services.memoir.utterance_editing_pipeline import schedule_ready_utterance_background_work


@dataclass(frozen=True)
class ResponseTarget:
    project_id: str
    kind: Literal["book", "chapter", "story", "video"]
    id: str
    title: str
    share_key: str


async def response_target(db: AsyncSession, req: MemoirShareLikeTarget) -> ResponseTarget:
    # Serialize validation with stopping a share. Re-read after waiting on the lock.
    if req.reading_token is not None:
        resource, _ = await book_shares.readable_share(db, req.reading_token)
        locked = await db.scalar(select(type(resource)).where(type(resource).id == resource.id)
            .with_for_update().execution_options(populate_existing=True))
        if locked is None:
            raise HTTPException(status_code=404, detail="分享内容已删除")
        resource, version = await book_shares.readable_share(db, req.reading_token)
        scope = book_shares.share_scope(resource)
        key = await book_shares.active_share_key(db, scope, resource.id)
        if key is None:
            raise HTTPException(status_code=404, detail="阅读分享已停止")
        return ResponseTarget(resource.project_id, scope, resource.id,
            book_shares.reading_share_title(resource, version), key)
    job = await share_video_shares.shared_job(db, req.video_code or "")
    locked = await db.scalar(select(type(job).id).where(type(job).id == job.id).with_for_update())
    if locked is None:
        raise HTTPException(status_code=404, detail="分享视频已删除")
    job = await share_video_shares.shared_job(db, req.video_code or "")
    key = await share_video_shares.active_share_key(db, job.id)
    if key is None:
        raise HTTPException(status_code=404, detail="视频分享已停止")
    return ResponseTarget(job.project_id, "video", job.id, completed_share_video_file(job).title, key)


async def create_response(db: AsyncSession, user: User, req: ShareResponseCreate) -> ShareResponseItem:
    target = await response_target(db, req)
    name = user.display_name.strip()
    if not name or name == WECHAT_PLACEHOLDER_DISPLAY_NAME:
        name = "家人"
    if req.voice_capture_id:
        capture = await db.scalar(select(MemoirShareResponseVoice).where(
            MemoirShareResponseVoice.id == req.voice_capture_id,
        ).with_for_update())
        if (capture is None or capture.user_id != user.id or capture.project_id != target.project_id
                or capture.target_kind != target.kind or capture.target_id != target.id or not capture.audio_file_id):
            raise HTTPException(status_code=410, detail="原音已过期或未完成，请重新录制")
        if capture.share_key != target.share_key:
            raise HTTPException(status_code=410, detail="原音对应的分享已更新，请重新录制")
        used = await db.scalar(select(MemoirShareResponse).where(
            MemoirShareResponse.voice_capture_id == capture.id,
        ))
        if used is not None and (used.user_id != user.id or used.request_id != req.request_id):
            raise HTTPException(status_code=409, detail="这段录音已经提交")
    await db.execute(insert(MemoirShareResponse).values(
        project_id=target.project_id, user_id=user.id, request_id=req.request_id,
        target_kind=target.kind, target_id=target.id, target_title=target.title,
        display_name=name, content=req.content, voice_capture_id=req.voice_capture_id,
    ).on_conflict_do_nothing(index_elements=["user_id", "request_id"]))
    row = await db.scalar(select(MemoirShareResponse).where(
        MemoirShareResponse.user_id == user.id, MemoirShareResponse.request_id == req.request_id,
    ))
    if (row.content != req.content or row.target_id != target.id or row.target_kind != target.kind
            or row.voice_capture_id != req.voice_capture_id):
        raise HTTPException(status_code=409, detail="这次回应已提交，请勿重复修改")
    await db.commit()
    return ShareResponseItem.model_validate(row)


async def private_response(db: AsyncSession, response_id: str, user_id: str, *, owner_only: bool = False, lock: bool = False) -> MemoirShareResponse:
    query = select(MemoirShareResponse).where(MemoirShareResponse.id == response_id)
    if lock:
        query = query.with_for_update()
    row = await db.scalar(query.execution_options(populate_existing=True))
    if row is None:
        raise HTTPException(status_code=404, detail="回应不存在")
    owner_id = await db.scalar(select(Project.user_id).where(Project.id == row.project_id))
    if user_id != owner_id and (owner_only or user_id != row.user_id):
        raise HTTPException(status_code=404, detail="回应不存在")
    return row


async def list_responses(db: AsyncSession, project_id: str, user_id: str, before_id: str | None) -> ShareResponseList:
    await load_memoir_project(db, project_id, user_id)
    query = select(MemoirShareResponse).where(MemoirShareResponse.project_id == project_id)
    if before_id:
        cursor = await private_response(db, before_id, user_id, owner_only=True)
        if cursor.project_id != project_id:
            raise HTTPException(status_code=404, detail="回应不存在")
        query = query.where(tuple_(MemoirShareResponse.created_at, MemoirShareResponse.id) < (cursor.created_at, cursor.id))
    rows = list(await db.scalars(query.order_by(MemoirShareResponse.created_at.desc(), MemoirShareResponse.id.desc()).limit(21)))
    unread = await db.scalar(select(func.count()).select_from(MemoirShareResponse).where(
        MemoirShareResponse.project_id == project_id, MemoirShareResponse.read_at.is_(None),
    ))
    return ShareResponseList(items=[ShareResponseItem.model_validate(row) for row in rows[:20]],
        next_before_id=rows[19].id if len(rows) > 20 else None, unread_count=unread or 0)


async def mark_read(db: AsyncSession, response_id: str, user_id: str) -> ShareResponseItem:
    row = await private_response(db, response_id, user_id, owner_only=True, lock=True)
    row.read_at = row.read_at or utcnow_naive()
    await db.commit()
    return ShareResponseItem.model_validate(row)


async def adopt_response(db: AsyncSession, response_id: str, user_id: str) -> ShareResponseItem:
    preliminary = await private_response(db, response_id, user_id, owner_only=True)
    # Account deletion locks User before Project; audio staging needs the same order.
    current_user_id = await db.scalar(select(User.id).where(
        User.id == user_id,
    ).with_for_update(read=True, key_share=True))
    if current_user_id is None:
        raise HTTPException(status_code=404, detail="回应不存在")
    project_id = await db.scalar(select(Project.id).where(
        Project.id == preliminary.project_id,
    ).with_for_update(read=True))
    if project_id is None:
        raise HTTPException(status_code=404, detail="回应不存在")
    row = await private_response(db, response_id, user_id, owner_only=True, lock=True)
    if row.adopted_at is None:
        capture = await db.get(MemoirShareResponseVoice, row.voice_capture_id) if row.voice_capture_id else None
        original_audio = await db.get(AudioFile, capture.audio_file_id) if capture and capture.audio_file_id else None
        staged = None
        if capture and original_audio:
            staged = await audio_files.stage_audio_path(db, user_id,
                audio_files.audio_file_path(original_audio), mime_type=original_audio.mime_type, extension=".pcm")
        try:
            await _adopt_material(db, row, capture, staged)
            await db.commit()
        except Exception:
            await db.rollback()
            await audio_files.discard_staged_audio(staged)
            raise
    return ShareResponseItem.model_validate(row)


async def _adopt_material(db: AsyncSession, row: MemoirShareResponse,
                          capture: MemoirShareResponseVoice | None, staged: audio_files.StagedAudioFile | None) -> None:
    # Existing materials require a session; use a stopped human-source session,
    # never fabricate an ASR result or treat a relative as the narrator.
    session = MemoirVoiceSession(project_id=row.project_id, status="stopped", interview_type="human_interview", guidance_mode="free_narration")
    db.add(session)
    await db.flush()
    text = f"{row.display_name}对《{row.target_title}》的补充：\n{row.content}"
    utterance = MemoirVoiceUtterance(voice_session_id=session.id,
        audio_file_id=staged.audio_file.id if staged else None,
        transcript_raw=capture.asr_text if capture else text, transcript_edited=text,
        start_ms=capture.start_ms if capture else None, end_ms=capture.end_ms if capture else None,
        provider=capture.provider if capture else None,
        provider_payload_json={"share_response_id": row.id,
            "voice_capture_id": capture.id if capture else None,
            "original_audio_file_id": capture.audio_file_id if capture else None,
            "asr_provider_payload": capture.provider_payload_json if capture else {}})
    db.add(utterance)
    await db.flush()
    if capture:
        raw_version = MemoirTranscriptVersion(voice_session_id=session.id, source_artifact_id=utterance.id,
            version_type="asr_raw", content=capture.asr_text, provider=capture.provider, created_by="asr")
        db.add(raw_version)
        await db.flush()
        utterance.provider_payload_json = {
            **utterance.provider_payload_json, "asr_transcript_version_id": raw_version.id,
        }
    version = MemoirTranscriptVersion(voice_session_id=session.id, source_artifact_id=utterance.id,
        version_type="user_edited", content=text, created_by="user")
    db.add(version)
    await db.flush()
    message = MemoirChatMessage(project_id=row.project_id, voice_session_id=session.id, role="user",
        content_text=text, content_status="corrected", transcript_version_id=version.id,
        metadata_json={"share_response_id": row.id,
            "asr_transcript_version_id": raw_version.id if capture else None})
    db.add(message)
    await db.flush()
    utterance.chat_message = message
    await db.flush()
    await schedule_ready_utterance_background_work(db, project_id=row.project_id, utterance=utterance, source="share_response")
    row.adopted_utterance_id = utterance.id
    row.adopted_at = utcnow_naive()
    row.read_at = row.read_at or row.adopted_at
