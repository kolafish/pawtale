from pydantic import BaseModel, Field
from typing import Literal

from app.schemas.memoir_book import (
    MemoirBookChapterDraftResponse,
    MemoirBookOutputResponse,
    MemoirBookPlanResponse,
)
from app.schemas.memoir_images import MemoirImageAssetListResponse
from app.schemas.memoir_projects import MemoirIdentityResponse
from app.schemas.memoir_voice import (
    MemoirAutomationEventListResponse,
    MemoirShareVideoCountResponse,
    MemoirVoiceTranscriptItemResponse,
)


class MemoirHomeSnapshotResponse(BaseModel):
    server_revision: int = Field(ge=0)
    identity: MemoirIdentityResponse
    latest_transcript: MemoirVoiceTranscriptItemResponse | None = None


class MemoirBookSnapshotResponse(BaseModel):
    server_revision: int = Field(ge=0)
    identity: MemoirIdentityResponse
    plan: MemoirBookPlanResponse | None = None
    drafts_by_chapter_id: dict[
        str,
        MemoirBookChapterDraftResponse | None,
    ] = Field(default_factory=dict)
    output: MemoirBookOutputResponse | None = None
    automation_events: MemoirAutomationEventListResponse
    image_assets: MemoirImageAssetListResponse
    share_video_counts: list[MemoirShareVideoCountResponse] = Field(default_factory=list)


class MemoirDraftProgressResponse(BaseModel):
    project_id: str
    status: Literal["idle", "processing", "ready", "partial_ready", "needs_input", "no_change", "failed"] = "idle"
    stage: str = "idle"
    title: str = "讲下回忆后会自动整理成稿"
    detail: str = ""
    can_retry: bool = False
    readable_chapter_count: int = 0
    total_chapter_count: int = 0
    material_count: int = 0
    pending_material_count: int = 0
    needs_user_material_count: int = 0
    review_status: str = "not_started"
    review_error: str = ""


class MemoirStorySectionResponse(BaseModel):
    id: str
    title: str
    material_count: int = 0
    image_count: int = 0
    video_count: int = 0


class MemoirStoryChapterResponse(BaseModel):
    id: str
    title: str
    include_in_book: bool
    material_count: int = 0
    image_count: int = 0
    video_count: int = 0
    char_count: int = 0
    readable: bool = False
    state: Literal["settled", "attention", "draft", "muted"] = "muted"
    state_text: str = "待整理"
    sections: list[MemoirStorySectionResponse] = Field(default_factory=list)


class MemoirStorySnapshotResponse(BaseModel):
    server_revision: int
    identity: MemoirIdentityResponse
    progress: MemoirDraftProgressResponse | None = None
    chapters: list[MemoirStoryChapterResponse] = Field(default_factory=list)
    book_readable: bool = False
    book_char_count: int = 0
    updated_at: str = ""
    unassigned_image_count: int = 0
    unassigned_video_count: int = 0
    automation_events: MemoirAutomationEventListResponse
