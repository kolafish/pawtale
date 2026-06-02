from __future__ import annotations

from datetime import datetime
from enum import Enum
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.domain.memoir.types import (
    MemoirAnswerStatus,
    MemoirDraftStatus,
    MemoirQuestionSource,
    MemoirQuestionStatus,
    MemoirSessionStatus,
)


def _enum_values(enum_cls: type[Enum]) -> str:
    return ", ".join(f"'{item.value}'" for item in enum_cls)


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

    project: Mapped["Project"] = relationship("Project", back_populates="memoir_sessions")
    questions: Mapped[list["MemoirQuestion"]] = relationship(
        "MemoirQuestion",
        back_populates="session",
        cascade="all, delete-orphan",
        foreign_keys="MemoirQuestion.session_id",
        order_by="MemoirQuestion.sort_order",
    )
    current_question: Mapped["MemoirQuestion | None"] = relationship(
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

    session: Mapped[MemoirSession] = relationship(
        "MemoirSession",
        back_populates="questions",
        foreign_keys=[session_id],
    )
    answer: Mapped["MemoirAnswer | None"] = relationship(
        "MemoirAnswer",
        back_populates="question",
        cascade="all, delete-orphan",
        uselist=False,
    )


class AudioFile(Base):
    __tablename__ = "audio_files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


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

    question: Mapped[MemoirQuestion] = relationship("MemoirQuestion", back_populates="answer")
    audio_file: Mapped[AudioFile | None] = relationship(
        "AudioFile",
        cascade="all, delete-orphan",
        single_parent=True,
    )
    draft: Mapped["MemoirDraft | None"] = relationship(
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

    answer: Mapped[MemoirAnswer] = relationship("MemoirAnswer", back_populates="draft")
