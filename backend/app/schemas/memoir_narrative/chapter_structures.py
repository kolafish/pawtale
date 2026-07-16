from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.memoir.narrative_types import MemoirChapterContractStatus


class MemoirChapterCoreQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    question: str = Field(min_length=1, max_length=500)

    @field_validator("key", "question")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class MemoirChapterContractConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_story_ids: list[str] = Field(default_factory=list, max_length=100)
    exclude_story_ids: list[str] = Field(default_factory=list, max_length=100)
    include_topics: list[str] = Field(default_factory=list, max_length=30)
    exclude_topics: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("include_story_ids", "exclude_story_ids")
    @classmethod
    def normalize_ids(cls, values: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(value.strip() for value in values if value.strip()))
        if any(len(value) > 36 for value in normalized):
            raise ValueError("chapter contract story id is too long")
        return normalized

    @field_validator("include_topics", "exclude_topics")
    @classmethod
    def normalize_topics(cls, values: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(value.strip() for value in values if value.strip()))
        if any(len(value) > 120 for value in normalized):
            raise ValueError("chapter contract topic is too long")
        return normalized

    @model_validator(mode="after")
    def validate_disjoint_constraints(self):
        if set(self.include_story_ids) & set(self.exclude_story_ids):
            raise ValueError("a story cannot be both included and excluded")
        if set(self.include_topics) & set(self.exclude_topics):
            raise ValueError("a topic cannot be both included and excluded")
        return self


class MemoirChapterContractSuggestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=160)

    @field_validator("idempotency_key")
    @classmethod
    def strip_key(cls, value: str) -> str:
        return value.strip()


class MemoirChapterNodeStoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    story_id: str
    story_role: str
    sort_order: int
    required: bool


class MemoirChapterNodeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    chapter_id: str
    contract_id: str
    node_type: str
    title: str
    purpose: str
    sort_order: int
    required: bool
    transition_from_node_id: str | None
    transition_to_node_id: str | None
    status: str
    created_by: str
    created_by_user_id: str | None
    updated_by: str
    updated_by_user_id: str | None
    stories: list[MemoirChapterNodeStoryResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class MemoirChapterContractResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    chapter_id: str
    version: int
    objective: str
    reader_takeaway: str
    time_scope_text: str
    time_start: datetime | None
    time_end: datetime | None
    core_questions: list[MemoirChapterCoreQuestion]
    constraints: MemoirChapterContractConstraints
    status: MemoirChapterContractStatus
    created_by: str
    created_by_user_id: str | None
    confirmed_by_user_id: str | None
    confirmed_at: datetime | None
    superseded_at: datetime | None
    nodes: list[MemoirChapterNodeResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
