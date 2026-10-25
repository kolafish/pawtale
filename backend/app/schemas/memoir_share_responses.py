from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from app.schemas.memoir_share_likes import MemoirShareLikeTarget


class ShareResponseCreate(MemoirShareLikeTarget):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: str = Field(min_length=1, max_length=80)
    content: str = Field(min_length=1, max_length=2000)


class ShareResponseItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    target_kind: Literal["book", "chapter", "story", "video"]
    target_title: str
    display_name: str
    content: str
    created_at: datetime
    read_at: datetime | None
    adopted_at: datetime | None
    adopted_utterance_id: str | None


class ShareResponseList(BaseModel):
    items: list[ShareResponseItem]
    next_before_id: str | None = None
    unread_count: int
