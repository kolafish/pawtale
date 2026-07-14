from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.memoir.narrative_types import (
    MemoirChapterContractStatus,
    MemoirChapterNodeStatus,
    MemoirChapterNodeStoryRole,
    MemoirChapterNodeType,
)


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


class MemoirChapterContractDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=160)
    objective: str = Field(min_length=1, max_length=2000)
    reader_takeaway: str = Field(min_length=1, max_length=2000)
    time_scope_text: str = Field(default="", max_length=1000)
    time_start: datetime | None = None
    time_end: datetime | None = None
    core_questions: list[MemoirChapterCoreQuestion] = Field(min_length=1, max_length=20)
    constraints: MemoirChapterContractConstraints = Field(
        default_factory=MemoirChapterContractConstraints
    )
    copy_from_contract_id: str | None = Field(default=None, min_length=1, max_length=36)
    copy_nodes: bool = True

    @field_validator(
        "idempotency_key",
        "objective",
        "reader_takeaway",
        "time_scope_text",
        "copy_from_contract_id",
    )
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def validate_shape(self):
        if (self.time_start is None) != (self.time_end is None):
            raise ValueError("chapter contract time range requires both endpoints")
        if self.time_start and self.time_end and self.time_end < self.time_start:
            raise ValueError("chapter contract time_end must not precede time_start")
        question_keys = [question.key for question in self.core_questions]
        if len(question_keys) != len(set(question_keys)):
            raise ValueError("chapter contract question keys must be unique")
        return self


class MemoirChapterNodeCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=160)
    node_type: MemoirChapterNodeType
    title: str = Field(min_length=1, max_length=240)
    purpose: str = Field(min_length=1, max_length=2000)
    required: bool = False
    transition_from_node_id: str | None = Field(default=None, min_length=1, max_length=36)
    transition_to_node_id: str | None = Field(default=None, min_length=1, max_length=36)
    status: MemoirChapterNodeStatus = MemoirChapterNodeStatus.PLANNED

    @field_validator(
        "idempotency_key",
        "title",
        "purpose",
        "transition_from_node_id",
        "transition_to_node_id",
    )
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def validate_transition(self):
        transition_ids = (self.transition_from_node_id, self.transition_to_node_id)
        if self.node_type == MemoirChapterNodeType.TRANSITION:
            if not all(transition_ids) or transition_ids[0] == transition_ids[1]:
                raise ValueError("transition node requires two distinct neighboring nodes")
        elif any(transition_ids):
            raise ValueError("only transition nodes can reference neighboring nodes")
        return self


class MemoirChapterNodeUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=240)
    purpose: str | None = Field(default=None, min_length=1, max_length=2000)
    required: bool | None = None
    transition_from_node_id: str | None = Field(default=None, max_length=36)
    transition_to_node_id: str | None = Field(default=None, max_length=36)
    status: MemoirChapterNodeStatus | None = None

    @field_validator("title", "purpose", "transition_from_node_id", "transition_to_node_id")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def require_update(self):
        if not self.model_fields_set:
            raise ValueError("chapter node update requires at least one field")
        return self


class MemoirChapterNodeReorderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_ids: list[str] = Field(min_length=1, max_length=100)

    @field_validator("node_ids")
    @classmethod
    def normalize_ids(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values if value.strip()]
        if len(normalized) != len(set(normalized)):
            raise ValueError("chapter node order contains duplicates")
        return normalized


class MemoirChapterNodeStoryAttachRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    story_id: str = Field(min_length=1, max_length=36)
    story_role: MemoirChapterNodeStoryRole
    sort_order: int = Field(default=0, ge=0)
    required: bool = False


class MemoirChapterNodeStoryMoveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_node_id: str = Field(min_length=1, max_length=36)
    target_node_id: str = Field(min_length=1, max_length=36)

    @model_validator(mode="after")
    def require_distinct_nodes(self):
        if self.source_node_id == self.target_node_id:
            raise ValueError("moving a Story requires two different chapter nodes")
        return self


class MemoirChapterStructureSplitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_contract_id: str = Field(min_length=1, max_length=36)
    target_contract_id: str = Field(min_length=1, max_length=36)
    node_ids: list[str] = Field(min_length=1, max_length=100)

    @field_validator("node_ids")
    @classmethod
    def normalize_node_ids(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values if value.strip()]
        if len(normalized) != len(set(normalized)):
            raise ValueError("chapter split contains duplicate nodes")
        return normalized


class MemoirChapterStructureMergeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_contract_id: str = Field(min_length=1, max_length=36)
    target_contract_id: str = Field(min_length=1, max_length=36)


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


class MemoirChapterStructureResponse(BaseModel):
    chapter_id: str
    chapter_title: str
    contracts: list[MemoirChapterContractResponse] = Field(default_factory=list)


class MemoirChapterStructureMutationResponse(BaseModel):
    source: MemoirChapterContractResponse
    target: MemoirChapterContractResponse


class MemoirChapterContractValidationIssue(BaseModel):
    code: str
    message: str
    node_id: str | None = None
    story_id: str | None = None


class MemoirChapterContractValidationResponse(BaseModel):
    contract_id: str
    contract_confirmed: bool
    required_node_count: int
    ready_or_excluded_required_node_count: int
    can_generate: bool
    issues: list[MemoirChapterContractValidationIssue] = Field(default_factory=list)
