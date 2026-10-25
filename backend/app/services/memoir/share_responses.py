"""Private responses reuse share authorization and the existing material pipeline."""
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import func, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import WECHAT_PLACEHOLDER_DISPLAY_NAME
from app.clock import utcnow_naive
from app.models.memoir import (
    MemoirShareResponse, MemoirVoiceSession, MemoirVoiceUtterance,
    MemoirTranscriptVersion, MemoirChatMessage,
)
from app.models.projects import Project
from app.models.users import User
from app.schemas.memoir_share_likes import MemoirShareLikeTarget
from app.schemas.memoir_share_responses import ShareResponseCreate, ShareResponseItem, ShareResponseList
from app.services.memoir import book_shares, share_video_shares
from app.services.memoir.access import load_memoir_project
from app.services.memoir.share_video_jobs import completed_share_video_file
from app.services.memoir.utterance_editing_pipeline import schedule_ready_utterance_background_work


@dataclass(frozen=True)
class ResponseTarget:
    project_id: str
    kind: str
    id: str
    title: str


async def response_target(db: AsyncSession, req: MemoirShareLikeTarget) -> ResponseTarget:
    # Serialize validation with stopping a share. Re-read after waiting on the lock.
    if req.reading_token is not None:
        resource, _ = await book_shares.readable_share(db, req.reading_token)
        locked = await db.scalar(select(type(resource)).where(type(resource).id == resource.id)
            .with_for_update().execution_options(populate_existing=True))
        if locked is None:
            raise HTTPException(status_code=404, detail="分享内容已删除")
        resource, version = await book_shares.readable_share(db, req.reading_token)
        projection = await book_shares._resource_reading_response(db, resource, version, presentation=False)
        return ResponseTarget(resource.project_id, book_shares._scope(resource), resource.id, projection.title)
    job = await share_video_shares._shared_job(db, req.video_code or "")
    locked = await db.scalar(select(type(job).id).where(type(job).id == job.id).with_for_update())
    if locked is None:
        raise HTTPException(status_code=404, detail="分享视频已删除")
    job = await share_video_shares._shared_job(db, req.video_code or "")
    return ResponseTarget(job.project_id, "video", job.id, completed_share_video_file(job).title)


async def create_response(db: AsyncSession, user: User, req: ShareResponseCreate) -> ShareResponseItem:
    target = await response_target(db, req)
    name = user.display_name.strip()
    if not name or name == WECHAT_PLACEHOLDER_DISPLAY_NAME:
        name = "家人"
    await db.execute(insert(MemoirShareResponse).values(
        project_id=target.project_id, user_id=user.id, request_id=req.request_id,
        target_kind=target.kind, target_id=target.id, target_title=target.title,
        display_name=name, content=req.content,
    ).on_conflict_do_nothing(index_elements=["user_id", "request_id"]))
    row = await db.scalar(select(MemoirShareResponse).where(
        MemoirShareResponse.user_id == user.id, MemoirShareResponse.request_id == req.request_id,
    ))
    if row.content != req.content or row.target_id != target.id or row.target_kind != target.kind:
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
    row = await private_response(db, response_id, user_id, owner_only=True, lock=True)
    if row.adopted_at is None:
        # Existing materials require a session; use a stopped human-source session,
        # never fabricate an ASR result or treat a relative as the narrator.
        session = MemoirVoiceSession(project_id=row.project_id, status="stopped", interview_type="human_interview", guidance_mode="free_narration")
        db.add(session)
        await db.flush()
        text = f"{row.display_name}对《{row.target_title}》的补充：\n{row.content}"
        utterance = MemoirVoiceUtterance(voice_session_id=session.id, transcript_raw=text, transcript_edited=text)
        db.add(utterance)
        await db.flush()
        version = MemoirTranscriptVersion(voice_session_id=session.id, source_artifact_id=utterance.id,
            version_type="user_edited", content=text, created_by="user")
        db.add(version)
        await db.flush()
        message = MemoirChatMessage(project_id=row.project_id, voice_session_id=session.id, role="user",
            content_text=text, content_status="corrected", transcript_version_id=version.id,
            metadata_json={"share_response_id": row.id})
        db.add(message)
        await db.flush()
        utterance.chat_message = message
        await db.flush()
        await schedule_ready_utterance_background_work(db, project_id=row.project_id, utterance=utterance, source="share_response")
        row.adopted_utterance_id = utterance.id
        row.adopted_at = utcnow_naive()
        row.read_at = row.read_at or row.adopted_at
    await db.commit()
    return ShareResponseItem.model_validate(row)
