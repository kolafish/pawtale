from __future__ import annotations

from datetime import UTC, datetime
import uuid

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.domain.memoir.narrative_types import (
    MemoirInterviewAction,
    MemoirInterviewPolicyExecutionMode,
    MemoirInterviewResponseOutcome,
)
from app.models.memoir_common import _enum_values


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class MemoirInterviewPolicyDecision(Base):
    __tablename__ = "memoir_interview_policy_decisions"
    __table_args__ = (
        CheckConstraint(
            f"selected_action IN ({_enum_values(MemoirInterviewAction)})",
            name="ck_memoir_interview_policy_decisions_action",
        ),
        CheckConstraint(
            f"execution_mode IN ({_enum_values(MemoirInterviewPolicyExecutionMode)})",
            name="ck_memoir_interview_policy_decisions_mode",
        ),
        CheckConstraint(
            "user_response_outcome IS NULL OR "
            f"user_response_outcome IN ({_enum_values(MemoirInterviewResponseOutcome)})",
            name="ck_memoir_interview_policy_decisions_outcome",
        ),
        CheckConstraint(
            "human_rating IS NULL OR human_rating BETWEEN -1 AND 1",
            name="ck_memoir_interview_policy_decisions_rating",
        ),
        CheckConstraint(
            "length(brief_fingerprint) = 64",
            name="ck_memoir_interview_policy_decisions_fingerprint",
        ),
        CheckConstraint(
            "length(trim(reason_code)) > 0",
            name="ck_memoir_interview_policy_decisions_reason_code",
        ),
        CheckConstraint(
            "length(trim(policy_version)) > 0",
            name="ck_memoir_interview_policy_decisions_policy_version",
        ),
        Index(
            "ix_memoir_interview_policy_decisions_session_created",
            "session_id",
            "created_at",
        ),
        Index(
            "ix_memoir_interview_policy_decisions_generated_message",
            "generated_message_id",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("memoir_voice_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    trigger_message_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("memoir_chat_messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    brief_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    state_snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    candidate_actions_json: Mapped[list] = mapped_column(JSON, nullable=False)
    selected_action: Mapped[str] = mapped_column(String(40), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    reason_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    policy_version: Mapped[str] = mapped_column(String(40), nullable=False)
    model_provider: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    model_name: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    execution_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    generated_message_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("memoir_chat_messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    user_response_outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    human_rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class MemoirUtteranceInterviewAssignment(Base):
    __tablename__ = "memoir_utterance_interview_assignments"
    __table_args__ = (
        UniqueConstraint(
            "utterance_id",
            name="uq_memoir_utterance_interview_assignments_utterance",
        ),
        Index(
            "ix_memoir_utterance_interview_assignments_followup",
            "followup_task_id",
        ),
        Index(
            "ix_memoir_utterance_interview_assignments_policy",
            "policy_decision_id",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    utterance_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("memoir_voice_utterances.id", ondelete="CASCADE"),
        nullable=False,
    )
    followup_task_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("memoir_followup_tasks.id"),
        nullable=True,
    )
    policy_decision_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("memoir_interview_policy_decisions.id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
