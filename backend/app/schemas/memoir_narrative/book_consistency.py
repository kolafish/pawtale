from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.memoir.narrative_types import (
    MemoirNarrativeGapSeverity,
    MemoirNarrativeGapType,
)
from app.domain.memoir.types import MemoirEditingJobStatus, MemoirOutputStatus


class MemoirBookConsistencyAuditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_semantic_audit: bool = True
    ai_model: str | None = Field(default=None, max_length=120)

    @field_validator("ai_model")
    @classmethod
    def strip_model(cls, value: str | None) -> str | None:
        value = value.strip() if value else None
        return value or None


class MemoirBookConsistencyAuditJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    status: MemoirEditingJobStatus
    payload_json: dict
    result_json: dict
    error_message: str
    created_at: datetime
    updated_at: datetime


class MemoirBookReadinessBlocker(BaseModel):
    code: str
    title: str
    detail: str = ""
    chapter_id: str | None = None
    story_id: str | None = None
    claim_id: str | None = None
    completion_item_id: str | None = None


class MemoirBookReadinessGroups(BaseModel):
    must_handle: list[MemoirBookReadinessBlocker] = Field(default_factory=list)
    suggested: list[MemoirBookReadinessBlocker] = Field(default_factory=list)


class MemoirBookReadinessResponse(BaseModel):
    project_id: str
    ready_for_formal: bool
    current_signature: str
    output_status: MemoirOutputStatus | None = None
    output_version_id: str | None = None
    latest_audit_job: MemoirBookConsistencyAuditJobResponse | None = None
    audit_is_current: bool = False
    groups: MemoirBookReadinessGroups


class MemoirBookFormalizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audit_job_id: str = Field(min_length=1, max_length=36)
    output_version_id: str = Field(min_length=1, max_length=36)

    @field_validator("audit_job_id", "output_version_id")
    @classmethod
    def strip_id(cls, value: str) -> str:
        return value.strip()


class MemoirBookFormalizeResponse(BaseModel):
    project_id: str
    output_id: str
    output_version_id: str
    audit_job_id: str
    status: MemoirOutputStatus
    formalized_at: datetime


class MemoirBookSemanticGapCandidate(BaseModel):
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
    story_ids: list[str] = Field(default_factory=list, max_length=40)
    claim_ids: list[str] = Field(default_factory=list, max_length=80)
    chapter_ids: list[str] = Field(default_factory=list, max_length=40)

    @field_validator("title", "description")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("story_ids", "claim_ids", "chapter_ids")
    @classmethod
    def normalize_ids(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))


class MemoirBookSemanticGapAuditOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1"
    gaps: list[MemoirBookSemanticGapCandidate] = Field(default_factory=list, max_length=60)

    @field_validator("schema_version")
    @classmethod
    def require_schema_version(cls, value: str) -> str:
        if value != "1":
            raise ValueError("unsupported book semantic gap schema version")
        return value
