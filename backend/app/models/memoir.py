from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship as orm_relationship

from app.database import Base
from app.domain.memoir.types import (
    MemoirAutomationEventStatus,
    MemoirAutomationEventType,
    MemoirBookChapterDraftStatus,
    MemoirBookPlanStatus,
    MemoirChapterSourceAssignedBy,
    MemoirChapterSourceLinkStatus,
    MemoirEditingJobStatus,
    MemoirEditingJobType,
    MemoirChatContentStatus,
    MemoirChatMessageRole,
    MemoirCreatedBy,
    MemoirGuidanceMode,
    MemoirBookImagePlacementType,
    MemoirImageAssetSourceType,
    MemoirImageAssetStatus,
    MemoirInterviewType,
    MemoirSessionGoal,
    MemoirOutputStatus,
    MemoirSemanticCacheStatus,
    MemoirShareVideoJobStatus,
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
        CheckConstraint(
            f"interview_type IN ({_enum_values(MemoirInterviewType)})",
            name="ck_memoir_voice_sessions_interview_type",
        ),
        CheckConstraint(
            f"session_goal IN ({_enum_values(MemoirSessionGoal)})",
            name="ck_memoir_voice_sessions_session_goal",
        ),
        CheckConstraint(
            "current_completion_item_id IS NULL OR session_goal = 'book_completion'",
            name="ck_memoir_voice_sessions_completion_goal",
        ),
        CheckConstraint(
            "anchor_framework_item_id IS NULL OR "
            "(guidance_mode = 'guided_interview' "
            "AND session_goal = 'open_narration')",
            name="ck_memoir_voice_sessions_anchor_mode",
        ),
        CheckConstraint(
            "anchor_framework_item_id IS NULL "
            "OR current_completion_item_id IS NULL",
            name="ck_memoir_voice_sessions_single_target",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
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
    interview_type: Mapped[str] = mapped_column(
        String(30),
        default=MemoirInterviewType.AI_INTERVIEW.value,
        nullable=False,
    )
    session_goal: Mapped[str] = mapped_column(
        String(30),
        default=MemoirSessionGoal.OPEN_NARRATION.value,
        nullable=False,
    )
    current_completion_item_id: Mapped[str | None] = mapped_column(
        String(36),
        nullable=True,
        index=True,
    )
    anchor_framework_item_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("memoir_framework_items.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
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
    __table_args__ = (
        CheckConstraint(
            "framework_item_id IS NULL OR completion_item_id IS NULL",
            name="ck_memoir_voice_utterances_one_interview_target",
        ),
    )

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
    source_material_type: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    source_route_action: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    source_route_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_route_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    source_route_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    framework_item_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("memoir_framework_items.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    completion_item_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("memoir_completion_items.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    policy_decision_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("memoir_interview_policy_decisions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    voice_session: Mapped[MemoirVoiceSession] = orm_relationship("MemoirVoiceSession", back_populates="utterances")
    chat_message: Mapped[MemoirChatMessage | None] = orm_relationship(
        "MemoirChatMessage",
        back_populates="utterances",
    )
    audio_file: Mapped[AudioFile | None] = orm_relationship("AudioFile")
    transcript_alignment: Mapped["MemoirTranscriptAlignment | None"] = orm_relationship(
        "MemoirTranscriptAlignment",
        back_populates="utterance",
        cascade="all, delete-orphan",
    )


class MemoirTranscriptAlignment(Base):
    __tablename__ = "memoir_transcript_alignments"
    __table_args__ = (
        UniqueConstraint("utterance_id", name="uq_memoir_transcript_alignments_utterance"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    utterance_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("memoir_voice_utterances.id"),
        nullable=False,
        index=True,
    )
    text_source: Mapped[str] = mapped_column(String(20), nullable=False)
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    strategy: Mapped[str] = mapped_column(String(40), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    alignment_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    segments_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    utterance: Mapped[MemoirVoiceUtterance] = orm_relationship(
        "MemoirVoiceUtterance",
        back_populates="transcript_alignment",
    )


class MemoirMaterialShareVideo(Base):
    __tablename__ = "memoir_material_share_videos"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_enum_values(MemoirShareVideoJobStatus)})",
            name="ck_memoir_material_share_videos_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    utterance_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("memoir_voice_utterances.id"),
        nullable=False,
        index=True,
    )
    audio_file_id: Mapped[str] = mapped_column(String(36), ForeignKey("audio_files.id"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(20),
        default=MemoirShareVideoJobStatus.PENDING.value,
        nullable=False,
        index=True,
    )
    run_after: Mapped[datetime | None] = mapped_column(DateTime, default=datetime.utcnow, nullable=True, index=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    text_source: Mapped[str] = mapped_column(String(20), nullable=False)
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    selected_text: Mapped[str] = mapped_column(Text, nullable=False)
    start_char: Mapped[int] = mapped_column(Integer, nullable=False)
    end_char: Mapped[int] = mapped_column(Integer, nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    project: Mapped["Project"] = orm_relationship("Project")
    utterance: Mapped[MemoirVoiceUtterance] = orm_relationship("MemoirVoiceUtterance")
    audio_file: Mapped[AudioFile] = orm_relationship("AudioFile")
    output: Mapped["MemoirShareVideoOutput | None"] = orm_relationship(
        "MemoirShareVideoOutput",
        back_populates="job",
        cascade="all, delete-orphan",
        lazy="joined",
        uselist=False,
    )


class MemoirShareVideoOutput(Base):
    __tablename__ = "memoir_share_video_outputs"
    __table_args__ = (
        UniqueConstraint(
            "job_id",
            name="uq_memoir_share_video_outputs_job_id",
        ),
        CheckConstraint(
            "size_bytes > 0",
            name="ck_memoir_share_video_outputs_positive_size",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    job_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("memoir_material_share_videos.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    job: Mapped[MemoirMaterialShareVideo] = orm_relationship(
        "MemoirMaterialShareVideo",
        back_populates="output",
    )
    project: Mapped["Project"] = orm_relationship("Project")


class MemoirTranscriptVersion(Base):
    __tablename__ = "memoir_transcript_versions"
    __table_args__ = (
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
    voice_session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("memoir_voice_sessions.id"), nullable=False, index=True,
    )
    source_artifact_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    version_type: Mapped[str] = mapped_column(String(30), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_by: Mapped[str] = mapped_column(String(20), nullable=False, default=MemoirCreatedBy.SYSTEM.value)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    voice_session: Mapped[MemoirVoiceSession] = orm_relationship(
        "MemoirVoiceSession",
        back_populates="transcript_versions",
    )


class MemoirImageAsset(Base):
    __tablename__ = "memoir_image_assets"
    __table_args__ = (
        UniqueConstraint("project_id", "source_signature", name="uq_memoir_image_assets_source_signature"),
        CheckConstraint(
            f"status IN ({_enum_values(MemoirImageAssetStatus)})",
            name="ck_memoir_image_assets_status",
        ),
        CheckConstraint(
            f"source_type IN ({_enum_values(MemoirImageAssetSourceType)})",
            name="ck_memoir_image_assets_source_type",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(20),
        default=MemoirImageAssetStatus.READY.value,
        nullable=False,
        index=True,
    )
    source_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    source_signature: Mapped[str] = mapped_column(String(128), default="", nullable=False, index=True)
    storage_path: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    thumbnail_storage_path: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    caption: Mapped[str] = mapped_column(Text, default="", nullable=False)
    alt_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    style_id: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    prompt_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(128), default="", nullable=False, index=True)
    prompt_version: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    provider: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    model: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    source_utterance_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memoir_voice_utterances.id"), nullable=True, index=True,
    )
    source_chapter_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memoir_book_plan_chapters.id"), nullable=True, index=True,
    )
    share_video_job_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memoir_material_share_videos.id"), nullable=True, index=True,
    )
    source_quote: Mapped[str] = mapped_column(Text, default="", nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    project: Mapped["Project"] = orm_relationship("Project")
    user: Mapped["User"] = orm_relationship("User")
    source_utterance: Mapped[MemoirVoiceUtterance | None] = orm_relationship("MemoirVoiceUtterance")
    source_chapter: Mapped["MemoirBookPlanChapter | None"] = orm_relationship("MemoirBookPlanChapter")
    share_video_job: Mapped[MemoirMaterialShareVideo | None] = orm_relationship("MemoirMaterialShareVideo")
    placements: Mapped[list["MemoirBookImagePlacement"]] = orm_relationship(
        "MemoirBookImagePlacement",
        back_populates="image_asset",
        cascade="all, delete-orphan",
        order_by="MemoirBookImagePlacement.sort_order",
    )


class MemoirBookImagePlacement(Base):
    __tablename__ = "memoir_book_image_placements"
    __table_args__ = (
        CheckConstraint(
            f"placement_type IN ({_enum_values(MemoirBookImagePlacementType)})",
            name="ck_memoir_book_image_placements_type",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    image_asset_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("memoir_image_assets.id"), nullable=False, index=True,
    )
    placement_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    chapter_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memoir_book_plan_chapters.id"), nullable=True, index=True,
    )
    caption_override: Mapped[str] = mapped_column(Text, default="", nullable=False)
    display_size: Mapped[str] = mapped_column(String(40), default="full_width", nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    project: Mapped["Project"] = orm_relationship("Project")
    image_asset: Mapped[MemoirImageAsset] = orm_relationship("MemoirImageAsset", back_populates="placements")
    chapter: Mapped["MemoirBookPlanChapter | None"] = orm_relationship("MemoirBookPlanChapter")


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
    include_in_book: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
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
    block_snapshot_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
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
    formal_version_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey(
            "memoir_book_output_versions.id",
            name="fk_memoir_book_outputs_formal_version",
            use_alter=True,
        ),
        nullable=True,
    )
    formal_audit_job_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memoir_editing_jobs.id"), nullable=True
    )
    formalized_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    formalized_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
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
        foreign_keys="MemoirBookOutputVersion.output_id",
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
    manuscript_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_by: Mapped[str] = mapped_column(String(20), default=MemoirCreatedBy.SYSTEM.value, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    output: Mapped[MemoirBookOutput] = orm_relationship(
        "MemoirBookOutput",
        back_populates="versions",
        foreign_keys=[output_id],
    )


class MemoirSourceCard(Base):
    __tablename__ = "memoir_source_cards"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "utterance_id",
            "engine",
            "source_text_hash",
            name="uq_memoir_source_cards_signature",
        ),
        CheckConstraint(
            f"status IN ({_enum_values(MemoirSemanticCacheStatus)})",
            name="ck_memoir_source_cards_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    utterance_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("memoir_voice_utterances.id"),
        nullable=False,
        index=True,
    )
    transcript_version_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("memoir_transcript_versions.id"),
        nullable=True,
        index=True,
    )
    source_text_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    engine: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(20),
        default=MemoirSemanticCacheStatus.READY.value,
        nullable=False,
        index=True,
    )
    card_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    project: Mapped["Project"] = orm_relationship("Project")
    utterance: Mapped[MemoirVoiceUtterance] = orm_relationship("MemoirVoiceUtterance")
    transcript_version: Mapped[MemoirTranscriptVersion | None] = orm_relationship("MemoirTranscriptVersion")


class MemoirChapterCard(Base):
    __tablename__ = "memoir_chapter_cards"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "chapter_id",
            "engine",
            "input_signature",
            name="uq_memoir_chapter_cards_signature",
        ),
        CheckConstraint(
            f"status IN ({_enum_values(MemoirSemanticCacheStatus)})",
            name="ck_memoir_chapter_cards_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    plan_id: Mapped[str] = mapped_column(String(36), ForeignKey("memoir_book_plans.id"), nullable=False, index=True)
    chapter_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("memoir_book_plan_chapters.id"),
        nullable=False,
        index=True,
    )
    draft_version_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("memoir_book_chapter_draft_versions.id"),
        nullable=True,
        index=True,
    )
    input_signature: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    engine: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(20),
        default=MemoirSemanticCacheStatus.READY.value,
        nullable=False,
        index=True,
    )
    card_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    project: Mapped["Project"] = orm_relationship("Project")
    plan: Mapped[MemoirBookPlan] = orm_relationship("MemoirBookPlan")
    chapter: Mapped[MemoirBookPlanChapter] = orm_relationship("MemoirBookPlanChapter")
    draft_version: Mapped[MemoirBookChapterDraftVersion | None] = orm_relationship("MemoirBookChapterDraftVersion")


class MemoirBookQualityAuditSnapshot(Base):
    __tablename__ = "memoir_book_quality_audit_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "engine",
            "input_signature",
            name="uq_memoir_book_quality_audit_snapshots_signature",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    engine: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    input_signature: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    overall_status: Mapped[str] = mapped_column(String(40), default="", nullable=False, index=True)
    summary_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    display_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    primary_action_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    chapter_audits_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    source_card_ids_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    chapter_card_ids_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    project: Mapped["Project"] = orm_relationship("Project")


class MemoirChapterSourceLink(Base):
    __tablename__ = "memoir_chapter_source_links"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "chapter_id",
            "utterance_id",
            name="uq_memoir_chapter_source_links_chapter_utterance",
        ),
        CheckConstraint(
            f"status IN ({_enum_values(MemoirChapterSourceLinkStatus)})",
            name="ck_memoir_chapter_source_links_status",
        ),
        CheckConstraint(
            f"assigned_by IN ({_enum_values(MemoirChapterSourceAssignedBy)})",
            name="ck_memoir_chapter_source_links_assigned_by",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    plan_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    chapter_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    utterance_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    transcript_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    audio_file_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    start_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quote: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        default=MemoirChapterSourceLinkStatus.ACTIVE.value,
        nullable=False,
        index=True,
    )
    assigned_by: Mapped[str] = mapped_column(
        String(20),
        default=MemoirChapterSourceAssignedBy.SYSTEM.value,
        nullable=False,
        index=True,
    )
    assignment_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    assignment_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    source_card_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    merged_draft_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    project: Mapped["Project"] = orm_relationship("Project")


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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    project: Mapped["Project"] = orm_relationship("Project")
    chapter: Mapped[MemoirBookPlanChapter | None] = orm_relationship("MemoirBookPlanChapter")


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
        CheckConstraint(
            "event_type != 'source_held' OR status != 'needs_review' "
            "OR review_utterance_id IS NOT NULL",
            name="ck_memoir_automation_events_review_utterance",
        ),
        CheckConstraint(
            "(review_utterance_id IS NULL AND completion_item_id IS NULL) "
            "OR event_type = 'source_held'",
            name="ck_memoir_automation_events_review_links",
        ),
        CheckConstraint(
            "completion_item_id IS NULL OR review_utterance_id IS NOT NULL",
            name="ck_memoir_automation_events_completion_review",
        ),
        Index(
            "ix_memoir_automation_events_project_timeline",
            "project_id",
            "event_type",
            "status",
            "created_at",
        ),
        Index(
            "ix_memoir_automation_events_review_utterance",
            "project_id",
            "event_type",
            "status",
            "review_utterance_id",
        ),
        Index(
            "ix_memoir_automation_events_review_completion_item",
            "project_id",
            "event_type",
            "status",
            "completion_item_id",
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
    review_utterance_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("memoir_voice_utterances.id", ondelete="CASCADE"),
        nullable=True,
    )
    completion_item_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("memoir_completion_items.id", ondelete="SET NULL"),
        nullable=True,
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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    project: Mapped["Project"] = orm_relationship("Project")
    chapter: Mapped[MemoirBookPlanChapter | None] = orm_relationship("MemoirBookPlanChapter")
