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
from sqlalchemy.orm import Mapped, mapped_column, relationship as orm_relationship

from app.clock import utcnow_naive
from app.database import Base
from app.domain.memoir.types import (
    MemoirAutomationEventStatus,
    MemoirAutomationEventType,
    MemoirBookCompletionJobScope,
    MemoirBookCompletionJobStatus,
    MemoirEditingJobStatus,
    MemoirEditingJobType,
)
from app.models.memoir_common import _enum_values

if TYPE_CHECKING:
    from app.models.memoir import MemoirBookPlanChapter
    from app.models.projects import Project


class MemoirEditingJob(Base):
    __tablename__ = "memoir_editing_jobs"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_memoir_editing_jobs_dedupe_key"),
        CheckConstraint(
            f"job_type IN ({_enum_values(MemoirEditingJobType)})",
            name="ck_memoir_editing_jobs_type",
        ),
        CheckConstraint(
            f"status IN ({_enum_values(MemoirEditingJobStatus)})",
            name="ck_memoir_editing_jobs_status",
        ),
        Index(
            "ix_memoir_editing_jobs_chapter_lookup",
            "project_id",
            "chapter_id",
            "job_type",
            "updated_at",
        ),
        Index(
            "ix_memoir_editing_jobs_pending_due",
            "status",
            "run_after",
        ),
        Index(
            "ix_memoir_editing_jobs_running_lease",
            "status",
            "lease_expires_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    chapter_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("memoir_book_plan_chapters.id"),
        nullable=True,
        index=True,
    )
    job_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(20),
        default=MemoirEditingJobStatus.PENDING.value,
        nullable=False,
        index=True,
    )
    run_after: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    dedupe_key: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    result_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)

    project: Mapped["Project"] = orm_relationship("Project")
    chapter: Mapped["MemoirBookPlanChapter | None"] = orm_relationship("MemoirBookPlanChapter")


class MemoirBookCompletionJob(Base):
    __tablename__ = "memoir_book_completion_jobs"
    __table_args__ = (
        CheckConstraint(
            f"scope IN ({_enum_values(MemoirBookCompletionJobScope)})",
            name="ck_memoir_book_completion_jobs_scope",
        ),
        CheckConstraint(
            f"status IN ({_enum_values(MemoirBookCompletionJobStatus)})",
            name="ck_memoir_book_completion_jobs_status",
        ),
        UniqueConstraint(
            "active_key",
            name="uq_memoir_book_completion_jobs_active_key",
        ),
        Index(
            "ix_memoir_book_completion_jobs_project_timeline",
            "project_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    active_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    scope: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        default=MemoirBookCompletionJobStatus.PENDING.value,
        nullable=False,
        index=True,
    )
    requested_ai_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    target_chapters_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    chapter_job_ids_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    enqueue_errors_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    chapter_results_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)

    project: Mapped["Project"] = orm_relationship("Project")


class MemoirAutomationEvent(Base):
    __tablename__ = "memoir_automation_events"
    __table_args__ = (
        CheckConstraint(
            f"event_type IN ({_enum_values(MemoirAutomationEventType)})",
            name="ck_memoir_automation_events_event_type",
        ),
        CheckConstraint(
            f"status IN ({_enum_values(MemoirAutomationEventStatus)})",
            name="ck_memoir_automation_events_status",
        ),
        Index(
            "ix_memoir_automation_events_project_timeline",
            "project_id",
            "event_type",
            "status",
            "created_at",
        ),
        Index(
            "ix_memoir_automation_events_editing_job",
            "editing_job_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    chapter_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("memoir_book_plan_chapters.id"),
        nullable=True,
        index=True,
    )
    editing_job_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("memoir_editing_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(30),
        default=MemoirAutomationEventStatus.COMPLETED.value,
        nullable=False,
        index=True,
    )
    utterance_ids_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    summary_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)

    project: Mapped["Project"] = orm_relationship("Project")
    chapter: Mapped["MemoirBookPlanChapter | None"] = orm_relationship("MemoirBookPlanChapter")
