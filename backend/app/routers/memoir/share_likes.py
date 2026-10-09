from datetime import datetime

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, get_optional_user_id
from app.database import get_db
from app.models.users import User
from app.schemas.memoir_share_likes import (
    MemoirShareLikeActivityResponse,
    MemoirShareLikesResponse,
    MemoirShareLikeTarget,
    MemoirShareLikeUpdateRequest,
)
from app.services.memoir import book_shares as memoir_book_shares
from app.services.memoir import share_likes as memoir_share_likes
from app.services.memoir import share_video_shares as memoir_share_video_shares


router = APIRouter()


async def _like_target(db: AsyncSession, req: MemoirShareLikeTarget) -> memoir_share_likes.ShareLikeTarget:
    if req.reading_token is not None:
        return await memoir_book_shares.reading_like_target(db, req.reading_token)
    return await memoir_share_video_shares.video_like_target(db, req.video_code or "")


@router.post("/share-likes/read", response_model=MemoirShareLikesResponse)
async def read_mobile_memoir_share_likes(
    req: MemoirShareLikeTarget,
    response: Response,
    db: AsyncSession = Depends(get_db),
    viewer_id: str | None = Depends(get_optional_user_id),
):
    response.headers["Cache-Control"] = "no-store"
    return await memoir_share_likes.read_likes(db, await _like_target(db, req), viewer_id)


@router.put("/share-likes", response_model=MemoirShareLikesResponse)
async def update_mobile_memoir_share_like(
    req: MemoirShareLikeUpdateRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    response.headers["Cache-Control"] = "no-store"
    return await memoir_share_likes.set_like(db, await _like_target(db, req), current_user.id, req.liked)


@router.get("/projects/{project_id}/share-likes", response_model=MemoirShareLikeActivityResponse)
async def list_mobile_memoir_share_likes(
    project_id: str,
    response: Response,
    after: datetime | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    response.headers["Cache-Control"] = "no-store"
    return await memoir_share_likes.list_like_activity(db, user_id=current_user.id, project_id=project_id, after=after)
