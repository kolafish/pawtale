from __future__ import annotations

from datetime import UTC, datetime
import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.domain.memoir.narrative_types import (
    MemoirClaimTimePrecision,
    MemoirNarrativeActorType,
    MemoirStoryLifecycleStatus,
    MemoirStoryNarrativeRole,
    MemoirStoryPublicationStatus,
    MemoirStoryRevisionChangeType,
    MemoirStoryType,
    MemoirStoryVerificationStatus,
)
from app.models.memoir_common import _enum_values


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class MemoirStory(Base):
    __tablename__ = "memoir_stories"
    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_memoir_stories_project_id"),
        ForeignKeyConstraint(
            ["project_id", "primary_place_entity_id"],
            ["memoir_narrative_entities.project_id", "memoir_narrative_entities.id"],
            name="fk_memoir_stories_place_project",
        ),
        ForeignKeyConstraint(
            ["project_id", "merged_into_story_id"],
            ["memoir_stories.project_id", "memoir_stories.id"],
            name="fk_memoir_stories_merged_project",
        ),
        CheckConstraint(f"story_type IN ({_enum_values(MemoirStoryType)})", name="ck_memoir_stories_type"),
        CheckConstraint(
            f"lifecycle_status IN ({_enum_values(MemoirStoryLifecycleStatus)})",
            name="ck_memoir_stories_lifecycle",
        ),
        CheckConstraint(
            f"verification_status IN ({_enum_values(MemoirStoryVerificationStatus)})",
            name="ck_memoir_stories_verification",
        ),
        CheckConstraint(
            f"publication_status IN ({_enum_values(MemoirStoryPublicationStatus)})",
            name="ck_memoir_stories_publication",
        ),
        CheckConstraint(
            f"time_precision IN ({_enum_values(MemoirClaimTimePrecision)})",
            name="ck_memoir_stories_time_precision",
        ),
        CheckConstraint(
            f"created_by IN ({_enum_values(MemoirNarrativeActorType)})",
            name="ck_memoir_stories_created_by",
        ),
        CheckConstraint(
            f"updated_by IN ({_enum_values(MemoirNarrativeActorType)})",
            name="ck_memoir_stories_updated_by",
        ),
        CheckConstraint(
            "created_by != 'user' OR created_by_user_id IS NOT NULL",
            name="ck_memoir_stories_created_actor",
        ),
        CheckConstraint(
            "updated_by != 'user' OR updated_by_user_id IS NOT NULL",
            name="ck_memoir_stories_updated_actor",
        ),
        CheckConstraint(
            "verification_status NOT IN ('confirmed', 'rejected') OR verification_decided_by_user_id IS NOT NULL",
            name="ck_memoir_stories_user_verification_decision",
        ),
        CheckConstraint(
            "publication_status = 'eligible' OR publication_decided_by_user_id IS NOT NULL",
            name="ck_memoir_stories_user_publication_decision",
        ),
        CheckConstraint("length(trim(title)) > 0", name="ck_memoir_stories_title"),
        CheckConstraint("length(trim(title_normalized)) > 0", name="ck_memoir_stories_normalized_title"),
        CheckConstraint("version >= 1", name="ck_memoir_stories_version"),
        CheckConstraint(
            "(time_start IS NULL AND time_end IS NULL) OR "
            "(time_start IS NOT NULL AND time_end IS NOT NULL AND time_end >= time_start)",
            name="ck_memoir_stories_time_range",
        ),
        CheckConstraint(
            "(lifecycle_status = 'merged' AND merged_into_story_id IS NOT NULL) OR "
            "(lifecycle_status != 'merged' AND merged_into_story_id IS NULL)",
            name="ck_memoir_stories_merge_shape",
        ),
        CheckConstraint(
            "merged_into_story_id IS NULL OR merged_into_story_id != id",
            name="ck_memoir_stories_not_self_merged",
        ),
        Index("ix_memoir_stories_project_lifecycle", "project_id", "lifecycle_status"),
        Index("ix_memoir_stories_project_type_title", "project_id", "story_type", "title_normalized"),
        Index("ix_memoir_stories_project_status", "project_id", "verification_status", "publication_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    title_normalized: Mapped[str] = mapped_column(String(240), nullable=False)
    story_type: Mapped[str] = mapped_column(String(24), nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    time_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    time_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    time_precision: Mapped[str] = mapped_column(String(16), nullable=False)
    primary_place_entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lifecycle_status: Mapped[str] = mapped_column(String(16), nullable=False)
    verification_status: Mapped[str] = mapped_column(String(24), nullable=False)
    verification_decided_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    publication_status: Mapped[str] = mapped_column(String(16), nullable=False)
    publication_decided_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    merged_into_story_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_by: Mapped[str] = mapped_column(String(12), nullable=False)
    created_by_user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)
    updated_by: Mapped[str] = mapped_column(String(12), nullable=False)
    updated_by_user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    claims: Mapped[list["MemoirStoryClaim"]] = relationship(
        "MemoirStoryClaim", back_populates="story", cascade="all, delete-orphan"
    )
    revisions: Mapped[list["MemoirStoryRevision"]] = relationship(
        "MemoirStoryRevision", back_populates="story", cascade="all, delete-orphan"
    )


class MemoirStoryRevision(Base):
    __tablename__ = "memoir_story_revisions"
    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_memoir_story_revisions_project_id"),
        UniqueConstraint("project_id", "story_id", "story_version", name="uq_memoir_story_revisions_version"),
        ForeignKeyConstraint(
            ["project_id", "story_id"],
            ["memoir_stories.project_id", "memoir_stories.id"],
            ondelete="CASCADE",
            name="fk_memoir_story_revisions_story_project",
        ),
        CheckConstraint(
            f"change_type IN ({_enum_values(MemoirStoryRevisionChangeType)})",
            name="ck_memoir_story_revisions_change_type",
        ),
        CheckConstraint(
            f"created_by IN ({_enum_values(MemoirNarrativeActorType)})",
            name="ck_memoir_story_revisions_created_by",
        ),
        CheckConstraint(
            "created_by != 'user' OR created_by_user_id IS NOT NULL",
            name="ck_memoir_story_revisions_created_actor",
        ),
        CheckConstraint("story_version >= 1", name="ck_memoir_story_revisions_version"),
        Index(
            "uq_memoir_story_revisions_project_story_id",
            "project_id",
            "story_id",
            "id",
            unique=True,
        ),
        Index("ix_memoir_story_revisions_project_story", "project_id", "story_id", "story_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    story_id: Mapped[str] = mapped_column(String(36), nullable=False)
    story_version: Mapped[int] = mapped_column(Integer, nullable=False)
    change_type: Mapped[str] = mapped_column(String(24), nullable=False)
    before_state_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    after_state_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_by: Mapped[str] = mapped_column(String(12), nullable=False)
    created_by_user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    story: Mapped[MemoirStory] = relationship("MemoirStory", back_populates="revisions")


class MemoirStoryClaim(Base):
    __tablename__ = "memoir_story_claims"
    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_memoir_story_claims_project_id"),
        UniqueConstraint("project_id", "story_id", "claim_id", name="uq_memoir_story_claims_story_claim"),
        ForeignKeyConstraint(
            ["project_id", "story_id"],
            ["memoir_stories.project_id", "memoir_stories.id"],
            ondelete="CASCADE",
            name="fk_memoir_story_claims_story_project",
        ),
        ForeignKeyConstraint(
            ["project_id", "claim_id"],
            ["memoir_claims.project_id", "memoir_claims.id"],
            ondelete="CASCADE",
            name="fk_memoir_story_claims_claim_project",
        ),
        CheckConstraint(
            f"narrative_role IN ({_enum_values(MemoirStoryNarrativeRole)})",
            name="ck_memoir_story_claims_role",
        ),
        CheckConstraint(
            f"created_by IN ({_enum_values(MemoirNarrativeActorType)})",
            name="ck_memoir_story_claims_created_by",
        ),
        CheckConstraint(
            "created_by != 'user' OR created_by_user_id IS NOT NULL",
            name="ck_memoir_story_claims_created_actor",
        ),
        CheckConstraint("sort_order >= 0", name="ck_memoir_story_claims_sort_order"),
        Index("ix_memoir_story_claims_project_story", "project_id", "story_id", "sort_order"),
        Index("ix_memoir_story_claims_project_claim", "project_id", "claim_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    story_id: Mapped[str] = mapped_column(String(36), nullable=False)
    claim_id: Mapped[str] = mapped_column(String(36), nullable=False)
    narrative_role: Mapped[str] = mapped_column(String(24), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_core: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by: Mapped[str] = mapped_column(String(12), nullable=False)
    created_by_user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    story: Mapped[MemoirStory] = relationship("MemoirStory", back_populates="claims")
