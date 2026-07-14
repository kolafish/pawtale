from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MemoirNarrativeEntityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    entity_type: str
    canonical_name: str
    aliases: list[str] = Field(validation_alias="aliases_json")
    description: str
    verification_status: str
    merged_into_entity_id: str | None
    created_by: str
    created_by_user_id: str | None
    updated_at: datetime


class MemoirClaimEvidenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    evidence_role: str
    evidence_source_type: str
    utterance_id: str | None
    transcript_version_id: str | None
    audio_file_id: str | None
    image_asset_id: str | None
    speaker_entity_id: str | None
    speaker_role: str | None
    start_ms: int | None
    end_ms: int | None
    quote: str | None
    evidence_note: str | None
    created_at: datetime


class MemoirClaimResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    claim_type: str
    statement: str
    subject_entity_id: str | None
    predicate_code: str
    object_entity_id: str | None
    object_text: str | None
    place_entity_id: str | None
    time_text: str | None
    time_start: datetime | None
    time_end: datetime | None
    time_precision: str
    time_anchor_claim_id: str | None
    certainty: str
    verification_status: str
    publication_status: str
    conflict_group_id: str | None
    superseded_by_claim_id: str | None
    evidence: list[MemoirClaimEvidenceResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class MemoirClaimSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    claim_type: str
    statement: str
    subject_entity_id: str | None
    predicate_code: str
    object_entity_id: str | None
    object_text: str | None
    place_entity_id: str | None
    time_text: str | None
    time_start: datetime | None
    time_end: datetime | None
    time_precision: str
    certainty: str
    verification_status: str
    publication_status: str
    superseded_by_claim_id: str | None
    created_at: datetime
    updated_at: datetime


class MemoirNarrativeDecisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    target_type: str
    target_id: str
    action: str
    actor_type: str
    actor_user_id: str | None
    source_followup_task_utterance_id: str | None
    source_transcript_version_id: str | None
    before_state_json: dict
    after_state_json: dict
    reason: str
    idempotency_key: str
    created_at: datetime
