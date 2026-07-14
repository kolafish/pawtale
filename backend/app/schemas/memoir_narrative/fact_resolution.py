from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MemoirEntityMergeResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["merge_entities"]
    source_entity_id: str = Field(min_length=1, max_length=36)
    retained_entity_id: str = Field(min_length=1, max_length=36)
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("source_entity_id", "retained_entity_id", "reason")
    @classmethod
    def strip_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("entity merge fields cannot be blank")
        return normalized


class MemoirClaimConfirmationResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["confirm_claim"]
    claim_id: str = Field(min_length=1, max_length=36)
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("claim_id", "reason")
    @classmethod
    def strip_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("claim confirmation fields cannot be blank")
        return normalized


class MemoirClaimRejectionResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["reject_claim"]
    claim_id: str = Field(min_length=1, max_length=36)
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("claim_id", "reason")
    @classmethod
    def strip_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("claim rejection fields cannot be blank")
        return normalized


MemoirFactResolutionOperation = Annotated[
    MemoirEntityMergeResolution
    | MemoirClaimConfirmationResolution
    | MemoirClaimRejectionResolution,
    Field(discriminator="operation"),
]


class MemoirFactResolutionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["resolved", "needs_confirmation"]
    operations: list[MemoirFactResolutionOperation] = Field(default_factory=list, max_length=10)
    confirmation_question: str = Field(default="", max_length=1000)
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("confirmation_question")
    @classmethod
    def strip_confirmation_question(cls, value: str) -> str:
        return value.strip()

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("fact resolution reason cannot be blank")
        return normalized

    @model_validator(mode="after")
    def validate_status_shape(self):
        if self.status == "resolved":
            if not self.operations:
                raise ValueError("resolved fact answer requires at least one operation")
            if self.confirmation_question:
                raise ValueError("resolved fact answer cannot ask for confirmation")
        elif self.operations or not self.confirmation_question:
            raise ValueError(
                "ambiguous fact answer requires a confirmation question and no operations"
            )
        return self
