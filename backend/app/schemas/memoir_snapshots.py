from pydantic import BaseModel, Field

from app.schemas.memoir_book import (
    MemoirBookChapterDraftResponse,
    MemoirBookOutputResponse,
    MemoirBookPlanResponse,
)
from app.schemas.memoir_images import MemoirImageAssetListResponse
from app.schemas.memoir_projects import MemoirIdentityResponse
from app.schemas.memoir_voice import (
    MemoirAutomationEventListResponse,
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
