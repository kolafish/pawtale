from pydantic import BaseModel, Field
from datetime import datetime
from typing import Annotated, Literal

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
    MemoirSharedVideoResponse,
    MemoirVoiceTranscriptItemResponse,
)


class MemoirHomeSnapshotResponse(BaseModel):
    server_revision: int = Field(ge=0)
    identity: MemoirIdentityResponse
    latest_transcript: MemoirVoiceTranscriptItemResponse | None = None


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


class MemoirBookSnapshotResponse(BaseModel):
    server_revision: int = Field(ge=0)
    identity: MemoirIdentityResponse
    plan: MemoirBookPlanResponse | None = None
    drafts_by_chapter_id: dict[
        str,
        MemoirBookChapterDraftResponse | None,
    ] = Field(default_factory=dict)
    output: MemoirBookOutputResponse | None = None
    progress: MemoirDraftProgressResponse | None = None
    blocking_issue_count: int = Field(default=0, ge=0)
    automation_events: MemoirAutomationEventListResponse
    image_assets: MemoirImageAssetListResponse
    share_video_counts: list[MemoirShareVideoCountResponse] = Field(default_factory=list)


class MemoirStorySectionResponse(BaseModel):
    id: str
    title: str
    material_count: int = 0
    image_count: int = 0
    video_count: int = 0


class MemoirStoryIssueCounts(BaseModel):
    review_count: int = Field(default=0, ge=0)
    supplement_count: int = Field(default=0, ge=0)


class MemoirStoryChapterResponse(BaseModel):
    id: str
    title: str
    include_in_book: bool
    material_count: int = 0
    image_count: int = 0
    video_count: int = 0
    char_count: int = 0
    readable: bool = False
    candidate_job_id: str | None = None
    state: Literal["settled", "attention", "draft", "muted"] = "muted"
    state_text: str = "待整理"
    issues: MemoirStoryIssueCounts = Field(default_factory=MemoirStoryIssueCounts)
    sections: list[MemoirStorySectionResponse] = Field(default_factory=list)


class MemoirStoryChapterChange(BaseModel):
    kind: Literal["chapter_added", "chapter_updated"]
    chapter_id: str


class MemoirStorySectionChange(BaseModel):
    kind: Literal["section_added", "section_updated"]
    chapter_id: str
    section_id: str


class MemoirStoryBookChange(BaseModel):
    kind: Literal["book_updated"] = "book_updated"


MemoirStoryChange = Annotated[
    MemoirStoryChapterChange | MemoirStorySectionChange | MemoirStoryBookChange,
    Field(discriminator="kind"),
]


class MemoirStoryGrowthEvent(BaseModel):
    event_id: str
    summary_text: str
    created_at: datetime
    book_char_count_delta: int = 0
    changes: list[MemoirStoryChange] = Field(default_factory=list)


class MemoirStorySnapshotResponse(MemoirHomeSnapshotResponse):
    book_issues: MemoirStoryIssueCounts = Field(default_factory=MemoirStoryIssueCounts)
    book_manual_edit_preserved: bool = False
    book_chapter_count: int = Field(default=0, ge=0)
    progress: MemoirDraftProgressResponse | None = None
    chapters: list[MemoirStoryChapterResponse] = Field(default_factory=list)
    book_readable: bool = False
    book_char_count: int = 0
    updated_at: str = ""
    unassigned_image_count: int = 0
    unassigned_video_count: int = 0
    automation_events: MemoirAutomationEventListResponse
    growth_events: list[MemoirStoryGrowthEvent] = Field(default_factory=list)


class MemoirDemoVideoResponse(MemoirSharedVideoResponse):
    share_code: str


class MemoirDemoStoryResponse(BaseModel):
    """A sample memoir for visitors; no owner or interview details."""

    title: str = ""
    chapters: list[MemoirStoryChapterResponse] = Field(default_factory=list)
    book_chapter_count: int = Field(default=0, ge=0)
    book_char_count: int = 0
    videos: list[MemoirDemoVideoResponse] = Field(default_factory=list)
