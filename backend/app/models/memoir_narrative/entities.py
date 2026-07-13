from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.domain.memoir.narrative_types import (
    MemoirNarrativeActorType,
    MemoirNarrativeEntityType,
    MemoirNarrativeEntityVerificationStatus,
)
from app.models.memoir_common import _enum_values


class MemoirNarrativeEntity(Base):
    __tablename__ = "memoir_narrative_entities"
    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_memoir_narrative_entities_project_id"),
        ForeignKeyConstraint(
            ["project_id", "merged_into_entity_id"],
            ["memoir_narrative_entities.project_id", "memoir_narrative_entities.id"],
            name="fk_memoir_narrative_entities_merged_project",
        ),
        CheckConstraint(
            f"entity_type IN ({_enum_values(MemoirNarrativeEntityType)})",
            name="ck_memoir_narrative_entities_type",
        ),
        CheckConstraint(
            f"verification_status IN ({_enum_values(MemoirNarrativeEntityVerificationStatus)})",
            name="ck_memoir_narrative_entities_verification",
        ),
        CheckConstraint(
            f"created_by IN ({_enum_values(MemoirNarrativeActorType)})",
            name="ck_memoir_narrative_entities_created_by",
        ),
        CheckConstraint(
            f"updated_by IN ({_enum_values(MemoirNarrativeActorType)})",
            name="ck_memoir_narrative_entities_updated_by",
        ),
        CheckConstraint(
            "created_by != 'user' OR created_by_user_id IS NOT NULL",
            name="ck_memoir_narrative_entities_created_actor",
        ),
        CheckConstraint(
            "updated_by != 'user' OR updated_by_user_id IS NOT NULL",
            name="ck_memoir_narrative_entities_updated_actor",
        ),
        CheckConstraint(
            "length(trim(canonical_name)) > 0",
            name="ck_memoir_narrative_entities_name",
        ),
        CheckConstraint(
            "merged_into_entity_id IS NULL OR merged_into_entity_id != id",
            name="ck_memoir_narrative_entities_not_self_merged",
        ),
        Index(
            "ix_memoir_narrative_entities_project_type_status",
            "project_id",
            "entity_type",
            "verification_status",
        ),
        Index(
            "ix_memoir_narrative_entities_project_name",
            "project_id",
            "canonical_name",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    entity_type: Mapped[str] = mapped_column(String(24), nullable=False)
    canonical_name: Mapped[str] = mapped_column(String(200), nullable=False)
    aliases_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    verification_status: Mapped[str] = mapped_column(
        String(24),
        default=MemoirNarrativeEntityVerificationStatus.UNREVIEWED.value,
        nullable=False,
    )
    merged_into_entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
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
