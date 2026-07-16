from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.domain.memoir.narrative_types import (
    MemoirNarrativeActorType,
    MemoirNarrativeDecisionAction,
    MemoirNarrativeDecisionTargetType,
)
from app.models.memoir_common import _enum_values


class MemoirNarrativeDecision(Base):
    __tablename__ = "memoir_narrative_decisions"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "idempotency_key",
            name="uq_memoir_narrative_decisions_project_idempotency",
        ),
        ForeignKeyConstraint(
            ["project_id", "source_followup_task_answer_id"],
            [
                "memoir_followup_task_answers.project_id",
                "memoir_followup_task_answers.id",
            ],
            name="fk_memoir_narrative_decisions_followup_source_project",
        ),
        CheckConstraint(
            f"target_type IN ({_enum_values(MemoirNarrativeDecisionTargetType)})",
            name="ck_memoir_narrative_decisions_target_type",
        ),
        CheckConstraint(
            f"action IN ({_enum_values(MemoirNarrativeDecisionAction)})",
            name="ck_memoir_narrative_decisions_action",
        ),
        CheckConstraint(
            f"actor_type IN ({_enum_values(MemoirNarrativeActorType)})",
            name="ck_memoir_narrative_decisions_actor_type",
        ),
        CheckConstraint(
            "actor_type != 'user' OR actor_user_id IS NOT NULL",
            name="ck_memoir_narrative_decisions_user_actor",
        ),
        CheckConstraint(
            "length(trim(target_id)) > 0",
            name="ck_memoir_narrative_decisions_target_id",
        ),
        CheckConstraint(
            "length(trim(idempotency_key)) > 0",
            name="ck_memoir_narrative_decisions_idempotency_key",
        ),
        CheckConstraint(
            "(source_followup_task_answer_id IS NULL "
            "AND source_transcript_version_id IS NULL) OR "
            "(source_followup_task_answer_id IS NOT NULL "
            "AND source_transcript_version_id IS NOT NULL)",
            name="ck_memoir_narrative_decisions_followup_source_shape",
        ),
        Index(
            "ix_memoir_narrative_decisions_project_target_created",
            "project_id",
            "target_type",
            "target_id",
            "created_at",
        ),
        Index(
            "ix_memoir_narrative_decisions_followup_source",
            "source_followup_task_answer_id",
        ),
        Index(
            "ix_memoir_narrative_decisions_transcript_source",
            "source_transcript_version_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str] = mapped_column(String(36), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(12), nullable=False)
    actor_user_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.id"),
        nullable=True,
    )
    source_followup_task_answer_id: Mapped[str | None] = mapped_column(
        String(36),
        nullable=True,
    )
    source_transcript_version_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey(
            "memoir_transcript_versions.id",
            name="fk_memoir_narrative_decisions_transcript_source",
        ),
        nullable=True,
    )
    before_state_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    after_state_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
