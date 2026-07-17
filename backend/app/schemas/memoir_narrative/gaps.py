from __future__ import annotations

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.memoir.narrative_types import (
    MemoirNarrativeGapActionDecisionSource,
    MemoirNarrativeGapActionReasonCode,
    MemoirNarrativeGapClaimRole,
    MemoirNarrativeGapDetectedBy,
    MemoirNarrativeGapRecommendedAction,
    MemoirNarrativeGapScopeType,
    MemoirNarrativeGapSeverity,
    MemoirNarrativeGapStatus,
    MemoirNarrativeGapType,
)
from app.schemas.memoir_narrative.book_completion import MemoirChapterCompletionSummary


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
    involved_story_ids: set[str] = Field(default_factory=set)
    involved_chapter_ids: set[str] = Field(default_factory=set)
    action_reason_signal: MemoirNarrativeGapActionReasonCode | None = None

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
        if self.primary_story_id in self.involved_story_ids:
            raise ValueError("primary Story cannot also be an involved Story")
        if self.primary_chapter_id in self.involved_chapter_ids:
            raise ValueError("primary chapter cannot also be an involved chapter")
        return self


class MemoirNarrativeGapClaimResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    claim_id: str
    role: MemoirNarrativeGapClaimRole


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
    recommended_action: MemoirNarrativeGapRecommendedAction
    action_reason_code: MemoirNarrativeGapActionReasonCode
    action_decision_source: MemoirNarrativeGapActionDecisionSource
    action_policy_version: str
    detected_by: MemoirNarrativeGapDetectedBy
    detection_count: int
    resolution_note: str
    resolved_by: str | None
    resolved_by_user_id: str | None
    resolved_at: datetime | None
    first_detected_at: datetime
    last_detected_at: datetime
    claims: list[MemoirNarrativeGapClaimResponse] = Field(default_factory=list)
    involved_story_ids: list[str] = Field(default_factory=list)
    involved_chapter_ids: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class MemoirNarrativeGapResolutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=160)
    note: str = Field(min_length=1, max_length=2000)

    @field_validator("idempotency_key", "note", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class MemoirChapterWorkspaceResponse(BaseModel):
    project_id: str
    chapter_id: str
    chapter_title: str
    completion_summary: MemoirChapterCompletionSummary


class MemoirSemanticGapCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gap_type: MemoirNarrativeGapType
    severity: MemoirNarrativeGapSeverity
    dimension: Literal[
        "causality",
        "chapter_context",
        "chapter_focus",
        "opening",
        "closing",
        "person_introduction",
        "relationship_clarity",
        "reference",
        "transition",
        "list_style",
        "repetition",
        "contradiction",
        "unsupported_fact",
    ]
    reason_signal: MemoirNarrativeGapActionReasonCode
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(min_length=1, max_length=4000)
    story_ids: list[str] = Field(default_factory=list, max_length=20)
    claim_ids: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("dimension", "title", "description")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("reason_signal")
    @classmethod
    def reject_migration_reason(
        cls,
        value: MemoirNarrativeGapActionReasonCode,
    ) -> MemoirNarrativeGapActionReasonCode:
        if value == MemoirNarrativeGapActionReasonCode.MIGRATION_REVIEW_REQUIRED:
            raise ValueError("migration reason cannot come from semantic audit")
        return value

    @field_validator("story_ids", "claim_ids")
    @classmethod
    def normalize_ids(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))


class MemoirSemanticGapAuditOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "2"
    gaps: list[MemoirSemanticGapCandidate] = Field(default_factory=list, max_length=40)

    @field_validator("schema_version")
    @classmethod
    def require_schema_version(cls, value: str) -> str:
        if value != "2":
            raise ValueError("unsupported semantic gap schema version")
        return value
