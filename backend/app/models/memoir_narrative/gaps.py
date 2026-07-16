from __future__ import annotations

from datetime import UTC, datetime
import uuid

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.domain.memoir.narrative_types import (
    MemoirNarrativeActorType,
    MemoirNarrativeGapActionDecisionSource,
    MemoirNarrativeGapActionReasonCode,
    MemoirNarrativeGapClaimRole,
    MemoirNarrativeGapDetectedBy,
    MemoirNarrativeGapRecommendedAction,
    MemoirNarrativeGapScopeType,
    MemoirNarrativeGapSeverity,
    MemoirNarrativeGapStatus,
    MemoirNarrativeGapType,
)
from app.models.memoir_common import _enum_values


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class MemoirNarrativeGap(Base):
    __tablename__ = "memoir_narrative_gaps"
    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_memoir_narrative_gaps_project_id"),
        UniqueConstraint(
            "project_id",
            "fingerprint",
            name="uq_memoir_narrative_gaps_project_fingerprint",
        ),
        ForeignKeyConstraint(
            ["project_id", "primary_story_id"],
            ["memoir_stories.project_id", "memoir_stories.id"],
            name="fk_memoir_narrative_gaps_story_project",
        ),
        CheckConstraint(
            f"scope_type IN ({_enum_values(MemoirNarrativeGapScopeType)})",
            name="ck_memoir_narrative_gaps_scope_type",
        ),
        CheckConstraint(
            f"gap_type IN ({_enum_values(MemoirNarrativeGapType)})",
            name="ck_memoir_narrative_gaps_type",
        ),
        CheckConstraint(
            f"severity IN ({_enum_values(MemoirNarrativeGapSeverity)})",
            name="ck_memoir_narrative_gaps_severity",
        ),
        CheckConstraint(
            f"status IN ({_enum_values(MemoirNarrativeGapStatus)})",
            name="ck_memoir_narrative_gaps_status",
        ),
        CheckConstraint(
            f"recommended_action IN ({_enum_values(MemoirNarrativeGapRecommendedAction)})",
            name="ck_memoir_narrative_gaps_recommended_action",
        ),
        CheckConstraint(
            f"action_reason_code IN ({_enum_values(MemoirNarrativeGapActionReasonCode)})",
            name="ck_memoir_narrative_gaps_action_reason_code",
        ),
        CheckConstraint(
            f"action_decision_source IN ({_enum_values(MemoirNarrativeGapActionDecisionSource)})",
            name="ck_memoir_narrative_gaps_action_decision_source",
        ),
        CheckConstraint(
            "length(trim(action_policy_version)) > 0",
            name="ck_memoir_narrative_gaps_action_policy_version",
        ),
        CheckConstraint(
            f"detected_by IN ({_enum_values(MemoirNarrativeGapDetectedBy)})",
            name="ck_memoir_narrative_gaps_detected_by",
        ),
        CheckConstraint(
            f"resolved_by IS NULL OR resolved_by IN ({_enum_values(MemoirNarrativeActorType)})",
            name="ck_memoir_narrative_gaps_resolved_by",
        ),
        CheckConstraint(
            "(scope_type = 'story' AND primary_story_id IS NOT NULL "
            "AND primary_chapter_id IS NULL) OR "
            "(scope_type = 'chapter' AND primary_story_id IS NULL "
            "AND primary_chapter_id IS NOT NULL) OR "
            "(scope_type = 'book' AND primary_story_id IS NULL "
            "AND primary_chapter_id IS NULL)",
            name="ck_memoir_narrative_gaps_scope_shape",
        ),
        CheckConstraint(
            "(status = 'open' AND resolved_by IS NULL AND resolved_by_user_id IS NULL "
            "AND resolved_at IS NULL AND length(trim(resolution_note)) = 0) OR "
            "(status != 'open' AND resolved_by IS NOT NULL AND resolved_at IS NOT NULL "
            "AND (resolved_by != 'user' OR resolved_by_user_id IS NOT NULL))",
            name="ck_memoir_narrative_gaps_resolution_shape",
        ),
        CheckConstraint(
            "status != 'dismissed' OR length(trim(resolution_note)) > 0",
            name="ck_memoir_narrative_gaps_dismiss_reason",
        ),
        CheckConstraint("length(trim(fingerprint)) = 64", name="ck_memoir_narrative_gaps_fingerprint"),
        CheckConstraint("length(trim(title)) > 0", name="ck_memoir_narrative_gaps_title"),
        CheckConstraint("detection_count >= 1", name="ck_memoir_narrative_gaps_detection_count"),
        Index(
            "ix_memoir_narrative_gaps_project_status_severity",
            "project_id",
            "status",
            "severity",
        ),
        Index(
            "ix_memoir_narrative_gaps_project_chapter",
            "project_id",
            "primary_chapter_id",
            "status",
        ),
        Index(
            "ix_memoir_narrative_gaps_project_action",
            "project_id",
            "status",
            "recommended_action",
            "severity",
        ),
        Index(
            "ix_memoir_narrative_gaps_project_story",
            "project_id",
            "primary_story_id",
            "status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)
    primary_story_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    primary_chapter_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memoir_book_plan_chapters.id"), nullable=True
    )
    gap_type: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(12), nullable=False)
    target_slot: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), default=MemoirNarrativeGapStatus.OPEN.value, nullable=False
    )
    recommended_action: Mapped[str] = mapped_column(
        String(24),
        default=MemoirNarrativeGapRecommendedAction.MANUAL_REVIEW.value,
        server_default=text("'manual_review'"),
        nullable=False,
    )
    action_reason_code: Mapped[str] = mapped_column(
        String(48),
        default=MemoirNarrativeGapActionReasonCode.MIGRATION_REVIEW_REQUIRED.value,
        server_default=text("'migration_review_required'"),
        nullable=False,
    )
    action_decision_source: Mapped[str] = mapped_column(
        String(32),
        default=MemoirNarrativeGapActionDecisionSource.MIGRATION.value,
        server_default=text("'migration'"),
        nullable=False,
    )
    action_policy_version: Mapped[str] = mapped_column(
        String(24),
        default="migration-1",
        server_default=text("'migration-1'"),
        nullable=False,
    )
    detected_by: Mapped[str] = mapped_column(String(12), nullable=False)
    detection_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    resolution_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    resolved_by: Mapped[str | None] = mapped_column(String(12), nullable=True)
    resolved_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    first_detected_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    last_detected_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )

    claims: Mapped[list["MemoirNarrativeGapClaim"]] = relationship(
        "MemoirNarrativeGapClaim", cascade="all, delete-orphan"
    )
    stories: Mapped[list["MemoirNarrativeGapStory"]] = relationship(
        "MemoirNarrativeGapStory", cascade="all, delete-orphan"
    )
    chapters: Mapped[list["MemoirNarrativeGapChapter"]] = relationship(
        "MemoirNarrativeGapChapter", cascade="all, delete-orphan"
    )

    @property
    def involved_story_ids(self) -> list[str]:
        return sorted(relation.story_id for relation in self.stories)

    @property
    def involved_chapter_ids(self) -> list[str]:
        return sorted(relation.chapter_id for relation in self.chapters)


class MemoirNarrativeGapClaim(Base):
    __tablename__ = "memoir_narrative_gap_claims"
    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_memoir_narrative_gap_claims_project_id"),
        UniqueConstraint(
            "project_id", "gap_id", "claim_id", name="uq_memoir_narrative_gap_claims_relation"
        ),
        ForeignKeyConstraint(
            ["project_id", "gap_id"],
            ["memoir_narrative_gaps.project_id", "memoir_narrative_gaps.id"],
            ondelete="CASCADE",
            name="fk_memoir_narrative_gap_claims_gap_project",
        ),
        ForeignKeyConstraint(
            ["project_id", "claim_id"],
            ["memoir_claims.project_id", "memoir_claims.id"],
            name="fk_memoir_narrative_gap_claims_claim_project",
        ),
        CheckConstraint(
            f"role IN ({_enum_values(MemoirNarrativeGapClaimRole)})",
            name="ck_memoir_narrative_gap_claims_role",
        ),
        Index("ix_memoir_narrative_gap_claims_gap", "project_id", "gap_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    gap_id: Mapped[str] = mapped_column(String(36), nullable=False)
    claim_id: Mapped[str] = mapped_column(String(36), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class MemoirNarrativeGapStory(Base):
    __tablename__ = "memoir_narrative_gap_stories"
    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_memoir_narrative_gap_stories_project_id"),
        UniqueConstraint(
            "project_id", "gap_id", "story_id", name="uq_memoir_narrative_gap_stories_relation"
        ),
        ForeignKeyConstraint(
            ["project_id", "gap_id"],
            ["memoir_narrative_gaps.project_id", "memoir_narrative_gaps.id"],
            ondelete="CASCADE",
            name="fk_memoir_narrative_gap_stories_gap_project",
        ),
        ForeignKeyConstraint(
            ["project_id", "story_id"],
            ["memoir_stories.project_id", "memoir_stories.id"],
            name="fk_memoir_narrative_gap_stories_story_project",
        ),
        Index("ix_memoir_narrative_gap_stories_gap", "project_id", "gap_id"),
        Index(
            "ix_memoir_narrative_gap_stories_story",
            "project_id",
            "story_id",
            "gap_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    gap_id: Mapped[str] = mapped_column(String(36), nullable=False)
    story_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class MemoirNarrativeGapChapter(Base):
    __tablename__ = "memoir_narrative_gap_chapters"
    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_memoir_narrative_gap_chapters_project_id"),
        UniqueConstraint(
            "project_id",
            "gap_id",
            "chapter_id",
            name="uq_memoir_narrative_gap_chapters_relation",
        ),
        ForeignKeyConstraint(
            ["project_id", "gap_id"],
            ["memoir_narrative_gaps.project_id", "memoir_narrative_gaps.id"],
            ondelete="CASCADE",
            name="fk_memoir_narrative_gap_chapters_gap_project",
        ),
        Index("ix_memoir_narrative_gap_chapters_gap", "project_id", "gap_id"),
        Index(
            "ix_memoir_narrative_gap_chapters_chapter",
            "project_id",
            "chapter_id",
            "gap_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    gap_id: Mapped[str] = mapped_column(String(36), nullable=False)
    chapter_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("memoir_book_plan_chapters.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
