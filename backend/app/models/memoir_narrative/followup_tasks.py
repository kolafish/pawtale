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
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.domain.memoir.narrative_types import (
    MemoirFollowupAnswerInterpretationStatus,
    MemoirFollowupAnswerUse,
    MemoirFollowupTaskClaimRole,
    MemoirFollowupTaskStatus,
    MemoirFollowupTaskUtteranceRole,
    MemoirNarrativeActorType,
)
from app.models.memoir_common import _enum_values


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class MemoirFollowupTask(Base):
    __tablename__ = "memoir_followup_tasks"
    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_memoir_followup_tasks_project_id"),
        UniqueConstraint("project_id", "gap_id", name="uq_memoir_followup_tasks_gap"),
        ForeignKeyConstraint(
            ["project_id", "gap_id"],
            ["memoir_narrative_gaps.project_id", "memoir_narrative_gaps.id"],
            ondelete="CASCADE",
            name="fk_memoir_followup_tasks_gap_project",
        ),
        ForeignKeyConstraint(
            ["project_id", "story_id"],
            ["memoir_stories.project_id", "memoir_stories.id"],
            name="fk_memoir_followup_tasks_story_project",
        ),
        CheckConstraint(
            f"status IN ({_enum_values(MemoirFollowupTaskStatus)})",
            name="ck_memoir_followup_tasks_status",
        ),
        CheckConstraint(
            f"created_by IN ({_enum_values(MemoirNarrativeActorType)})",
            name="ck_memoir_followup_tasks_created_by",
        ),
        CheckConstraint("priority >= 0", name="ck_memoir_followup_tasks_priority"),
        CheckConstraint("length(trim(question)) > 0", name="ck_memoir_followup_tasks_question"),
        CheckConstraint("length(trim(purpose)) > 0", name="ck_memoir_followup_tasks_purpose"),
        CheckConstraint(
            "(status = 'pending' AND assigned_session_id IS NULL "
            "AND started_at IS NULL AND completed_at IS NULL "
            "AND defer_until IS NULL AND length(trim(defer_reason)) = 0) OR "
            "(status = 'in_progress' AND assigned_session_id IS NOT NULL "
            "AND started_at IS NOT NULL AND completed_at IS NULL "
            "AND defer_until IS NULL AND length(trim(defer_reason)) = 0) OR "
            "(status = 'deferred' AND assigned_session_id IS NULL "
            "AND completed_at IS NULL AND length(trim(defer_reason)) > 0) OR "
            "(status IN ('answered', 'cannot_recall', 'excluded', 'cancelled') "
            "AND completed_at IS NOT NULL AND defer_until IS NULL "
            "AND length(trim(defer_reason)) = 0)",
            name="ck_memoir_followup_tasks_state_shape",
        ),
        Index("ix_memoir_followup_tasks_project_status", "project_id", "status", "priority"),
        Index("ix_memoir_followup_tasks_project_chapter", "project_id", "chapter_id", "status"),
        Index("ix_memoir_followup_tasks_project_story", "project_id", "story_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    gap_id: Mapped[str] = mapped_column(String(36), nullable=False)
    chapter_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memoir_book_plan_chapters.id"), nullable=True
    )
    story_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    target_slot: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    answer_use: Mapped[str] = mapped_column(
        String(24), default=MemoirFollowupAnswerUse.DIRECT_NARRATIVE.value, nullable=False
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    known_context_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), default=MemoirFollowupTaskStatus.PENDING.value, nullable=False
    )
    assigned_session_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memoir_voice_sessions.id", ondelete="SET NULL"), nullable=True
    )
    defer_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    defer_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_by: Mapped[str] = mapped_column(String(12), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )

    claims: Mapped[list["MemoirFollowupTaskClaim"]] = relationship(
        "MemoirFollowupTaskClaim", cascade="all, delete-orphan"
    )
    utterances: Mapped[list["MemoirFollowupTaskUtterance"]] = relationship(
        "MemoirFollowupTaskUtterance", cascade="all, delete-orphan"
    )


class MemoirFollowupTaskClaim(Base):
    __tablename__ = "memoir_followup_task_claims"
    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_memoir_followup_task_claims_project_id"),
        UniqueConstraint(
            "project_id", "task_id", "claim_id", name="uq_memoir_followup_task_claims_relation"
        ),
        ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["memoir_followup_tasks.project_id", "memoir_followup_tasks.id"],
            ondelete="CASCADE",
            name="fk_memoir_followup_task_claims_task_project",
        ),
        ForeignKeyConstraint(
            ["project_id", "claim_id"],
            ["memoir_claims.project_id", "memoir_claims.id"],
            name="fk_memoir_followup_task_claims_claim_project",
        ),
        CheckConstraint(
            f"context_role IN ({_enum_values(MemoirFollowupTaskClaimRole)})",
            name="ck_memoir_followup_task_claims_role",
        ),
        Index("ix_memoir_followup_task_claims_task", "project_id", "task_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    task_id: Mapped[str] = mapped_column(String(36), nullable=False)
    claim_id: Mapped[str] = mapped_column(String(36), nullable=False)
    context_role: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class MemoirFollowupTaskUtterance(Base):
    __tablename__ = "memoir_followup_task_utterances"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "id", name="uq_memoir_followup_task_utterances_project_id"
        ),
        UniqueConstraint(
            "project_id",
            "task_id",
            "utterance_id",
            name="uq_memoir_followup_task_utterances_relation",
        ),
        ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["memoir_followup_tasks.project_id", "memoir_followup_tasks.id"],
            ondelete="CASCADE",
            name="fk_memoir_followup_task_utterances_task_project",
        ),
        CheckConstraint(
            f"result_role IN ({_enum_values(MemoirFollowupTaskUtteranceRole)})",
            name="ck_memoir_followup_task_utterances_role",
        ),
        Index("ix_memoir_followup_task_utterances_task", "project_id", "task_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    task_id: Mapped[str] = mapped_column(String(36), nullable=False)
    utterance_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("memoir_voice_utterances.id"), nullable=False
    )
    result_role: Mapped[str] = mapped_column(String(24), nullable=False)
    is_assignment_snapshot: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    interpretation_status: Mapped[str] = mapped_column(
        String(24),
        default=MemoirFollowupAnswerInterpretationStatus.PENDING.value,
        nullable=False,
    )
    answer_kind: Mapped[str | None] = mapped_column(String(24), nullable=True)
    direct_narrative_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    clarification_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    fact_resolution_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    confirmation_question: Mapped[str] = mapped_column(Text, default="", nullable=False)
    interpretation_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
