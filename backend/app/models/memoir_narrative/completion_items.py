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
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.domain.memoir.narrative_types import (
    MemoirCompletionItemStatus,
    MemoirNarrativeGapDetectedBy,
    MemoirNarrativeGapRecommendedAction,
    MemoirNarrativeGapScopeType,
    MemoirNarrativeGapSeverity,
    MemoirNarrativeGapType,
)
from app.models.memoir_common import _enum_values


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class MemoirCompletionItem(Base):
    """One actionable chapter/book issue, including its question and outcome."""

    __tablename__ = "memoir_completion_items"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "fingerprint",
            name="uq_memoir_completion_items_project_fingerprint",
        ),
        ForeignKeyConstraint(
            ["project_id", "story_id"],
            ["memoir_stories.project_id", "memoir_stories.id"],
            name="fk_memoir_completion_items_story_project",
        ),
        ForeignKeyConstraint(
            ["project_id", "claim_id"],
            ["memoir_claims.project_id", "memoir_claims.id"],
            name="fk_memoir_completion_items_claim_project",
        ),
        ForeignKeyConstraint(
            ["project_id", "related_claim_id"],
            ["memoir_claims.project_id", "memoir_claims.id"],
            name="fk_memoir_completion_items_related_claim_project",
        ),
        CheckConstraint(
            f"scope_type IN ({_enum_values(MemoirNarrativeGapScopeType)})",
            name="ck_memoir_completion_items_scope",
        ),
        CheckConstraint(
            f"item_type IN ({_enum_values(MemoirNarrativeGapType)})",
            name="ck_memoir_completion_items_type",
        ),
        CheckConstraint(
            f"severity IN ({_enum_values(MemoirNarrativeGapSeverity)})",
            name="ck_memoir_completion_items_severity",
        ),
        CheckConstraint(
            f"action IN ({_enum_values(MemoirNarrativeGapRecommendedAction)})",
            name="ck_memoir_completion_items_action",
        ),
        CheckConstraint(
            f"status IN ({_enum_values(MemoirCompletionItemStatus)})",
            name="ck_memoir_completion_items_status",
        ),
        CheckConstraint(
            f"detected_by IN ({_enum_values(MemoirNarrativeGapDetectedBy)})",
            name="ck_memoir_completion_items_detected_by",
        ),
        CheckConstraint("priority >= 0", name="ck_memoir_completion_items_priority"),
        CheckConstraint(
            "length(trim(fingerprint)) = 64",
            name="ck_memoir_completion_items_fingerprint",
        ),
        CheckConstraint(
            "length(trim(title)) > 0",
            name="ck_memoir_completion_items_title",
        ),
        Index(
            "ix_memoir_completion_items_project_status",
            "project_id",
            "status",
            "priority",
        ),
        Index(
            "ix_memoir_completion_items_project_chapter",
            "project_id",
            "chapter_id",
            "status",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)
    chapter_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("memoir_book_plan_chapters.id", ondelete="CASCADE"),
        nullable=True,
    )
    story_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    claim_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    related_claim_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    item_type: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(12), nullable=False)
    dimension: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    question: Mapped[str] = mapped_column(Text, default="", nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), default=MemoirCompletionItemStatus.OPEN.value, nullable=False
    )
    defer_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    outcome_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    detected_by: Mapped[str] = mapped_column(String(12), nullable=False)
    detection_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    first_detected_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, nullable=False
    )
    last_detected_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )
