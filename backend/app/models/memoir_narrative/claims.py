from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
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
from app.models.memoir_common import _enum_values


class MemoirClaim(Base):
    __tablename__ = "memoir_claims"
    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_memoir_claims_project_id"),
        ForeignKeyConstraint(
            ["project_id", "subject_entity_id"],
            ["memoir_narrative_entities.project_id", "memoir_narrative_entities.id"],
            name="fk_memoir_claims_subject_project",
        ),
        ForeignKeyConstraint(
            ["project_id", "object_entity_id"],
            ["memoir_narrative_entities.project_id", "memoir_narrative_entities.id"],
            name="fk_memoir_claims_object_project",
        ),
        ForeignKeyConstraint(
            ["project_id", "place_entity_id"],
            ["memoir_narrative_entities.project_id", "memoir_narrative_entities.id"],
            name="fk_memoir_claims_place_project",
        ),
        ForeignKeyConstraint(
            ["project_id", "time_anchor_claim_id"],
            ["memoir_claims.project_id", "memoir_claims.id"],
            name="fk_memoir_claims_anchor_project",
        ),
        ForeignKeyConstraint(
            ["project_id", "superseded_by_claim_id"],
            ["memoir_claims.project_id", "memoir_claims.id"],
            name="fk_memoir_claims_superseded_project",
        ),
        CheckConstraint(
            f"claim_type IN ({_enum_values(MemoirClaimType)})",
            name="ck_memoir_claims_type",
        ),
        CheckConstraint(
            f"predicate_code IN ({_enum_values(MemoirClaimPredicate)})",
            name="ck_memoir_claims_predicate",
        ),
        CheckConstraint(
            f"time_precision IN ({_enum_values(MemoirClaimTimePrecision)})",
            name="ck_memoir_claims_time_precision",
        ),
        CheckConstraint(
            f"certainty IN ({_enum_values(MemoirClaimCertainty)})",
            name="ck_memoir_claims_certainty",
        ),
        CheckConstraint(
            f"verification_status IN ({_enum_values(MemoirClaimVerificationStatus)})",
            name="ck_memoir_claims_verification",
        ),
        CheckConstraint(
            f"publication_status IN ({_enum_values(MemoirClaimPublicationStatus)})",
            name="ck_memoir_claims_publication",
        ),
        CheckConstraint(
            f"created_by IN ({_enum_values(MemoirNarrativeActorType)})",
            name="ck_memoir_claims_created_by",
        ),
        CheckConstraint(
            f"updated_by IN ({_enum_values(MemoirNarrativeActorType)})",
            name="ck_memoir_claims_updated_by",
        ),
        CheckConstraint(
            "created_by != 'user' OR created_by_user_id IS NOT NULL",
            name="ck_memoir_claims_created_actor",
        ),
        CheckConstraint(
            "updated_by != 'user' OR updated_by_user_id IS NOT NULL",
            name="ck_memoir_claims_updated_actor",
        ),
        CheckConstraint(
            "verification_status NOT IN ('confirmed', 'rejected') OR "
            "(updated_by = 'user' AND updated_by_user_id IS NOT NULL)",
            name="ck_memoir_claims_user_verification_decision",
        ),
        CheckConstraint(
            "publication_status = 'eligible' OR "
            "(updated_by = 'user' AND updated_by_user_id IS NOT NULL)",
            name="ck_memoir_claims_user_publication_decision",
        ),
        CheckConstraint("length(trim(statement)) > 0", name="ck_memoir_claims_statement"),
        CheckConstraint(
            "(time_start IS NULL AND time_end IS NULL) OR "
            "(time_start IS NOT NULL AND time_end IS NOT NULL AND time_end >= time_start)",
            name="ck_memoir_claims_time_range",
        ),
        CheckConstraint(
            "claim_type != 'relationship' OR "
            "(subject_entity_id IS NOT NULL AND object_entity_id IS NOT NULL)",
            name="ck_memoir_claims_relationship_entities",
        ),
        CheckConstraint(
            "time_anchor_claim_id IS NULL OR time_anchor_claim_id != id",
            name="ck_memoir_claims_not_self_anchored",
        ),
        CheckConstraint(
            "superseded_by_claim_id IS NULL OR superseded_by_claim_id != id",
            name="ck_memoir_claims_not_self_superseded",
        ),
        Index(
            "ix_memoir_claims_project_verification_publication",
            "project_id",
            "verification_status",
            "publication_status",
        ),
        Index("ix_memoir_claims_project_subject", "project_id", "subject_entity_id"),
        Index("ix_memoir_claims_project_time", "project_id", "time_start", "time_end"),
        Index("ix_memoir_claims_project_conflict", "project_id", "conflict_group_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    claim_type: Mapped[str] = mapped_column(String(24), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    subject_entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    predicate_code: Mapped[str] = mapped_column(
        String(40),
        default=MemoirClaimPredicate.NARRATIVE_FACT.value,
        nullable=False,
    )
    object_entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    object_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    place_entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    time_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    time_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    time_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    time_precision: Mapped[str] = mapped_column(
        String(16),
        default=MemoirClaimTimePrecision.UNKNOWN.value,
        nullable=False,
    )
    time_anchor_claim_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    certainty: Mapped[str] = mapped_column(
        String(16),
        default=MemoirClaimCertainty.UNCERTAIN.value,
        nullable=False,
    )
    verification_status: Mapped[str] = mapped_column(
        String(24),
        default=MemoirClaimVerificationStatus.UNREVIEWED.value,
        nullable=False,
    )
    publication_status: Mapped[str] = mapped_column(
        String(16),
        default=MemoirClaimPublicationStatus.ELIGIBLE.value,
        nullable=False,
    )
    conflict_group_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    superseded_by_claim_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_by: Mapped[str] = mapped_column(String(12), nullable=False)
    created_by_user_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.id"),
        nullable=True,
    )
    updated_by: Mapped[str] = mapped_column(String(12), nullable=False)
    updated_by_user_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    evidence: Mapped[list["MemoirClaimEvidence"]] = relationship(
        "MemoirClaimEvidence",
        back_populates="claim",
        cascade="all, delete-orphan",
    )


class MemoirClaimEvidence(Base):
    __tablename__ = "memoir_claim_evidence"
    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_memoir_claim_evidence_project_id"),
        ForeignKeyConstraint(
            ["project_id", "claim_id"],
            ["memoir_claims.project_id", "memoir_claims.id"],
            ondelete="CASCADE",
            name="fk_memoir_claim_evidence_claim_project",
        ),
        ForeignKeyConstraint(
            ["project_id", "speaker_entity_id"],
            ["memoir_narrative_entities.project_id", "memoir_narrative_entities.id"],
            name="fk_memoir_claim_evidence_speaker_project",
        ),
        CheckConstraint(
            f"evidence_role IN ({_enum_values(MemoirClaimEvidenceRole)})",
            name="ck_memoir_claim_evidence_role",
        ),
        CheckConstraint(
            f"evidence_source_type IN ({_enum_values(MemoirClaimEvidenceSourceType)})",
            name="ck_memoir_claim_evidence_source_type",
        ),
        CheckConstraint(
            f"speaker_role IS NULL OR speaker_role IN ({_enum_values(MemoirClaimEvidenceSpeakerRole)})",
            name="ck_memoir_claim_evidence_speaker_role",
        ),
        CheckConstraint(
            f"created_by IN ({_enum_values(MemoirNarrativeActorType)})",
            name="ck_memoir_claim_evidence_created_by",
        ),
        CheckConstraint(
            "(start_ms IS NULL AND end_ms IS NULL) OR "
            "(start_ms IS NOT NULL AND end_ms IS NOT NULL AND start_ms >= 0 AND end_ms > start_ms)",
            name="ck_memoir_claim_evidence_time_range",
        ),
        CheckConstraint(
            "(evidence_source_type = 'transcript' "
            "AND transcript_version_id IS NOT NULL "
            "AND image_asset_id IS NULL "
            "AND quote IS NOT NULL AND length(trim(quote)) > 0 "
            "AND source_text_hash IS NOT NULL AND length(source_text_hash) = 64) "
            "OR (evidence_source_type = 'image' "
            "AND image_asset_id IS NOT NULL "
            "AND utterance_id IS NULL AND transcript_version_id IS NULL AND audio_file_id IS NULL "
            "AND speaker_entity_id IS NULL AND speaker_role IS NULL "
            "AND start_ms IS NULL AND end_ms IS NULL AND quote IS NULL AND source_text_hash IS NULL "
            "AND evidence_note IS NOT NULL AND length(trim(evidence_note)) > 0) "
            "OR (evidence_source_type = 'manual' "
            "AND utterance_id IS NULL AND transcript_version_id IS NULL AND audio_file_id IS NULL "
            "AND image_asset_id IS NULL AND speaker_entity_id IS NULL AND speaker_role IS NULL "
            "AND start_ms IS NULL AND end_ms IS NULL AND quote IS NULL AND source_text_hash IS NULL "
            "AND evidence_note IS NOT NULL AND length(trim(evidence_note)) > 0 "
            "AND created_by = 'user' AND created_by_user_id IS NOT NULL)",
            name="ck_memoir_claim_evidence_source_shape",
        ),
        Index("ix_memoir_claim_evidence_project_claim", "project_id", "claim_id"),
        Index("ix_memoir_claim_evidence_transcript", "transcript_version_id"),
        Index("ix_memoir_claim_evidence_utterance", "utterance_id"),
        Index("ix_memoir_claim_evidence_image", "image_asset_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    claim_id: Mapped[str] = mapped_column(String(36), nullable=False)
    evidence_role: Mapped[str] = mapped_column(String(16), nullable=False)
    evidence_source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    utterance_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("memoir_voice_utterances.id"),
        nullable=True,
    )
    transcript_version_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("memoir_transcript_versions.id"),
        nullable=True,
    )
    audio_file_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("audio_files.id"),
        nullable=True,
    )
    image_asset_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("memoir_image_assets.id"),
        nullable=True,
    )
    speaker_entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    speaker_role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    start_ms: Mapped[int | None] = mapped_column(nullable=True)
    end_ms: Mapped[int | None] = mapped_column(nullable=True)
    quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_text_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[str] = mapped_column(String(12), nullable=False)
    created_by_user_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    claim: Mapped[MemoirClaim] = relationship("MemoirClaim", back_populates="evidence")
