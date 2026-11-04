from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from app.schemas.memoir_share_likes import MemoirShareLikeTarget


class ShareResponseCreate(MemoirShareLikeTarget):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    can_feature: bool = False
    request_id: str = Field(min_length=1, max_length=80)
    content: str = Field(min_length=1, max_length=2000)
    voice_capture_id: str | None = Field(default=None, max_length=36)


class ShareResponseVoiceTicket(BaseModel):
    capture_id: str


class ShareResponseItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    can_feature: bool
    featured_at: datetime | None
    featured_chapter_id: str | None
    target_kind: Literal["book", "chapter", "story", "video", "excerpt"]
    target_title: str
    display_name: str
    content: str
    voice_capture_id: str | None = None
    created_at: datetime
    read_at: datetime | None
    adopted_at: datetime | None
    adopted_utterance_id: str | None


class FeatureResponseRequest(BaseModel):
    featured: bool
    chapter_id: str | None = None


class FeaturedFamilyNote(BaseModel):
    display_name: str
    content: str


class ShareResponseList(BaseModel):
    items: list[ShareResponseItem]
    next_before_id: str | None = None
    unread_count: int
