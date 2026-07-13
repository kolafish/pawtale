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
from app.schemas.memoir_narrative.decisions import MemoirNarrativeDecisionRequest


class MemoirDraftBlockClaimSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, max_length=36)
    usage_role: MemoirDraftBlockClaimUsageRole

    @field_validator("claim_id")
    @classmethod
    def strip_claim_id(cls, value: str) -> str:
        return value.strip()


class MemoirDraftBlockStorySource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    story_id: str = Field(min_length=1, max_length=36)
    story_revision_id: str = Field(min_length=1, max_length=36)
    usage_role: MemoirDraftBlockStoryUsageRole

    @field_validator("story_id", "story_revision_id")
    @classmethod
    def strip_story_ids(cls, value: str) -> str:
        return value.strip()


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
    legacy_draft_version_id: str | None
    claims: list[MemoirDraftBlockClaimSource]
    stories: list[MemoirDraftBlockStorySource]
    created_at: datetime


class MemoirDraftBlockResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    chapter_id: str
    node_id: str | None
    block_role: MemoirDraftBlockRole
    sort_order: int
    editorial_status: MemoirDraftBlockEditorialStatus
    freshness_status: MemoirDraftBlockFreshnessStatus
    locked: bool
    current_version_id: str
    legacy_draft_id: str | None
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

    @field_validator("expected_current_version_id", "content", "feedback")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class MemoirDraftBlockDecisionRequest(MemoirNarrativeDecisionRequest):
    expected_current_version_id: str = Field(min_length=1, max_length=36)

    @field_validator("expected_current_version_id")
    @classmethod
    def strip_expected_current_version_id(cls, value: str) -> str:
        return value.strip()
