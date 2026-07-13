from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.memoir.narrative_types import (
    MemoirNarrativeActorType,
    MemoirNarrativeEntityType,
    MemoirNarrativeEntityVerificationStatus,
)


class MemoirNarrativeEntityCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1, max_length=36)
    entity_type: MemoirNarrativeEntityType
    canonical_name: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(default_factory=list, max_length=100)
    description: str = Field(default="", max_length=4000)
    verification_status: MemoirNarrativeEntityVerificationStatus = (
        MemoirNarrativeEntityVerificationStatus.UNREVIEWED
    )
    created_by: MemoirNarrativeActorType
    created_by_user_id: str | None = Field(default=None, max_length=36)

    @field_validator("project_id", "canonical_name", "description", "created_by_user_id")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, aliases: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for alias in aliases:
            value = alias.strip()
            if not value or value in seen:
                continue
            if len(value) > 200:
                raise ValueError("entity alias must be at most 200 characters")
            seen.add(value)
            normalized.append(value)
        return normalized

    @model_validator(mode="after")
    def validate_actor(self):
        if self.created_by == MemoirNarrativeActorType.USER and not self.created_by_user_id:
            raise ValueError("user-created entity requires created_by_user_id")
        if (
            self.verification_status
            in {
                MemoirNarrativeEntityVerificationStatus.CONFIRMED,
                MemoirNarrativeEntityVerificationStatus.REJECTED,
            }
            and self.created_by != MemoirNarrativeActorType.USER
        ):
            raise ValueError("confirmed or rejected entity requires an explicit user decision")
        return self
