from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship as orm_relationship

from app.database import Base
from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirBookPlanStatus,
    MemoirChatContentStatus,
    MemoirChatMessageRole,
    MemoirCreatedBy,
    MemoirGuidanceMode,
    MemoirOutputStatus,
    MemoirTranscriptVersionType,
    MemoirVoiceSessionStatus,
)
from app.models.memoir_common import AudioFile, _enum_values


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
    memoir_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
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
    answer_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    voice_session_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memoir_voice_sessions.id"), nullable=True, index=True,
    )
    source_artifact_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    version_type: Mapped[str] = mapped_column(String(30), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_by: Mapped[str] = mapped_column(String(20), nullable=False, default=MemoirCreatedBy.SYSTEM.value)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    voice_session: Mapped[MemoirVoiceSession | None] = orm_relationship(
        "MemoirVoiceSession",
        back_populates="transcript_versions",
    )


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
