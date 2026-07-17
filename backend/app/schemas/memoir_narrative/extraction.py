from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.memoir.narrative_types import (
    MemoirClaimCertainty,
    MemoirClaimPredicate,
    MemoirClaimTimePrecision,
    MemoirClaimType,
    MemoirNarrativeEntityType,
    MemoirStoryNarrativeRole,
    MemoirStoryType,
)


class MemoirNarrativeExtractedEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    entity_type: MemoirNarrativeEntityType
    canonical_name: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(default_factory=list, max_length=20)
    description: str = Field(default="", max_length=1000)
    is_narrator: bool = False

    @field_validator("key", "canonical_name", "description")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, aliases: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(alias.strip() for alias in aliases if alias.strip()))
        if any(len(alias) > 200 for alias in normalized):
            raise ValueError("entity alias must be at most 200 characters")
        return normalized

    @model_validator(mode="after")
    def validate_narrator(self):
        if self.is_narrator and self.entity_type != MemoirNarrativeEntityType.PERSON:
            raise ValueError("narrator entity must be a person")
        return self


class MemoirNarrativeExtractedClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    claim_type: MemoirClaimType
    statement: str = Field(min_length=1, max_length=1000)
    subject_entity_key: str | None = Field(default=None, max_length=80)
    predicate_code: MemoirClaimPredicate = MemoirClaimPredicate.NARRATIVE_FACT
    object_entity_key: str | None = Field(default=None, max_length=80)
    object_text: str | None = Field(default=None, max_length=500)
    place_entity_key: str | None = Field(default=None, max_length=80)
    time_text: str | None = Field(default=None, max_length=200)
    time_start: datetime | None = None
    time_end: datetime | None = None
    time_precision: MemoirClaimTimePrecision = MemoirClaimTimePrecision.UNKNOWN
    certainty: MemoirClaimCertainty = MemoirClaimCertainty.UNCERTAIN
    evidence_quotes: list[str] = Field(min_length=1, max_length=8)

    @field_validator(
        "key",
        "statement",
        "subject_entity_key",
        "object_entity_key",
        "object_text",
        "place_entity_key",
        "time_text",
    )
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("evidence_quotes")
    @classmethod
    def normalize_quotes(cls, quotes: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(quote.strip() for quote in quotes if quote.strip()))
        if not normalized:
            raise ValueError("claim requires at least one evidence quote")
        return normalized

    @model_validator(mode="after")
    def validate_shape(self):
        if self.claim_type == MemoirClaimType.RELATIONSHIP:
            if not self.subject_entity_key or not self.object_entity_key:
                raise ValueError("relationship claim requires subject and object entities")
        if (self.time_start is None) != (self.time_end is None):
            raise ValueError("normalized time requires both time_start and time_end")
        if self.time_start and self.time_end and self.time_end < self.time_start:
            raise ValueError("time_end must not precede time_start")
        return self


class MemoirNarrativeExtractedStoryClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_key: str = Field(min_length=1, max_length=80)
    narrative_role: MemoirStoryNarrativeRole
    is_core: bool = False

    @field_validator("claim_key")
    @classmethod
    def strip_claim_key(cls, value: str) -> str:
        return value.strip()

class MemoirNarrativeExtractedStory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    title: str = Field(min_length=1, max_length=240)
    story_type: MemoirStoryType
    summary: str = Field(default="", max_length=4000)
    time_start: datetime | None = None
    time_end: datetime | None = None
    time_precision: MemoirClaimTimePrecision = MemoirClaimTimePrecision.UNKNOWN
    primary_place_entity_key: str | None = Field(default=None, max_length=80)
    existing_story_id: str | None = Field(default=None, max_length=36)
    match_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    claims: list[MemoirNarrativeExtractedStoryClaim] = Field(min_length=1, max_length=30)

    @field_validator("key", "title", "summary", "primary_place_entity_key", "existing_story_id")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def validate_shape(self):
        if (self.time_start is None) != (self.time_end is None):
            raise ValueError("story normalized time requires both time_start and time_end")
        if self.time_start and self.time_end and self.time_end < self.time_start:
            raise ValueError("story time_end must not precede time_start")
        claim_keys = [claim.claim_key for claim in self.claims]
        if len(claim_keys) != len(set(claim_keys)):
            raise ValueError("story claim references must be unique")
        return self


class MemoirNarrativeExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(pattern=r"^1$")
    entities: list[MemoirNarrativeExtractedEntity] = Field(default_factory=list, max_length=30)
    claims: list[MemoirNarrativeExtractedClaim] = Field(default_factory=list, max_length=30)
    stories: list[MemoirNarrativeExtractedStory] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def validate_keys(self):
        entity_keys = [entity.key for entity in self.entities]
        if len(entity_keys) != len(set(entity_keys)):
            raise ValueError("entity keys must be unique")
        claim_keys = [claim.key for claim in self.claims]
        if len(claim_keys) != len(set(claim_keys)):
            raise ValueError("claim keys must be unique")
        story_keys = [story.key for story in self.stories]
        if len(story_keys) != len(set(story_keys)):
            raise ValueError("story keys must be unique")
        entity_types = {entity.key: entity.entity_type for entity in self.entities}
        known_entities = set(entity_types)
        for claim in self.claims:
            references = {
                claim.subject_entity_key,
                claim.object_entity_key,
                claim.place_entity_key,
            } - {None}
            if not references.issubset(known_entities):
                raise ValueError("claim references an unknown entity key")
            if (
                claim.place_entity_key
                and entity_types[claim.place_entity_key] != MemoirNarrativeEntityType.PLACE
            ):
                raise ValueError("place_entity_key must reference a place entity")
        known_claims = set(claim_keys)
        for story in self.stories:
            if not {link.claim_key for link in story.claims}.issubset(known_claims):
                raise ValueError("story references an unknown claim key")
            if story.primary_place_entity_key:
                if story.primary_place_entity_key not in known_entities:
                    raise ValueError("story references an unknown place entity key")
                if entity_types[story.primary_place_entity_key] != MemoirNarrativeEntityType.PLACE:
                    raise ValueError("story primary place must reference a place entity")
        return self
