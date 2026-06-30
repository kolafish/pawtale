from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.memoir.types import MemoirSemanticCacheStatus


class MemoirSourceCardPayload(BaseModel):
    short_title: str = ""
    summary: str = ""
    people: list[str] = Field(default_factory=list)
    places: list[str] = Field(default_factory=list)
    time_hints: list[str] = Field(default_factory=list)
    event_tags: list[str] = Field(default_factory=list)
    emotion_tags: list[str] = Field(default_factory=list)
    key_facts: list[dict] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class MemoirChapterCardPayload(BaseModel):
    summary: str = ""
    covered: dict = Field(default_factory=dict)
    gaps: list[dict] = Field(default_factory=list)
    next_questions: list[str] = Field(default_factory=list)
    source_card_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[dict] = Field(default_factory=list)


class MemoirSourceCardResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    utterance_id: str
    transcript_version_id: str | None = None
    source_text_hash: str
    engine: str
    status: MemoirSemanticCacheStatus
    card_json: dict = Field(default_factory=dict)
    error_message: str = ""
    created_at: datetime
    updated_at: datetime


class MemoirChapterCardResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    plan_id: str
    chapter_id: str
    draft_version_id: str | None = None
    input_signature: str
    engine: str
    status: MemoirSemanticCacheStatus
    card_json: dict = Field(default_factory=dict)
    error_message: str = ""
    created_at: datetime
    updated_at: datetime


class MemoirBookQualityAuditSnapshotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    engine: str
    input_signature: str
    overall_status: str
    summary_text: str
    display_json: dict = Field(default_factory=dict)
    primary_action_json: dict = Field(default_factory=dict)
    chapter_audits_json: list = Field(default_factory=list)
    source_card_ids_json: list[str] = Field(default_factory=list)
    chapter_card_ids_json: list[str] = Field(default_factory=list)
    error_message: str = ""
    created_at: datetime
    updated_at: datetime

    @field_validator("source_card_ids_json", "chapter_card_ids_json", mode="before")
    @classmethod
    def _clean_id_list(cls, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, str) and item.strip()]
