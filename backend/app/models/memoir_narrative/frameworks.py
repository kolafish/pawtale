from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
import uuid

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.clock import utcnow_naive
from app.database import Base
from app.domain.memoir.narrative_types import (
    MemoirFrameworkEvidenceState,
    MemoirFrameworkMatchSource,
    MemoirFrameworkUserResolution,
)
from app.models.memoir_common import _enum_values

if TYPE_CHECKING:
    from app.models.memoir import MemoirVoiceUtterance


class MemoirProjectFramework(Base):
    __tablename__ = "memoir_project_frameworks"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            name="uq_memoir_project_frameworks_project",
        ),
        CheckConstraint(
            "length(trim(template_key)) > 0",
            name="ck_memoir_project_frameworks_template_key",
        ),
        CheckConstraint(
            "length(trim(template_version)) > 0",
            name="ck_memoir_project_frameworks_template_version",
        ),
        CheckConstraint(
            "length(template_hash) = 64",
            name="ck_memoir_project_frameworks_template_hash",
        ),
        Index(
            "ix_memoir_project_frameworks_template",
            "template_key",
            "template_version",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    template_key: Mapped[str] = mapped_column(String(64), nullable=False)
    template_version: Mapped[str] = mapped_column(String(24), nullable=False)
    template_snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    template_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )

    items: Mapped[list["MemoirFrameworkItem"]] = relationship(
        "MemoirFrameworkItem",
        back_populates="project_framework",
        cascade="all, delete-orphan",
        order_by="MemoirFrameworkItem.sort_order",
    )


class MemoirFrameworkItem(Base):
    __tablename__ = "memoir_framework_items"
    __table_args__ = (
        UniqueConstraint(
            "project_framework_id",
            "item_key",
            name="uq_memoir_framework_items_framework_key",
        ),
        CheckConstraint(
            f"evidence_state IN ({_enum_values(MemoirFrameworkEvidenceState)})",
            name="ck_memoir_framework_items_evidence_state",
        ),
        CheckConstraint(
            f"user_resolution IN ({_enum_values(MemoirFrameworkUserResolution)})",
            name="ck_memoir_framework_items_user_resolution",
        ),
        CheckConstraint(
            "length(trim(item_key)) > 0",
            name="ck_memoir_framework_items_item_key",
        ),
        CheckConstraint(
            "length(trim(module_key)) > 0",
            name="ck_memoir_framework_items_module_key",
        ),
        CheckConstraint(
            "sort_order >= 0",
            name="ck_memoir_framework_items_sort_order",
        ),
        CheckConstraint(
            "(user_resolution = 'none' "
            "AND resolved_by_user_id IS NULL AND resolved_at IS NULL) OR "
            "(user_resolution != 'none' "
            "AND resolved_at IS NOT NULL)",
            name="ck_memoir_framework_items_resolution_shape",
        ),
        CheckConstraint(
            "(deferred_until IS NULL AND length(trim(defer_reason)) = 0) OR "
            "(deferred_until IS NOT NULL AND length(trim(defer_reason)) > 0)",
            name="ck_memoir_framework_items_defer_shape",
        ),
        Index(
            "ix_memoir_framework_items_framework_state",
            "project_framework_id",
            "evidence_state",
            "user_resolution",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    project_framework_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("memoir_project_frameworks.id", ondelete="CASCADE"),
        nullable=False,
    )
    item_key: Mapped[str] = mapped_column(String(64), nullable=False)
    module_key: Mapped[str] = mapped_column(String(64), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_state: Mapped[str] = mapped_column(
        String(24),
        default=MemoirFrameworkEvidenceState.NONE.value,
        nullable=False,
    )
    user_resolution: Mapped[str] = mapped_column(
        String(24),
        default=MemoirFrameworkUserResolution.NONE.value,
        nullable=False,
    )
    assessment_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    assessment_version: Mapped[str] = mapped_column(
        String(40), default="", nullable=False
    )
    user_resolution_note: Mapped[str] = mapped_column(
        Text, default="", nullable=False
    )
    resolved_by_user_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deferred_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    defer_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )

    project_framework: Mapped[MemoirProjectFramework] = relationship(
        "MemoirProjectFramework",
        back_populates="items",
    )
    materials: Mapped[list["MemoirFrameworkItemMaterial"]] = relationship(
        "MemoirFrameworkItemMaterial",
        back_populates="framework_item",
        cascade="all, delete-orphan",
    )


class MemoirFrameworkItemMaterial(Base):
    __tablename__ = "memoir_framework_item_materials"
    __table_args__ = (
        CheckConstraint(
            f"match_source IN ({_enum_values(MemoirFrameworkMatchSource)})",
            name="ck_memoir_framework_item_materials_match_source",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_memoir_framework_item_materials_confidence",
        ),
        CheckConstraint(
            "(match_source = 'manual' AND matcher_version IS NULL "
            "AND confidence IS NULL) OR "
            "(match_source != 'manual' AND matcher_version IS NOT NULL "
            "AND length(trim(matcher_version)) > 0 AND confidence IS NOT NULL)",
            name="ck_memoir_framework_item_materials_match_shape",
        ),
        Index(
            "ix_memoir_framework_item_materials_utterance",
            "utterance_id",
        ),
        Index(
            "ix_memoir_framework_item_materials_matcher",
            "match_source",
            "matcher_version",
        ),
    )

    framework_item_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("memoir_framework_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    utterance_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("memoir_voice_utterances.id", ondelete="CASCADE"),
        primary_key=True,
    )
    match_source: Mapped[str] = mapped_column(String(16), nullable=False)
    matcher_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )

    framework_item: Mapped[MemoirFrameworkItem] = relationship(
        "MemoirFrameworkItem",
        back_populates="materials",
    )
    utterance: Mapped["MemoirVoiceUtterance"] = relationship(
        "MemoirVoiceUtterance",
    )
