from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.memoir.narrative_types import (
    MemoirClaimCertainty,
    MemoirClaimEvidenceRole,
    MemoirClaimEvidenceSourceType,
    MemoirClaimEvidenceSpeakerRole,
    MemoirClaimPredicate,
    MemoirClaimPublicationStatus,
    MemoirClaimTimePrecision,
    MemoirClaimType,
    MemoirClaimVerificationStatus,
    MemoirNarrativeActorType,
)


class MemoirClaimCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1, max_length=36)
    claim_type: MemoirClaimType
    statement: str = Field(min_length=1, max_length=8000)
    subject_entity_id: str | None = Field(default=None, max_length=36)
    predicate_code: MemoirClaimPredicate = MemoirClaimPredicate.NARRATIVE_FACT
    object_entity_id: str | None = Field(default=None, max_length=36)
    object_text: str | None = Field(default=None, max_length=4000)
    place_entity_id: str | None = Field(default=None, max_length=36)
    time_text: str | None = Field(default=None, max_length=500)
    time_start: datetime | None = None
    time_end: datetime | None = None
    time_precision: MemoirClaimTimePrecision = MemoirClaimTimePrecision.UNKNOWN
    time_anchor_claim_id: str | None = Field(default=None, max_length=36)
    certainty: MemoirClaimCertainty = MemoirClaimCertainty.UNCERTAIN
    verification_status: MemoirClaimVerificationStatus = MemoirClaimVerificationStatus.UNREVIEWED
    publication_status: MemoirClaimPublicationStatus = MemoirClaimPublicationStatus.ELIGIBLE
    conflict_group_id: str | None = Field(default=None, max_length=36)
    created_by: MemoirNarrativeActorType
    created_by_user_id: str | None = Field(default=None, max_length=36)

    @field_validator(
        "project_id",
        "statement",
        "subject_entity_id",
        "object_entity_id",
        "object_text",
        "place_entity_id",
        "time_text",
        "time_anchor_claim_id",
        "conflict_group_id",
        "created_by_user_id",
    )
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def validate_claim_shape(self):
        if self.claim_type == MemoirClaimType.RELATIONSHIP:
            if not self.subject_entity_id or not self.object_entity_id:
                raise ValueError("relationship claim requires subject and object entities")
        if (self.time_start is None) != (self.time_end is None):
            raise ValueError("normalized time range requires both time_start and time_end")
        if self.time_start is not None and self.time_end is not None and self.time_end < self.time_start:
            raise ValueError("time_end must not precede time_start")
        if self.created_by == MemoirNarrativeActorType.USER and not self.created_by_user_id:
            raise ValueError("user-created claim requires created_by_user_id")
        return self


class MemoirClaimEvidenceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1, max_length=36)
    claim_id: str = Field(min_length=1, max_length=36)
    evidence_role: MemoirClaimEvidenceRole
    evidence_source_type: MemoirClaimEvidenceSourceType
    utterance_id: str | None = Field(default=None, max_length=36)
    transcript_version_id: str | None = Field(default=None, max_length=36)
    audio_file_id: str | None = Field(default=None, max_length=36)
    image_asset_id: str | None = Field(default=None, max_length=36)
    speaker_entity_id: str | None = Field(default=None, max_length=36)
    speaker_role: MemoirClaimEvidenceSpeakerRole | None = None
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, gt=0)
    quote: str | None = Field(default=None, max_length=8000)
    evidence_note: str | None = Field(default=None, max_length=8000)
    source_text_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    created_by: MemoirNarrativeActorType
    created_by_user_id: str | None = Field(default=None, max_length=36)

    @field_validator(
        "project_id",
        "claim_id",
        "utterance_id",
        "transcript_version_id",
        "audio_file_id",
        "image_asset_id",
        "speaker_entity_id",
        "quote",
        "evidence_note",
        "source_text_hash",
        "created_by_user_id",
    )
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def validate_source_shape(self):
        if (self.start_ms is None) != (self.end_ms is None):
            raise ValueError("audio range requires both start_ms and end_ms")
        if self.start_ms is not None and self.end_ms is not None and self.end_ms <= self.start_ms:
            raise ValueError("end_ms must be greater than start_ms")

        transcript_only_fields = (
            self.utterance_id,
            self.audio_file_id,
            self.speaker_entity_id,
            self.speaker_role,
            self.start_ms,
            self.end_ms,
        )
        if self.evidence_source_type == MemoirClaimEvidenceSourceType.TRANSCRIPT:
            if not self.transcript_version_id or not self.quote or not self.source_text_hash:
                raise ValueError("transcript evidence requires version, quote, and source_text_hash")
            if self.image_asset_id:
                raise ValueError("transcript evidence cannot reference an image")
        elif self.evidence_source_type == MemoirClaimEvidenceSourceType.IMAGE:
            if not self.image_asset_id or not self.evidence_note:
                raise ValueError("image evidence requires image_asset_id and evidence_note")
            if self.transcript_version_id or self.quote or self.source_text_hash or any(transcript_only_fields):
                raise ValueError("image evidence cannot contain transcript source fields")
        else:
            if not self.evidence_note:
                raise ValueError("manual evidence requires evidence_note")
            if (
                self.transcript_version_id
                or self.image_asset_id
                or self.quote
                or self.source_text_hash
                or any(transcript_only_fields)
            ):
                raise ValueError("manual evidence cannot reference transcript or image sources")
            if self.created_by != MemoirNarrativeActorType.USER or not self.created_by_user_id:
                raise ValueError("manual evidence requires an explicit user actor")
        return self
