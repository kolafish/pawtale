from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.memoir.narrative_types import (
    MemoirDraftBlockClaimUsageRole,
    MemoirDraftBlockEditorialStatus,
    MemoirDraftBlockFreshnessStatus,
    MemoirDraftBlockRole,
    MemoirDraftBlockStoryUsageRole,
    MemoirNarrativeActorType,
)
from app.domain.memoir.types import MemoirEditingJobStatus, MemoirEditingJobType
from app.schemas.memoir_narrative.decisions import MemoirNarrativeDecisionRequest


class MemoirDraftBlockClaimSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, max_length=36)
    usage_role: MemoirDraftBlockClaimUsageRole

    @field_validator("claim_id", mode="before")
    @classmethod
    def strip_claim_id(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class MemoirDraftBlockStorySource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    story_id: str = Field(min_length=1, max_length=36)
    story_revision_id: str = Field(min_length=1, max_length=36)
    usage_role: MemoirDraftBlockStoryUsageRole

    @field_validator("story_id", "story_revision_id", mode="before")
    @classmethod
    def strip_story_ids(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class MemoirDraftBlockVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    block_id: str
    version: int
    content: str
    created_by: MemoirNarrativeActorType
    model: str | None
    prompt_snapshot: str
    feedback: str | None
    claims: list[MemoirDraftBlockClaimSource]
    stories: list[MemoirDraftBlockStorySource]
    created_at: datetime


class MemoirDraftBlockResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    chapter_id: str
    node_id: str
    block_role: MemoirDraftBlockRole
    sort_order: int
    editorial_status: MemoirDraftBlockEditorialStatus
    freshness_status: MemoirDraftBlockFreshnessStatus
    locked: bool
    current_version_id: str
    current_version: MemoirDraftBlockVersionResponse
    version_count: int
    created_at: datetime
    updated_at: datetime


class MemoirDraftBlockListResponse(BaseModel):
    chapter_id: str
    blocks: list[MemoirDraftBlockResponse]


class MemoirDraftBlockManualRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_current_version_id: str = Field(min_length=1, max_length=36)
    content: str = Field(min_length=1, max_length=200_000)
    feedback: str | None = Field(default=None, max_length=4000)

    @field_validator("expected_current_version_id", "content", "feedback", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class MemoirDraftBlockDecisionRequest(MemoirNarrativeDecisionRequest):
    expected_current_version_id: str = Field(min_length=1, max_length=36)

    @field_validator("expected_current_version_id", mode="before")
    @classmethod
    def strip_expected_current_version_id(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class MemoirDraftBlockGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_ids: list[str] = Field(default_factory=list, max_length=100)
    ai_model: str | None = Field(default=None, max_length=120)

    @field_validator("node_ids")
    @classmethod
    def unique_node_ids(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        if any(len(item) > 36 for item in normalized):
            raise ValueError("node id cannot exceed 36 characters")
        return normalized

    @field_validator("ai_model")
    @classmethod
    def strip_ai_model(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class MemoirDraftBlockRevisionJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_current_version_id: str = Field(min_length=1, max_length=36)
    feedback: str = Field(min_length=1, max_length=4000)
    ai_model: str | None = Field(default=None, max_length=120)

    @field_validator("expected_current_version_id", "feedback", mode="before")
    @classmethod
    def strip_required_revision_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("ai_model")
    @classmethod
    def strip_revision_ai_model(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class MemoirDraftBlockRevisionApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_current_version_id: str = Field(min_length=1, max_length=36)
    generation_job_id: str = Field(min_length=1, max_length=36)

    @field_validator("expected_current_version_id", "generation_job_id", mode="before")
    @classmethod
    def strip_apply_ids(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class MemoirDraftBlockJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    chapter_id: str | None
    job_type: MemoirEditingJobType
    status: MemoirEditingJobStatus
    result_json: dict[str, object] = Field(default_factory=dict)
    error_message: str
    created_at: datetime
    updated_at: datetime


class MemoirDraftBlockAssemblyItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block_id: str
    block_version_id: str
    node_id: str
    block_role: MemoirDraftBlockRole
    node_sort_order: int
    block_sort_order: int
    editorial_status: MemoirDraftBlockEditorialStatus
    freshness_status: MemoirDraftBlockFreshnessStatus
    locked: bool
    content_char_count: int


class MemoirChapterDraftBlockAssemblyResponse(BaseModel):
    project_id: str
    chapter_id: str
    draft_id: str
    draft_version_id: str
    version: int
    content: str
    blocks: list[MemoirDraftBlockAssemblyItem]
    created_at: datetime


class MemoirBookDraftBlockAssemblyResponse(BaseModel):
    project_id: str
    output_id: str
    output_version_id: str
    version: int
    content: str
    chapter_snapshot_json: list[dict[str, object]]
    created_at: datetime
