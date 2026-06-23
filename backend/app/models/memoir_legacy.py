from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship as orm_relationship

from app.database import Base
from app.domain.memoir.types import (
    MemoirAnswerStatus,
    MemoirCreatedBy,
    MemoirDraftStatus,
    MemoirOutputStatus,
    MemoirOutputType,
    MemoirQuestionSource,
    MemoirQuestionStatus,
    MemoirSessionStatus,
)
from app.models.memoir_common import AudioFile, _enum_values


class MemoirSession(Base):
    __tablename__ = "memoir_sessions"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_enum_values(MemoirSessionStatus)})",
            name="ck_memoir_sessions_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        default=MemoirSessionStatus.ACTIVE.value,
        nullable=False,
    )
    current_question_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey(
            "memoir_questions.id",
            name="fk_memoir_sessions_current_question_id",
            use_alter=True,
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    project: Mapped["Project"] = orm_relationship("Project", back_populates="memoir_sessions")
    questions: Mapped[list["MemoirQuestion"]] = orm_relationship(
        "MemoirQuestion",
        back_populates="session",
        cascade="all, delete-orphan",
        foreign_keys="MemoirQuestion.session_id",
        order_by="MemoirQuestion.sort_order",
    )
    current_question: Mapped["MemoirQuestion | None"] = orm_relationship(
        "MemoirQuestion",
        foreign_keys=[current_question_id],
        post_update=True,
    )


class MemoirQuestion(Base):
    __tablename__ = "memoir_questions"
    __table_args__ = (
        CheckConstraint(
            f"source IN ({_enum_values(MemoirQuestionSource)})",
            name="ck_memoir_questions_source",
        ),
        CheckConstraint(
            f"status IN ({_enum_values(MemoirQuestionStatus)})",
            name="ck_memoir_questions_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("memoir_sessions.id"),
        nullable=False,
        index=True,
    )
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(
        String(20),
        default=MemoirQuestionSource.TEMPLATE.value,
        nullable=False,
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        default=MemoirQuestionStatus.PENDING.value,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    session: Mapped[MemoirSession] = orm_relationship(
        "MemoirSession",
        back_populates="questions",
        foreign_keys=[session_id],
    )
    answer: Mapped["MemoirAnswer | None"] = orm_relationship(
        "MemoirAnswer",
        back_populates="question",
        cascade="all, delete-orphan",
        uselist=False,
    )


class MemoirAnswer(Base):
    __tablename__ = "memoir_answers"
    __table_args__ = (
        UniqueConstraint("question_id", name="uq_memoir_answers_question_id"),
        CheckConstraint(
            f"status IN ({_enum_values(MemoirAnswerStatus)})",
            name="ck_memoir_answers_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    question_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("memoir_questions.id"),
        nullable=False,
        index=True,
    )
    audio_file_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("audio_files.id"),
        nullable=True,
    )
    transcript_raw: Mapped[str] = mapped_column(Text, default="", nullable=False)
    transcript_edited: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        default=MemoirAnswerStatus.ANSWERED.value,
        nullable=False,
    )
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    question: Mapped[MemoirQuestion] = orm_relationship("MemoirQuestion", back_populates="answer")
    audio_file: Mapped[AudioFile | None] = orm_relationship(
        "AudioFile",
        cascade="all, delete-orphan",
        single_parent=True,
    )
    draft: Mapped["MemoirDraft | None"] = orm_relationship(
        "MemoirDraft",
        back_populates="answer",
        cascade="all, delete-orphan",
        uselist=False,
    )


class MemoirDraft(Base):
    __tablename__ = "memoir_drafts"
    __table_args__ = (
        UniqueConstraint("answer_id", name="uq_memoir_drafts_answer_id"),
        CheckConstraint(
            f"status IN ({_enum_values(MemoirDraftStatus)})",
            name="ck_memoir_drafts_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    answer_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("memoir_answers.id"),
        nullable=False,
        index=True,
    )
    content_raw: Mapped[str] = mapped_column(Text, default="", nullable=False)
    content_edited: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        default=MemoirDraftStatus.DRAFT.value,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    answer: Mapped[MemoirAnswer] = orm_relationship("MemoirAnswer", back_populates="draft")


class MemoirSessionSummary(Base):
    __tablename__ = "memoir_session_summaries"
    __table_args__ = (
        UniqueConstraint("session_id", name="uq_memoir_session_summaries_session_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("memoir_sessions.id"), nullable=False, index=True,
    )
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    new_people_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    new_events_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    new_places_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    new_themes_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    open_questions_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    next_question_suggestions_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    project: Mapped["Project"] = orm_relationship("Project", back_populates="memoir_session_summaries")
    session: Mapped[MemoirSession] = orm_relationship("MemoirSession")


class MemoirOutput(Base):
    __tablename__ = "memoir_outputs"
    __table_args__ = (
        CheckConstraint(
            f"output_type IN ({_enum_values(MemoirOutputType)})",
            name="ck_memoir_outputs_type",
        ),
        CheckConstraint(
            f"status IN ({_enum_values(MemoirOutputStatus)})",
            name="ck_memoir_outputs_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    output_type: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    source_session_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memoir_sessions.id"), nullable=True, index=True,
    )
    status: Mapped[str] = mapped_column(String(20), default=MemoirOutputStatus.DRAFT.value, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    project: Mapped["Project"] = orm_relationship("Project", back_populates="memoir_outputs")
    source_session: Mapped[MemoirSession | None] = orm_relationship("MemoirSession")
    versions: Mapped[list["MemoirOutputVersion"]] = orm_relationship(
        "MemoirOutputVersion",
        back_populates="output",
        cascade="all, delete-orphan",
        order_by="MemoirOutputVersion.version",
    )


class MemoirOutputVersion(Base):
    __tablename__ = "memoir_output_versions"
    __table_args__ = (
        UniqueConstraint("output_id", "version", name="uq_memoir_output_versions_output_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    output_id: Mapped[str] = mapped_column(String(36), ForeignKey("memoir_outputs.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    prompt_snapshot: Mapped[str] = mapped_column(Text, default="", nullable=False)
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    output: Mapped[MemoirOutput] = orm_relationship("MemoirOutput", back_populates="versions")
