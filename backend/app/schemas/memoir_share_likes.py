from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class MemoirShareLikeTarget(BaseModel):
    """The forwarded reading link or story video card a like belongs to."""

    reading_token: str | None = Field(default=None, max_length=200)
    video_code: str | None = Field(default=None, max_length=40)

    @model_validator(mode="after")
    def _one_target(self) -> MemoirShareLikeTarget:
        if (self.reading_token is None) == (self.video_code is None):
            raise ValueError("reading_token 和 video_code 必须二选一")
        return self


class MemoirShareLikeUpdateRequest(MemoirShareLikeTarget):
    liked: bool


class MemoirShareLiker(BaseModel):
    # Empty while the liker keeps WeChat's placeholder name.
    display_name: str
    avatar_url: str | None = None


class MemoirShareLikeSummary(BaseModel):
    count: int = 0
    # Newest first, capped for the avatar row.
    likers: list[MemoirShareLiker] = Field(default_factory=list)


class MemoirShareLikesResponse(MemoirShareLikeSummary):
    liked: bool = False
    # The author sees who liked their story but does not like it themselves.
    can_like: bool = True
    # The viewer liked without leaving a name, so the author cannot tell who they are.
    profile_missing: bool = False


class MemoirShareLikeActivityItem(BaseModel):
    id: str
    target_kind: Literal["book", "chapter", "story", "video"]
    target_title: str
    liker: MemoirShareLiker
    created_at: datetime


class MemoirShareLikeActivityResponse(BaseModel):
    items: list[MemoirShareLikeActivityItem]
