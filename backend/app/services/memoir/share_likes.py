"""Family likes on forwarded books, chapters and story videos."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.auth import WECHAT_PLACEHOLDER_DISPLAY_NAME
from app.models.memoir import MemoirBookOutput, MemoirBookPlanChapter, MemoirMaterialShareVideo, MemoirShareLike
from app.models.projects import Project
from app.models.users import User
from app.schemas.memoir_share_likes import (
    MemoirShareLikeActivityItem,
    MemoirShareLikeActivityResponse,
    MemoirShareLiker,
    MemoirShareLikesResponse,
    MemoirShareLikeSummary,
)
from app.services.memoir.access import load_memoir_project
from app.services.memoir.share_video_jobs import share_video_title
from app.services.memoir.share_video_metadata import ShareVideoJobMetadata

LIKER_LIMIT = 20
ACTIVITY_LIMIT = 20


@dataclass(frozen=True)
class ShareLikeTarget:
    project_id: str
    column: InstrumentedAttribute[str | None]
    target_id: str


def book_target(output: MemoirBookOutput) -> ShareLikeTarget:
    return ShareLikeTarget(output.project_id, MemoirShareLike.book_output_id, output.id)


def chapter_target(chapter: MemoirBookPlanChapter) -> ShareLikeTarget:
    return ShareLikeTarget(chapter.project_id, MemoirShareLike.chapter_id, chapter.id)


def video_target(job: MemoirMaterialShareVideo) -> ShareLikeTarget:
    return ShareLikeTarget(job.project_id, MemoirShareLike.share_video_id, job.id)


def _liker(user: User) -> MemoirShareLiker:
    name = user.display_name.strip()
    return MemoirShareLiker(
        display_name="" if name == WECHAT_PLACEHOLDER_DISPLAY_NAME else name,
        avatar_url=user.avatar_url or None,
    )


async def like_summary(db: AsyncSession, target: ShareLikeTarget) -> MemoirShareLikeSummary:
    count = await db.scalar(select(func.count()).select_from(MemoirShareLike).where(target.column == target.target_id))
    likers = await db.scalars(
        select(User)
        .join(MemoirShareLike, MemoirShareLike.user_id == User.id)
        .where(target.column == target.target_id)
        .order_by(MemoirShareLike.created_at.desc(), MemoirShareLike.id.desc())
        .limit(LIKER_LIMIT)
    )
    return MemoirShareLikeSummary(count=count or 0, likers=[_liker(user) for user in likers])


async def _project_owner_id(db: AsyncSession, target: ShareLikeTarget) -> str | None:
    return await db.scalar(select(Project.user_id).where(Project.id == target.project_id))


async def read_likes(db: AsyncSession, target: ShareLikeTarget, viewer_id: str | None) -> MemoirShareLikesResponse:
    summary = await like_summary(db, target)
    liked = viewer_id is not None and await db.scalar(select(MemoirShareLike.id).where(
        target.column == target.target_id, MemoirShareLike.user_id == viewer_id,
    )) is not None
    return MemoirShareLikesResponse(
        **summary.model_dump(),
        liked=liked,
        can_like=viewer_id is None or viewer_id != await _project_owner_id(db, target),
    )


async def set_like(db: AsyncSession, target: ShareLikeTarget, user_id: str, liked: bool) -> MemoirShareLikesResponse:
    if user_id == await _project_owner_id(db, target):
        raise HTTPException(status_code=403, detail="自己的故事不用点赞")
    if liked:
        await db.execute(insert(MemoirShareLike).values(
            project_id=target.project_id, user_id=user_id, **{target.column.key: target.target_id},
        ).on_conflict_do_nothing(index_elements=[MemoirShareLike.user_id, target.column]))
    else:
        await db.execute(delete(MemoirShareLike).where(
            target.column == target.target_id, MemoirShareLike.user_id == user_id,
        ))
    await db.commit()
    return await read_likes(db, target, user_id)


async def list_like_activity(
    db: AsyncSession, *, user_id: str, project_id: str, after: datetime | None,
) -> MemoirShareLikeActivityResponse:
    await load_memoir_project(db, project_id, user_id)
    rows = (await db.execute(
        select(MemoirShareLike, User, MemoirBookOutput, MemoirBookPlanChapter, MemoirMaterialShareVideo)
        .join(User, User.id == MemoirShareLike.user_id)
        .outerjoin(MemoirBookOutput, MemoirBookOutput.id == MemoirShareLike.book_output_id)
        .outerjoin(MemoirBookPlanChapter, MemoirBookPlanChapter.id == MemoirShareLike.chapter_id)
        .outerjoin(MemoirMaterialShareVideo, MemoirMaterialShareVideo.id == MemoirShareLike.share_video_id)
        .where(
            MemoirShareLike.project_id == project_id,
            *(() if after is None else (MemoirShareLike.created_at > after,)),
        )
        .order_by(MemoirShareLike.created_at.desc(), MemoirShareLike.id.desc())
        .limit(ACTIVITY_LIMIT)
    )).unique().all()
    items = []
    for like, user, output, chapter, video in rows:
        if output is not None:
            kind, title = "book", output.title
        elif chapter is not None:
            kind, title = "chapter", chapter.title
        else:
            kind = "video"
            title = share_video_title(ShareVideoJobMetadata.from_json(video.metadata_json), video.selected_text)
        items.append(MemoirShareLikeActivityItem(
            id=like.id, target_kind=kind, target_title=title, liker=_liker(user), created_at=like.created_at,
        ))
    return MemoirShareLikeActivityResponse(items=items)
