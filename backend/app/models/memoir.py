from __future__ import annotations

from datetime import date, datetime
from enum import Enum
import uuid

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship as orm_relationship

from app.database import Base
from app.domain.memoir.types import (
    MemoirAnswerStatus,
    MemoirBookChapterDraftStatus,
    MemoirBookPlanStatus,
    MemoirChatContentStatus,
    MemoirChatMessageRole,
    MemoirCoverageStatus,
    MemoirCreatedBy,
    MemoirDraftStatus,
    MemoirGuidanceMode,
    MemoirInterviewStage,
    MemoirMemoryStatus,
    MemoirMemoryType,
    MemoirOpenQuestionStatus,
    MemoirOutputStatus,
    MemoirOutputType,
    MemoirQuestionCandidateStatus,
    MemoirQuestionSource,
    MemoirQuestionStatus,
    MemoirQuestionType,
    MemoirSensitivity,
    MemoirSessionStatus,
    MemoirSourceArtifactType,
    MemoirTranscriptVersionType,
    MemoirVoiceSessionStatus,
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


class MemoirVoiceSession(Base):
    __tablename__ = "memoir_voice_sessions"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_enum_values(MemoirVoiceSessionStatus)})",
            name="ck_memoir_voice_sessions_status",
        ),
        CheckConstraint(
            f"guidance_mode IN ({_enum_values(MemoirGuidanceMode)})",
            name="ck_memoir_voice_sessions_guidance_mode",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    memoir_session_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memoir_sessions.id"), nullable=True, index=True,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        default=MemoirVoiceSessionStatus.CREATED.value,
        nullable=False,
        index=True,
    )
    guidance_mode: Mapped[str] = mapped_column(
        String(30),
        default=MemoirGuidanceMode.FREE_NARRATION.value,
        nullable=False,
    )
    current_guidance_module: Mapped[str | None] = mapped_column(String(80), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    input_format: Mapped[str | None] = mapped_column(String(100), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    project: Mapped["Project"] = orm_relationship("Project", back_populates="memoir_voice_sessions")
    memoir_session: Mapped[MemoirSession | None] = orm_relationship("MemoirSession")
    messages: Mapped[list["MemoirChatMessage"]] = orm_relationship(
        "MemoirChatMessage",
        back_populates="voice_session",
        cascade="all, delete-orphan",
        order_by="MemoirChatMessage.turn_index",
    )
    utterances: Mapped[list["MemoirVoiceUtterance"]] = orm_relationship(
        "MemoirVoiceUtterance",
        back_populates="voice_session",
        cascade="all, delete-orphan",
        order_by="MemoirVoiceUtterance.created_at",
    )
    transcript_versions: Mapped[list["MemoirTranscriptVersion"]] = orm_relationship(
        "MemoirTranscriptVersion",
        back_populates="voice_session",
        cascade="all, delete-orphan",
    )


class MemoirVoiceAuthorizationConsent(Base):
    __tablename__ = "memoir_voice_authorization_consents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    consent_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    agreed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source: Mapped[str] = mapped_column(String(80), default="miniprogram", nullable=False)
    source_session_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("memoir_voice_sessions.id"),
        nullable=True,
        index=True,
    )
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user: Mapped["User"] = orm_relationship("User")
    source_session: Mapped[MemoirVoiceSession | None] = orm_relationship("MemoirVoiceSession")


class MemoirChatMessage(Base):
    __tablename__ = "memoir_chat_messages"
    __table_args__ = (
        CheckConstraint(
            f"role IN ({_enum_values(MemoirChatMessageRole)})",
            name="ck_memoir_chat_messages_role",
        ),
        CheckConstraint(
            f"content_status IN ({_enum_values(MemoirChatContentStatus)})",
            name="ck_memoir_chat_messages_content_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    voice_session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("memoir_voice_sessions.id"), nullable=False, index=True,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    content_status: Mapped[str] = mapped_column(
        String(20),
        default=MemoirChatContentStatus.FINAL.value,
        nullable=False,
    )
    audio_file_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("audio_files.id"), nullable=True)
    transcript_version_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memoir_transcript_versions.id"), nullable=True,
    )
    parent_message_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memoir_chat_messages.id"), nullable=True,
    )
    guidance_intent: Mapped[str | None] = mapped_column(String(80), nullable=True)
    turn_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    project: Mapped["Project"] = orm_relationship("Project")
    voice_session: Mapped[MemoirVoiceSession] = orm_relationship("MemoirVoiceSession", back_populates="messages")
    audio_file: Mapped[AudioFile | None] = orm_relationship("AudioFile")
    transcript_version: Mapped["MemoirTranscriptVersion | None"] = orm_relationship("MemoirTranscriptVersion")
    parent_message: Mapped["MemoirChatMessage | None"] = orm_relationship(
        "MemoirChatMessage",
        remote_side=[id],
    )
    utterances: Mapped[list["MemoirVoiceUtterance"]] = orm_relationship(
        "MemoirVoiceUtterance",
        back_populates="chat_message",
    )


class MemoirVoiceUtterance(Base):
    __tablename__ = "memoir_voice_utterances"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    voice_session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("memoir_voice_sessions.id"), nullable=False, index=True,
    )
    chat_message_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memoir_chat_messages.id"), nullable=True, index=True,
    )
    audio_file_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("audio_files.id"), nullable=True)
    transcript_raw: Mapped[str] = mapped_column(Text, default="", nullable=False)
    transcript_edited: Mapped[str] = mapped_column(Text, default="", nullable=False)
    start_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    provider_payload_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    voice_session: Mapped[MemoirVoiceSession] = orm_relationship("MemoirVoiceSession", back_populates="utterances")
    chat_message: Mapped[MemoirChatMessage | None] = orm_relationship(
        "MemoirChatMessage",
        back_populates="utterances",
    )
    audio_file: Mapped[AudioFile | None] = orm_relationship("AudioFile")


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


class MemoirSourceArtifact(Base):
    __tablename__ = "memoir_source_artifacts"
    __table_args__ = (
        CheckConstraint(
            f"artifact_type IN ({_enum_values(MemoirSourceArtifactType)})",
            name="ck_memoir_source_artifacts_type",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    session_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memoir_sessions.id"), nullable=True, index=True,
    )
    answer_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memoir_answers.id"), nullable=True, index=True,
    )
    artifact_type: Mapped[str] = mapped_column(String(20), nullable=False)
    storage_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    project: Mapped["Project"] = orm_relationship("Project", back_populates="memoir_source_artifacts")
    session: Mapped[MemoirSession | None] = orm_relationship("MemoirSession")
    answer: Mapped[MemoirAnswer | None] = orm_relationship("MemoirAnswer")
    transcript_versions: Mapped[list["MemoirTranscriptVersion"]] = orm_relationship(
        "MemoirTranscriptVersion",
        back_populates="source_artifact",
        cascade="all, delete-orphan",
    )


class MemoirTranscriptVersion(Base):
    __tablename__ = "memoir_transcript_versions"
    __table_args__ = (
        CheckConstraint(
            "answer_id IS NOT NULL OR voice_session_id IS NOT NULL",
            name="ck_memoir_transcript_versions_owner",
        ),
        CheckConstraint(
            f"version_type IN ({_enum_values(MemoirTranscriptVersionType)})",
            name="ck_memoir_transcript_versions_type",
        ),
        CheckConstraint(
            f"created_by IN ({_enum_values(MemoirCreatedBy)})",
            name="ck_memoir_transcript_versions_created_by",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    answer_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memoir_answers.id"), nullable=True, index=True,
    )
    voice_session_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memoir_voice_sessions.id"), nullable=True, index=True,
    )
    source_artifact_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memoir_source_artifacts.id"), nullable=True, index=True,
    )
    version_type: Mapped[str] = mapped_column(String(30), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_by: Mapped[str] = mapped_column(String(20), nullable=False, default=MemoirCreatedBy.SYSTEM.value)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    answer: Mapped[MemoirAnswer | None] = orm_relationship("MemoirAnswer")
    voice_session: Mapped[MemoirVoiceSession | None] = orm_relationship(
        "MemoirVoiceSession",
        back_populates="transcript_versions",
    )
    source_artifact: Mapped[MemoirSourceArtifact | None] = orm_relationship(
        "MemoirSourceArtifact",
        back_populates="transcript_versions",
    )


class MemoirPersonMemory(Base):
    __tablename__ = "memoir_person_memories"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_enum_values(MemoirMemoryStatus)})",
            name="ck_memoir_person_memories_status",
        ),
        CheckConstraint(
            f"created_by IN ({_enum_values(MemoirCreatedBy)})",
            name="ck_memoir_person_memories_created_by",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    canonical_name: Mapped[str] = mapped_column(String(120), nullable=False)
    aliases_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    relationship: Mapped[str | None] = mapped_column(String(120), nullable=True)
    first_seen_session_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memoir_sessions.id"), nullable=True,
    )
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=MemoirMemoryStatus.DRAFT.value, nullable=False)
    created_by: Mapped[str] = mapped_column(String(20), default=MemoirCreatedBy.AI.value, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    project: Mapped["Project"] = orm_relationship("Project", back_populates="memoir_person_memories")
    first_seen_session: Mapped[MemoirSession | None] = orm_relationship("MemoirSession")


class MemoirEventMemory(Base):
    __tablename__ = "memoir_event_memories"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_enum_values(MemoirMemoryStatus)})",
            name="ck_memoir_event_memories_status",
        ),
        CheckConstraint(
            f"created_by IN ({_enum_values(MemoirCreatedBy)})",
            name="ck_memoir_event_memories_created_by",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    life_stage: Mapped[str | None] = mapped_column(String(80), nullable=True)
    time_text: Mapped[str | None] = mapped_column(String(200), nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    place_text: Mapped[str | None] = mapped_column(String(200), nullable=True)
    cause: Mapped[str] = mapped_column(Text, default="", nullable=False)
    process: Mapped[str] = mapped_column(Text, default="", nullable=False)
    result: Mapped[str] = mapped_column(Text, default="", nullable=False)
    emotion: Mapped[str | None] = mapped_column(String(200), nullable=True)
    importance: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=MemoirMemoryStatus.DRAFT.value, nullable=False)
    created_by: Mapped[str] = mapped_column(String(20), default=MemoirCreatedBy.AI.value, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    project: Mapped["Project"] = orm_relationship("Project", back_populates="memoir_event_memories")


class MemoirPlaceMemory(Base):
    __tablename__ = "memoir_place_memories"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_enum_values(MemoirMemoryStatus)})",
            name="ck_memoir_place_memories_status",
        ),
        CheckConstraint(
            f"created_by IN ({_enum_values(MemoirCreatedBy)})",
            name="ck_memoir_place_memories_created_by",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    elder_alias: Mapped[str | None] = mapped_column(String(160), nullable=True)
    modern_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    place_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=MemoirMemoryStatus.DRAFT.value, nullable=False)
    created_by: Mapped[str] = mapped_column(String(20), default=MemoirCreatedBy.AI.value, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    project: Mapped["Project"] = orm_relationship("Project", back_populates="memoir_place_memories")


class MemoirThemeMemory(Base):
    __tablename__ = "memoir_theme_memories"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_enum_values(MemoirMemoryStatus)})",
            name="ck_memoir_theme_memories_status",
        ),
        CheckConstraint(
            f"created_by IN ({_enum_values(MemoirCreatedBy)})",
            name="ck_memoir_theme_memories_created_by",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    strength: Mapped[float | None] = mapped_column(Float, nullable=True)
    mention_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=MemoirMemoryStatus.DRAFT.value, nullable=False)
    created_by: Mapped[str] = mapped_column(String(20), default=MemoirCreatedBy.AI.value, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    project: Mapped["Project"] = orm_relationship("Project", back_populates="memoir_theme_memories")


class MemoirMemoryLink(Base):
    __tablename__ = "memoir_memory_links"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(30), nullable=False)
    source_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(30), nullable=False)
    target_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    relation_type: Mapped[str] = mapped_column(String(60), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    project: Mapped["Project"] = orm_relationship("Project", back_populates="memoir_memory_links")


class MemoirMemoryEvidence(Base):
    __tablename__ = "memoir_memory_evidence"
    __table_args__ = (
        CheckConstraint(
            f"memory_type IN ({_enum_values(MemoirMemoryType)})",
            name="ck_memoir_memory_evidence_memory_type",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    memory_type: Mapped[str] = mapped_column(String(30), nullable=False)
    memory_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    session_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memoir_sessions.id"), nullable=True, index=True,
    )
    question_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memoir_questions.id"), nullable=True, index=True,
    )
    answer_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memoir_answers.id"), nullable=True, index=True,
    )
    transcript_version_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memoir_transcript_versions.id"), nullable=True,
    )
    quote: Mapped[str] = mapped_column(Text, default="", nullable=False)
    start_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    project: Mapped["Project"] = orm_relationship("Project", back_populates="memoir_memory_evidence")
    session: Mapped[MemoirSession | None] = orm_relationship("MemoirSession")
    question: Mapped[MemoirQuestion | None] = orm_relationship("MemoirQuestion")
    answer: Mapped[MemoirAnswer | None] = orm_relationship("MemoirAnswer")
    transcript_version: Mapped[MemoirTranscriptVersion | None] = orm_relationship("MemoirTranscriptVersion")


class MemoirOpenQuestion(Base):
    __tablename__ = "memoir_open_questions"
    __table_args__ = (
        CheckConstraint(
            f"question_type IN ({_enum_values(MemoirQuestionType)})",
            name="ck_memoir_open_questions_question_type",
        ),
        CheckConstraint(
            f"sensitivity IN ({_enum_values(MemoirSensitivity)})",
            name="ck_memoir_open_questions_sensitivity",
        ),
        CheckConstraint(
            f"status IN ({_enum_values(MemoirOpenQuestionStatus)})",
            name="ck_memoir_open_questions_status",
        ),
        CheckConstraint(
            f"created_by IN ({_enum_values(MemoirCreatedBy)})",
            name="ck_memoir_open_questions_created_by",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    session_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memoir_sessions.id"), nullable=True, index=True,
    )
    target_memory_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    target_memory_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    question_type: Mapped[str] = mapped_column(String(30), nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    sensitivity: Mapped[str] = mapped_column(String(20), default=MemoirSensitivity.LOW.value, nullable=False)
    priority: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=MemoirOpenQuestionStatus.OPEN.value, nullable=False)
    created_by: Mapped[str] = mapped_column(String(20), default=MemoirCreatedBy.AI.value, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    project: Mapped["Project"] = orm_relationship("Project", back_populates="memoir_open_questions")
    session: Mapped[MemoirSession | None] = orm_relationship("MemoirSession")


class MemoirCoverageTopic(Base):
    __tablename__ = "memoir_coverage_topics"
    __table_args__ = (
        UniqueConstraint("project_id", "key", name="uq_memoir_coverage_topics_project_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    life_stage: Mapped[str | None] = mapped_column(String(80), nullable=True)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    project: Mapped["Project"] = orm_relationship("Project", back_populates="memoir_coverage_topics")
    snapshots: Mapped[list["MemoirCoverageSnapshot"]] = orm_relationship(
        "MemoirCoverageSnapshot",
        back_populates="topic",
        cascade="all, delete-orphan",
    )


class MemoirCoverageSnapshot(Base):
    __tablename__ = "memoir_coverage_snapshots"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_enum_values(MemoirCoverageStatus)})",
            name="ck_memoir_coverage_snapshots_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    topic_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("memoir_coverage_topics.id"), nullable=False, index=True,
    )
    answered_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    memory_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    open_question_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    coverage_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=MemoirCoverageStatus.RED.value, nullable=False)
    signals_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    project: Mapped["Project"] = orm_relationship("Project", back_populates="memoir_coverage_snapshots")
    topic: Mapped[MemoirCoverageTopic] = orm_relationship("MemoirCoverageTopic", back_populates="snapshots")


class MemoirQuestionCandidate(Base):
    __tablename__ = "memoir_question_candidates"
    __table_args__ = (
        CheckConstraint(
            f"question_type IN ({_enum_values(MemoirQuestionType)})",
            name="ck_memoir_question_candidates_type",
        ),
        CheckConstraint(
            f"sensitivity IN ({_enum_values(MemoirSensitivity)})",
            name="ck_memoir_question_candidates_sensitivity",
        ),
        CheckConstraint(
            f"stage IN ({_enum_values(MemoirInterviewStage)})",
            name="ck_memoir_question_candidates_stage",
        ),
        CheckConstraint(
            f"status IN ({_enum_values(MemoirQuestionCandidateStatus)})",
            name="ck_memoir_question_candidates_status",
        ),
        CheckConstraint(
            f"created_by IN ({_enum_values(MemoirCreatedBy)})",
            name="ck_memoir_question_candidates_created_by",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    session_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memoir_sessions.id"), nullable=True, index=True,
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    question_type: Mapped[str] = mapped_column(String(30), nullable=False)
    target_memory_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    target_memory_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    coverage_topic_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memoir_coverage_topics.id"), nullable=True, index=True,
    )
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    sensitivity: Mapped[str] = mapped_column(String(20), default=MemoirSensitivity.LOW.value, nullable=False)
    stage: Mapped[str] = mapped_column(String(30), default=MemoirInterviewStage.SKELETON.value, nullable=False)
    priority: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default=MemoirQuestionCandidateStatus.CANDIDATE.value, nullable=False,
    )
    created_by: Mapped[str] = mapped_column(String(20), default=MemoirCreatedBy.AI.value, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    project: Mapped["Project"] = orm_relationship("Project", back_populates="memoir_question_candidates")
    session: Mapped[MemoirSession | None] = orm_relationship("MemoirSession")
    coverage_topic: Mapped[MemoirCoverageTopic | None] = orm_relationship("MemoirCoverageTopic")


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


class MemoirBookPlan(Base):
    __tablename__ = "memoir_book_plans"
    __table_args__ = (
        UniqueConstraint("project_id", name="uq_memoir_book_plans_project_id"),
        CheckConstraint(
            f"status IN ({_enum_values(MemoirBookPlanStatus)})",
            name="ck_memoir_book_plans_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), default="章节规划", nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        default=MemoirBookPlanStatus.DRAFT.value,
        nullable=False,
    )
    created_by: Mapped[str] = mapped_column(
        String(20),
        default=MemoirCreatedBy.AI.value,
        nullable=False,
    )
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    prompt_snapshot: Mapped[str] = mapped_column(Text, default="", nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    project: Mapped["Project"] = orm_relationship("Project", back_populates="memoir_book_plans")
    chapters: Mapped[list["MemoirBookPlanChapter"]] = orm_relationship(
        "MemoirBookPlanChapter",
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="MemoirBookPlanChapter.sort_order",
    )


class MemoirBookPlanChapter(Base):
    __tablename__ = "memoir_book_plan_chapters"
    __table_args__ = (
        UniqueConstraint("plan_id", "sort_order", name="uq_memoir_book_plan_chapters_plan_order"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    plan_id: Mapped[str] = mapped_column(String(36), ForeignKey("memoir_book_plans.id"), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    source_refs_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    plan: Mapped[MemoirBookPlan] = orm_relationship("MemoirBookPlan", back_populates="chapters")
    draft: Mapped["MemoirBookChapterDraft | None"] = orm_relationship(
        "MemoirBookChapterDraft",
        back_populates="chapter",
        cascade="all, delete-orphan",
        uselist=False,
    )


class MemoirBookChapterDraft(Base):
    __tablename__ = "memoir_book_chapter_drafts"
    __table_args__ = (
        UniqueConstraint("chapter_id", name="uq_memoir_book_chapter_drafts_chapter_id"),
        CheckConstraint(
            f"status IN ({_enum_values(MemoirBookChapterDraftStatus)})",
            name="ck_memoir_book_chapter_drafts_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    chapter_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("memoir_book_plan_chapters.id"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        default=MemoirBookChapterDraftStatus.DRAFT.value,
        nullable=False,
    )
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    chapter: Mapped[MemoirBookPlanChapter] = orm_relationship("MemoirBookPlanChapter", back_populates="draft")
    versions: Mapped[list["MemoirBookChapterDraftVersion"]] = orm_relationship(
        "MemoirBookChapterDraftVersion",
        back_populates="draft",
        cascade="all, delete-orphan",
        order_by="MemoirBookChapterDraftVersion.version",
    )


class MemoirBookChapterDraftVersion(Base):
    __tablename__ = "memoir_book_chapter_draft_versions"
    __table_args__ = (
        UniqueConstraint("draft_id", "version", name="uq_memoir_book_chapter_draft_versions_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    draft_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("memoir_book_chapter_drafts.id"),
        nullable=False,
        index=True,
    )
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    chapter_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("memoir_book_plan_chapters.id"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(String(20), default=MemoirCreatedBy.AI.value, nullable=False)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    prompt_snapshot: Mapped[str] = mapped_column(Text, default="", nullable=False)
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    draft: Mapped[MemoirBookChapterDraft] = orm_relationship("MemoirBookChapterDraft", back_populates="versions")


class MemoirBookOutput(Base):
    __tablename__ = "memoir_book_outputs"
    __table_args__ = (
        UniqueConstraint("project_id", name="uq_memoir_book_outputs_project_id"),
        CheckConstraint(
            f"status IN ({_enum_values(MemoirOutputStatus)})",
            name="ck_memoir_book_outputs_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    plan_id: Mapped[str] = mapped_column(String(36), ForeignKey("memoir_book_plans.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=MemoirOutputStatus.DRAFT.value, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    project: Mapped["Project"] = orm_relationship("Project", back_populates="memoir_book_outputs")
    plan: Mapped[MemoirBookPlan] = orm_relationship("MemoirBookPlan")
    versions: Mapped[list["MemoirBookOutputVersion"]] = orm_relationship(
        "MemoirBookOutputVersion",
        back_populates="output",
        cascade="all, delete-orphan",
        order_by="MemoirBookOutputVersion.version",
    )


class MemoirBookOutputVersion(Base):
    __tablename__ = "memoir_book_output_versions"
    __table_args__ = (
        UniqueConstraint("output_id", "version", name="uq_memoir_book_output_versions_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    output_id: Mapped[str] = mapped_column(String(36), ForeignKey("memoir_book_outputs.id"), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    plan_id: Mapped[str] = mapped_column(String(36), ForeignKey("memoir_book_plans.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    chapter_snapshot_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    created_by: Mapped[str] = mapped_column(String(20), default=MemoirCreatedBy.SYSTEM.value, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    output: Mapped[MemoirBookOutput] = orm_relationship("MemoirBookOutput", back_populates="versions")
