from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.memoir.narrative_types import (
    MemoirNarrativeActorType,
    MemoirNarrativeDecisionAction,
    MemoirNarrativeDecisionTargetType,
)


class MemoirNarrativeDecisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1, max_length=36)
    target_type: MemoirNarrativeDecisionTargetType
    target_id: str = Field(min_length=1, max_length=36)
    action: MemoirNarrativeDecisionAction
    actor_type: MemoirNarrativeActorType
    actor_user_id: str | None = Field(default=None, max_length=36)
    source_followup_task_answer_id: str | None = Field(default=None, max_length=36)
    source_transcript_version_id: str | None = Field(default=None, max_length=36)
    before_state: dict
    after_state: dict
    reason: str = Field(default="", max_length=4000)
    idempotency_key: str = Field(min_length=1, max_length=200)

    @field_validator(
        "project_id",
        "target_id",
        "actor_user_id",
        "source_followup_task_answer_id",
        "source_transcript_version_id",
        "reason",
        "idempotency_key",
    )
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def validate_actor(self):
        if self.actor_type == MemoirNarrativeActorType.USER and not self.actor_user_id:
            raise ValueError("user decision requires actor_user_id")
        if bool(self.source_followup_task_answer_id) != bool(
            self.source_transcript_version_id
        ):
            raise ValueError(
                "follow-up decision source requires both task answer and transcript version"
            )
        return self


class MemoirNarrativeDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=200)
    reason: str = Field(default="", max_length=4000)

    @field_validator("idempotency_key", "reason")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class MemoirNarrativeEntityMergeRequest(MemoirNarrativeDecisionRequest):
    retained_entity_id: str = Field(min_length=1, max_length=36)

    @field_validator("retained_entity_id")
    @classmethod
    def strip_retained_entity_id(cls, value: str) -> str:
        return value.strip()
