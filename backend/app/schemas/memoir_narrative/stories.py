from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.memoir.narrative_types import (
    MemoirClaimTimePrecision,
    MemoirNarrativeActorType,
    MemoirStoryNarrativeRole,
    MemoirStoryPublicationStatus,
    MemoirStoryType,
    MemoirStoryVerificationStatus,
)


class MemoirStoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1, max_length=36)
    title: str = Field(min_length=1, max_length=240)
    story_type: MemoirStoryType
    summary: str = Field(default="", max_length=4000)
    time_start: datetime | None = None
    time_end: datetime | None = None
    time_precision: MemoirClaimTimePrecision = MemoirClaimTimePrecision.UNKNOWN
    primary_place_entity_id: str | None = Field(default=None, min_length=1, max_length=36)
    verification_status: MemoirStoryVerificationStatus = MemoirStoryVerificationStatus.UNREVIEWED
    publication_status: MemoirStoryPublicationStatus = MemoirStoryPublicationStatus.ELIGIBLE
    created_by: MemoirNarrativeActorType
    created_by_user_id: str | None = Field(default=None, max_length=36)

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("story title must not be blank")
        return stripped

    @field_validator("summary")
    @classmethod
    def strip_summary(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_shape(self):
        if self.created_by == MemoirNarrativeActorType.USER and not self.created_by_user_id:
            raise ValueError("user-created story requires created_by_user_id")
        if self.verification_status not in {
            MemoirStoryVerificationStatus.UNREVIEWED,
            MemoirStoryVerificationStatus.NEEDS_VERIFICATION,
        }:
            raise ValueError("initial story verification requires the decision workflow")
        if self.publication_status != MemoirStoryPublicationStatus.ELIGIBLE:
            raise ValueError("initial story publication requires the decision workflow")
        if (self.time_start is None) != (self.time_end is None):
            raise ValueError("story normalized time requires both time_start and time_end")
        if self.time_start and self.time_end and self.time_end < self.time_start:
            raise ValueError("story time_end must not precede time_start")
        return self


class MemoirStoryCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=240)
    story_type: MemoirStoryType
    summary: str = Field(default="", max_length=4000)
    time_start: datetime | None = None
    time_end: datetime | None = None
    time_precision: MemoirClaimTimePrecision = MemoirClaimTimePrecision.UNKNOWN
    primary_place_entity_id: str | None = Field(default=None, min_length=1, max_length=36)
    idempotency_key: str = Field(min_length=1, max_length=160)
    reason: str = Field(default="", max_length=1000)

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("story title must not be blank")
        return stripped

    @field_validator("summary", "reason")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_time(self):
        if (self.time_start is None) != (self.time_end is None):
            raise ValueError("story normalized time requires both time_start and time_end")
        if self.time_start and self.time_end and self.time_end < self.time_start:
            raise ValueError("story time_end must not precede time_start")
        return self


class MemoirStoryClaimInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, max_length=36)
    narrative_role: MemoirStoryNarrativeRole
    sort_order: int = Field(default=0, ge=0)
    is_core: bool = False


class MemoirStoryClaimsAttachRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: list[MemoirStoryClaimInput] = Field(min_length=1, max_length=50)
    reason: str = Field(default="", max_length=1000)


class MemoirStoryMergeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    retained_story_id: str = Field(min_length=1, max_length=36)
    idempotency_key: str = Field(min_length=1, max_length=160)
    reason: str = Field(default="", max_length=1000)


class MemoirStoryClaimLinkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    claim_id: str
    narrative_role: str
    sort_order: int
    is_core: bool


class MemoirStoryRevisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    story_version: int
    change_type: str
    before_state_json: dict
    after_state_json: dict
    reason: str
    created_by: str
    created_by_user_id: str | None
    created_at: datetime


class MemoirStorySummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    title: str
    story_type: str
    summary: str
    time_start: datetime | None
    time_end: datetime | None
    time_precision: str
    primary_place_entity_id: str | None
    lifecycle_status: str
    verification_status: str
    publication_status: str
    merged_into_story_id: str | None
    version: int
    updated_at: datetime


class MemoirStoryResponse(MemoirStorySummaryResponse):
    claims: list[MemoirStoryClaimLinkResponse] = Field(default_factory=list)
    revisions: list[MemoirStoryRevisionResponse] = Field(default_factory=list)
