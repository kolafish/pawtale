from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.memoir.types import (
    MemoirCoverageStatus,
    MemoirCreatedBy,
    MemoirInterviewStage,
    MemoirMemoryStatus,
    MemoirMemoryType,
    MemoirOpenQuestionStatus,
    MemoirQuestionCandidateStatus,
    MemoirQuestionType,
    MemoirSensitivity,
)
from app.schemas.memoir_legacy import (
    MemoirInterviewState,
    MemoirOutputResponse,
    MemoirSessionSummaryResponse,
    MemoirSummaryResponse,
)
from app.schemas.projects import ProjectResponse


class MemoirMemoryUpdateRequest(BaseModel):
    status: MemoirMemoryStatus | None = None
    canonical_name: str | None = None
    aliases_json: list[str] | None = None
    relationship: str | None = None
    title: str | None = None
    life_stage: str | None = None
    time_text: str | None = None
    place_text: str | None = None
    cause: str | None = None
    process: str | None = None
    result: str | None = None
    emotion: str | None = None
    importance: float | None = Field(default=None, ge=0, le=1)
    name: str | None = None
    elder_alias: str | None = None
    modern_name: str | None = None
    place_type: str | None = None
    description: str | None = None
    strength: float | None = Field(default=None, ge=0, le=1)
    mention_count: int | None = Field(default=None, ge=1)
    confidence: float | None = Field(default=None, ge=0, le=1)

class MemoirOpenQuestionUpdateRequest(BaseModel):
    question: str | None = None
    reason: str | None = None
    sensitivity: MemoirSensitivity | None = None
    priority: float | None = Field(default=None, ge=0, le=1)
    status: MemoirOpenQuestionStatus | None = None

class MemoirPersonMemoryCreate(BaseModel):
    project_id: str
    canonical_name: str
    aliases_json: list[str] = Field(default_factory=list)
    relationship: str | None = None
    first_seen_session_id: str | None = None
    description: str = ""
    confidence: float | None = Field(default=None, ge=0, le=1)
    status: MemoirMemoryStatus = MemoirMemoryStatus.DRAFT
    created_by: MemoirCreatedBy = MemoirCreatedBy.AI

class MemoirPersonMemoryResponse(BaseModel):
    id: str
    project_id: str
    canonical_name: str
    aliases_json: list[str] = Field(default_factory=list)
    relationship: str | None = None
    first_seen_session_id: str | None = None
    description: str
    confidence: float | None = None
    status: MemoirMemoryStatus
    created_by: MemoirCreatedBy
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

class MemoirEventMemoryCreate(BaseModel):
    project_id: str
    title: str
    life_stage: str | None = None
    time_text: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    place_text: str | None = None
    cause: str = ""
    process: str = ""
    result: str = ""
    emotion: str | None = None
    importance: float | None = Field(default=None, ge=0, le=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    status: MemoirMemoryStatus = MemoirMemoryStatus.DRAFT
    created_by: MemoirCreatedBy = MemoirCreatedBy.AI

class MemoirEventMemoryResponse(BaseModel):
    id: str
    project_id: str
    title: str
    life_stage: str | None = None
    time_text: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    place_text: str | None = None
    cause: str
    process: str
    result: str
    emotion: str | None = None
    importance: float | None = None
    confidence: float | None = None
    status: MemoirMemoryStatus
    created_by: MemoirCreatedBy
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

class MemoirPlaceMemoryCreate(BaseModel):
    project_id: str
    name: str
    elder_alias: str | None = None
    modern_name: str | None = None
    place_type: str | None = None
    description: str = ""
    confidence: float | None = Field(default=None, ge=0, le=1)
    status: MemoirMemoryStatus = MemoirMemoryStatus.DRAFT
    created_by: MemoirCreatedBy = MemoirCreatedBy.AI

class MemoirPlaceMemoryResponse(BaseModel):
    id: str
    project_id: str
    name: str
    elder_alias: str | None = None
    modern_name: str | None = None
    place_type: str | None = None
    description: str
    confidence: float | None = None
    status: MemoirMemoryStatus
    created_by: MemoirCreatedBy
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

class MemoirThemeMemoryCreate(BaseModel):
    project_id: str
    name: str
    description: str = ""
    strength: float | None = Field(default=None, ge=0, le=1)
    mention_count: int = Field(default=1, ge=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    status: MemoirMemoryStatus = MemoirMemoryStatus.DRAFT
    created_by: MemoirCreatedBy = MemoirCreatedBy.AI

class MemoirThemeMemoryResponse(BaseModel):
    id: str
    project_id: str
    name: str
    description: str
    strength: float | None = None
    mention_count: int
    confidence: float | None = None
    status: MemoirMemoryStatus
    created_by: MemoirCreatedBy
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

class MemoirMemoryEvidenceCreate(BaseModel):
    project_id: str
    memory_type: MemoirMemoryType
    memory_id: str
    session_id: str | None = None
    question_id: str | None = None
    answer_id: str | None = None
    transcript_version_id: str | None = None
    quote: str = ""
    start_offset: int | None = Field(default=None, ge=0)
    end_offset: int | None = Field(default=None, ge=0)
    confidence: float | None = Field(default=None, ge=0, le=1)

class MemoirMemoryEvidenceResponse(BaseModel):
    id: str
    project_id: str
    memory_type: MemoirMemoryType
    memory_id: str
    session_id: str | None = None
    question_id: str | None = None
    answer_id: str | None = None
    transcript_version_id: str | None = None
    quote: str
    start_offset: int | None = None
    end_offset: int | None = None
    confidence: float | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class MemoirOpenQuestionCreate(BaseModel):
    project_id: str
    session_id: str | None = None
    target_memory_type: MemoirMemoryType | None = None
    target_memory_id: str | None = None
    question: str
    question_type: MemoirQuestionType
    reason: str = ""
    sensitivity: MemoirSensitivity = MemoirSensitivity.LOW
    priority: float | None = Field(default=None, ge=0, le=1)
    status: MemoirOpenQuestionStatus = MemoirOpenQuestionStatus.OPEN
    created_by: MemoirCreatedBy = MemoirCreatedBy.AI

class MemoirOpenQuestionResponse(BaseModel):
    id: str
    project_id: str
    session_id: str | None = None
    target_memory_type: str | None = None
    target_memory_id: str | None = None
    question: str
    question_type: MemoirQuestionType
    reason: str
    sensitivity: MemoirSensitivity
    priority: float | None = None
    status: MemoirOpenQuestionStatus
    created_by: MemoirCreatedBy
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

class MemoirCoverageTopicCreate(BaseModel):
    project_id: str
    key: str
    name: str
    life_stage: str | None = None
    description: str = ""
    sort_order: int = 0

class MemoirCoverageTopicResponse(BaseModel):
    id: str
    project_id: str
    key: str
    name: str
    life_stage: str | None = None
    description: str
    sort_order: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

class MemoirCoverageSnapshotResponse(BaseModel):
    id: str
    project_id: str
    topic_id: str
    answered_count: int
    memory_count: int
    open_question_count: int
    coverage_score: float
    status: MemoirCoverageStatus
    signals_json: dict[str, object] = Field(default_factory=dict)
    calculated_at: datetime

    model_config = ConfigDict(from_attributes=True)

class MemoirCoverageItemResponse(BaseModel):
    topic_id: str
    key: str
    name: str
    life_stage: str | None = None
    description: str
    sort_order: int
    answered_count: int
    memory_count: int
    open_question_count: int
    coverage_score: float
    status: MemoirCoverageStatus
    signals_json: dict[str, object] = Field(default_factory=dict)
    calculated_at: datetime

class MemoirQuestionCandidateResponse(BaseModel):
    id: str
    project_id: str
    session_id: str | None = None
    question: str
    question_type: MemoirQuestionType
    target_memory_type: str | None = None
    target_memory_id: str | None = None
    coverage_topic_id: str | None = None
    reason: str
    sensitivity: MemoirSensitivity
    stage: MemoirInterviewStage
    priority: float
    status: MemoirQuestionCandidateStatus
    created_by: MemoirCreatedBy
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class MemoirSessionProcessResponse(BaseModel):
    session_summary: MemoirSessionSummaryResponse
    story_fragment: MemoirOutputResponse | None = None
    people: list[MemoirPersonMemoryResponse] = Field(default_factory=list)
    events: list[MemoirEventMemoryResponse] = Field(default_factory=list)
    places: list[MemoirPlaceMemoryResponse] = Field(default_factory=list)
    themes: list[MemoirThemeMemoryResponse] = Field(default_factory=list)
    open_questions: list[MemoirOpenQuestionResponse] = Field(default_factory=list)

class MemoirMemoryStatsResponse(BaseModel):
    total_memory_count: int = 0
    draft_memory_count: int = 0
    confirmed_memory_count: int = 0
    rejected_memory_count: int = 0
    open_question_count: int = 0
    asked_open_question_count: int = 0
    answered_open_question_count: int = 0
    dismissed_open_question_count: int = 0
    question_candidate_count: int = 0
    output_count: int = 0
    coverage_average: int = 0
    low_coverage_count: int = 0

class MemoirDashboardResponse(BaseModel):
    project: ProjectResponse
    interview: MemoirInterviewState
    summary: MemoirSummaryResponse
    session_summary: MemoirSessionSummaryResponse | None = None
    people: list[MemoirPersonMemoryResponse] = Field(default_factory=list)
    events: list[MemoirEventMemoryResponse] = Field(default_factory=list)
    places: list[MemoirPlaceMemoryResponse] = Field(default_factory=list)
    themes: list[MemoirThemeMemoryResponse] = Field(default_factory=list)
    open_questions: list[MemoirOpenQuestionResponse] = Field(default_factory=list)
    coverage: list[MemoirCoverageItemResponse] = Field(default_factory=list)
    candidates: list[MemoirQuestionCandidateResponse] = Field(default_factory=list)
    outputs: list[MemoirOutputResponse] = Field(default_factory=list)
    memory_stats: MemoirMemoryStatsResponse = Field(default_factory=MemoirMemoryStatsResponse)
