from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.memoir.narrative_types import (
    MemoirNarrativeGapClaimRole,
    MemoirNarrativeGapDetectedBy,
    MemoirNarrativeGapRelationRole,
    MemoirNarrativeGapScopeType,
    MemoirNarrativeGapSeverity,
    MemoirNarrativeGapStatus,
    MemoirNarrativeGapType,
)
from app.domain.memoir.types import MemoirEditingJobStatus
from app.schemas.memoir_narrative.chapter_structures import MemoirChapterContractResponse
from app.schemas.memoir_narrative.responses import MemoirClaimResponse


class MemoirNarrativeGapCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope_type: MemoirNarrativeGapScopeType
    primary_story_id: str | None = Field(default=None, max_length=36)
    primary_chapter_id: str | None = Field(default=None, max_length=36)
    gap_type: MemoirNarrativeGapType
    severity: MemoirNarrativeGapSeverity
    target_slot: str = Field(default="", max_length=120)
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(default="", max_length=4000)
    detected_by: MemoirNarrativeGapDetectedBy
    claim_roles: dict[str, MemoirNarrativeGapClaimRole] = Field(default_factory=dict)
    story_roles: dict[str, MemoirNarrativeGapRelationRole] = Field(default_factory=dict)
    chapter_roles: dict[str, MemoirNarrativeGapRelationRole] = Field(default_factory=dict)

    @field_validator("target_slot", "title", "description")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_scope(self):
        if self.scope_type == MemoirNarrativeGapScopeType.STORY:
            if not self.primary_story_id or self.primary_chapter_id:
                raise ValueError("story gap requires only primary_story_id")
        elif self.scope_type == MemoirNarrativeGapScopeType.CHAPTER:
            if not self.primary_chapter_id or self.primary_story_id:
                raise ValueError("chapter gap requires only primary_chapter_id")
        elif self.primary_story_id or self.primary_chapter_id:
            raise ValueError("book gap cannot have a primary Story or chapter")
        return self


class MemoirNarrativeGapClaimResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    claim_id: str
    role: MemoirNarrativeGapClaimRole


class MemoirNarrativeGapStoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    story_id: str
    role: MemoirNarrativeGapRelationRole


class MemoirNarrativeGapChapterResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    chapter_id: str
    role: MemoirNarrativeGapRelationRole


class MemoirNarrativeGapResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    scope_type: MemoirNarrativeGapScopeType
    primary_story_id: str | None
    primary_chapter_id: str | None
    gap_type: MemoirNarrativeGapType
    severity: MemoirNarrativeGapSeverity
    target_slot: str
    title: str
    description: str
    fingerprint: str
    status: MemoirNarrativeGapStatus
    detected_by: MemoirNarrativeGapDetectedBy
    detection_count: int
    resolution_note: str
    resolved_by: str | None
    resolved_by_user_id: str | None
    resolved_at: datetime | None
    first_detected_at: datetime
    last_detected_at: datetime
    claims: list[MemoirNarrativeGapClaimResponse] = Field(default_factory=list)
    stories: list[MemoirNarrativeGapStoryResponse] = Field(default_factory=list)
    chapters: list[MemoirNarrativeGapChapterResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class MemoirNarrativeGapListResponse(BaseModel):
    items: list[MemoirNarrativeGapResponse] = Field(default_factory=list)
    total: int = 0


class MemoirNarrativeGapResolutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=160)
    note: str = Field(default="", max_length=2000)

    @field_validator("idempotency_key", "note")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class MemoirNarrativeGapDismissRequest(MemoirNarrativeGapResolutionRequest):
    @model_validator(mode="after")
    def require_note(self):
        if not self.note:
            raise ValueError("dismissing a gap requires a reason")
        return self


class MemoirChapterGapAuditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_semantic_audit: bool = True
    ai_model: str | None = Field(default=None, max_length=120)

    @field_validator("ai_model")
    @classmethod
    def strip_model(cls, value: str | None) -> str | None:
        value = value.strip() if value else None
        return value or None


class MemoirChapterGapAuditJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    chapter_id: str | None
    status: MemoirEditingJobStatus
    result_json: dict
    error_message: str
    created_at: datetime
    updated_at: datetime


class MemoirChapterWorkspaceStorySlotResponse(BaseModel):
    id: str | None = None
    slot_key: str
    required: bool
    status: str
    assessment_summary: str = ""
    claim_ids: list[str] = Field(default_factory=list)


class MemoirChapterWorkspaceStoryResponse(BaseModel):
    id: str
    title: str
    story_type: str
    summary: str
    completion_status: str
    verification_status: str
    publication_status: str
    story_version: int
    slots: list[MemoirChapterWorkspaceStorySlotResponse] = Field(default_factory=list)
    claims: list[MemoirClaimResponse] = Field(default_factory=list)


class MemoirChapterWorkspaceAvailableStoryResponse(BaseModel):
    id: str
    title: str
    story_type: str
    summary: str
    completion_status: str
    verification_status: str
    story_version: int
    excluded_from_chapter: bool = False


class MemoirChapterWorkspaceGapGroups(BaseModel):
    must_handle: list[MemoirNarrativeGapResponse] = Field(default_factory=list)
    suggested: list[MemoirNarrativeGapResponse] = Field(default_factory=list)
    accepted_incomplete: list[MemoirNarrativeGapResponse] = Field(default_factory=list)


class MemoirChapterWorkspaceAnswerUpdateResponse(BaseModel):
    id: str
    task_id: str
    status: Literal["applied", "needs_confirmation"]
    question: str
    answer_text: str = ""
    summaries: list[str] = Field(default_factory=list)
    created_at: datetime


class MemoirChapterWorkspaceResponse(BaseModel):
    project_id: str
    chapter_id: str
    chapter_title: str
    contract: MemoirChapterContractResponse | None = None
    stories: list[MemoirChapterWorkspaceStoryResponse] = Field(default_factory=list)
    available_stories: list[MemoirChapterWorkspaceAvailableStoryResponse] = Field(
        default_factory=list
    )
    answer_updates: list[MemoirChapterWorkspaceAnswerUpdateResponse] = Field(
        default_factory=list
    )
    gaps: MemoirChapterWorkspaceGapGroups
    latest_audit_job: MemoirChapterGapAuditJobResponse | None = None


class MemoirSemanticGapCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gap_type: MemoirNarrativeGapType
    severity: MemoirNarrativeGapSeverity
    dimension: Literal[
        "causality",
        "person_introduction",
        "reference",
        "transition",
        "list_style",
        "repetition",
        "contradiction",
        "unsupported_fact",
    ]
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(min_length=1, max_length=4000)
    story_ids: list[str] = Field(default_factory=list, max_length=20)
    claim_ids: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("dimension", "title", "description")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("story_ids", "claim_ids")
    @classmethod
    def normalize_ids(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))


class MemoirSemanticGapAuditOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1"
    gaps: list[MemoirSemanticGapCandidate] = Field(default_factory=list, max_length=40)

    @field_validator("schema_version")
    @classmethod
    def require_schema_version(cls, value: str) -> str:
        if value != "1":
            raise ValueError("unsupported semantic gap schema version")
        return value
