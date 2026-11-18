from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
import secrets
import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    JSON,
    String,
    Text,
    UniqueConstraint,
    text as sql_text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship as orm_relationship

from app.clock import utcnow_naive
from app.database import Base
from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirBookRevisionProposalStatus,
    MemoirBookRevisionProposalPhase,
    MemoirBookPlanStatus,
    MemoirCompletionIssueChannel,
    MemoirCompletionIssueOutcome,
    MemoirCompletionIssuePriority,
    MemoirCompletionIssueScope,
    MemoirCompletionIssueStatus,
    MemoirCompletionIssueType,
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
    MemoirSectionMaterialAssignedBy,
    MemoirSectionStatus,
    MemoirSectionType,
    MemoirSemanticCacheStatus,
    MemoirShareVideoJobStatus,
    MemoirShareVideoCandidateStatus,
    MemoirTranscriptVersionType,
    MemoirVoiceSessionStatus,
)
from app.models.memoir_common import AudioFile, _enum_values
from app.models.memoir_jobs import (
    MemoirAutomationEvent as MemoirAutomationEvent,
    MemoirBookCompletionJob as MemoirBookCompletionJob,
    MemoirEditingJob as MemoirEditingJob,
)

if TYPE_CHECKING:
    from app.models.projects import Project
    from app.models.users import User


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
            "current_completion_issue_id IS NULL OR session_goal = 'book_completion'",
            name="ck_memoir_voice_sessions_completion_goal",
        ),
        CheckConstraint(
            "anchor_framework_item_id IS NULL OR "
            "(interview_type = 'ai_interview' "
            "AND session_goal = 'open_narration')",
            name="ck_memoir_voice_sessions_anchor_mode",
        ),
        CheckConstraint(
            "anchor_framework_item_id IS NULL OR current_completion_issue_id IS NULL",
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
        default=lambda context: (
            MemoirGuidanceMode.FREE_NARRATION.value
            if context.get_current_parameters().get("interview_type") == MemoirInterviewType.HUMAN_INTERVIEW.value
            else MemoirGuidanceMode.GUIDED_INTERVIEW.value
        ),
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
    current_completion_issue_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("memoir_completion_issues.id", ondelete="SET NULL"),
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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)

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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)

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
        String(36),
        ForeignKey("memoir_voice_sessions.id"),
        nullable=False,
        index=True,
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
        String(36),
        ForeignKey("memoir_transcript_versions.id"),
        nullable=True,
    )
    parent_message_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("memoir_chat_messages.id"),
        nullable=True,
    )
    guidance_intent: Mapped[str | None] = mapped_column(String(80), nullable=True)
    turn_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)

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
            "framework_item_id IS NULL OR completion_issue_id IS NULL",
            name="ck_memoir_voice_utterances_one_interview_target",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    voice_session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("memoir_voice_sessions.id"),
        nullable=False,
        index=True,
    )
    chat_message_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("memoir_chat_messages.id"),
        nullable=True,
        index=True,
    )
    audio_file_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("audio_files.id"), nullable=True)
    transcript_raw: Mapped[str] = mapped_column(Text, default="", nullable=False)
    transcript_edited: Mapped[str] = mapped_column(Text, default="", nullable=False)
    start_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    provider_payload_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    framework_item_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("memoir_framework_items.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    completion_issue_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("memoir_completion_issues.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)

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
    __table_args__ = (UniqueConstraint("utterance_id", name="uq_memoir_transcript_alignments_utterance"),)

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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)

    utterance: Mapped[MemoirVoiceUtterance] = orm_relationship(
        "MemoirVoiceUtterance",
        back_populates="transcript_alignment",
    )


class MemoirShareVideoCandidate(Base):
    __tablename__ = "memoir_share_video_candidates"
    __table_args__ = (
        UniqueConstraint("voice_session_id", name="uq_memoir_share_video_candidates_session"),
        CheckConstraint(
            f"status IN ({_enum_values(MemoirShareVideoCandidateStatus)})",
            name="ck_memoir_share_video_candidates_status",
        ),
        CheckConstraint(
            "status NOT IN ('ready', 'accepted') OR ("
            "text_hash IS NOT NULL AND "
            "start_char IS NOT NULL AND end_char IS NOT NULL AND end_char > start_char AND "
            "start_ms IS NOT NULL AND end_ms IS NOT NULL AND end_ms > start_ms AND length(selected_text) > 0)",
            name="ck_memoir_share_video_candidates_ready_range",
        ),
        Index(
            "ix_memoir_share_video_candidates_project_latest",
            "project_id", "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    voice_session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("memoir_voice_sessions.id", ondelete="CASCADE"), nullable=False,
    )
    utterance_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memoir_voice_utterances.id", ondelete="SET NULL"), nullable=True,
    )
    audio_file_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("audio_files.id", ondelete="SET NULL"), nullable=True,
    )
    transcript_version_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memoir_transcript_versions.id", ondelete="SET NULL"), nullable=True,
    )
    text_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    selected_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    start_char: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_char: Mapped[int | None] = mapped_column(Integer, nullable=True)
    start_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), default=MemoirShareVideoCandidateStatus.NONE.value, nullable=False,
    )
    algorithm_version: Mapped[str] = mapped_column(String(32), default="v1", nullable=False)
    reason_code: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    # The model's 1-5 scores, kept for every judged range so thresholds can be tuned later.
    emotion_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    visual_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    standalone_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # One line shown on the recommendation card.
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # A sentence of selected_text that opens a video made from this candidate.
    hook_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    video_job_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memoir_material_share_videos.id", ondelete="SET NULL"), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)


class MemoirMaterialShareVideo(Base):
    __tablename__ = "memoir_material_share_videos"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_enum_values(MemoirShareVideoJobStatus)})",
            name="ck_memoir_material_share_videos_status",
        ),
        UniqueConstraint("share_code", name="uq_memoir_material_share_videos_share_code"),
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
    run_after: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow_naive, nullable=True, index=True)
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
    # Public handle for the forwarded card and the mini program code in the video.
    share_code: Mapped[str] = mapped_column(String(20), default=lambda: secrets.token_hex(10), nullable=False)
    share_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sql_text("false"), nullable=False)
    rendered_share_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)

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


class MemoirShareVideoImageReceipt(Base):
    __tablename__ = "memoir_share_video_image_receipts"

    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("memoir_material_share_videos.id", ondelete="CASCADE"), primary_key=True,
    )
    slot: Mapped[str] = mapped_column(String(32), primary_key=True)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    cache_key: Mapped[dict] = mapped_column(JSON, nullable=False)
    # Retain the provider result until the cache exists and its debit commits.
    pending_content: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)


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

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
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
        default=utcnow_naive,
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
        String(36),
        ForeignKey("memoir_voice_sessions.id"),
        nullable=False,
        index=True,
    )
    source_artifact_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    version_type: Mapped[str] = mapped_column(String(30), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_by: Mapped[str] = mapped_column(String(20), nullable=False, default=MemoirCreatedBy.SYSTEM.value)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)

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
        String(36),
        ForeignKey("memoir_voice_utterances.id"),
        nullable=True,
        index=True,
    )
    source_chapter_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("memoir_book_plan_chapters.id"),
        nullable=True,
        index=True,
    )
    share_video_job_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("memoir_material_share_videos.id"),
        nullable=True,
        index=True,
    )
    source_quote: Mapped[str] = mapped_column(Text, default="", nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)

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
        CheckConstraint("inline_position IN ('auto', 'before', 'middle', 'after')", name="ck_memoir_book_image_inline_position"),
        CheckConstraint(
            f"placement_type IN ({_enum_values(MemoirBookImagePlacementType)})",
            name="ck_memoir_book_image_placements_type",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    image_asset_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("memoir_image_assets.id"),
        nullable=False,
        index=True,
    )
    placement_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    chapter_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("memoir_book_plan_chapters.id"),
        nullable=True,
        index=True,
    )
    source_utterance_ids_json: Mapped[list[str]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False,
    )
    inline_position: Mapped[str] = mapped_column(String(10), default="auto", server_default="auto", nullable=False)
    caption_override: Mapped[str] = mapped_column(Text, default="", nullable=False)
    display_size: Mapped[str] = mapped_column(String(40), default="full_width", nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)

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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)

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
        UniqueConstraint(
            "project_id",
            "id",
            name="uq_memoir_book_plan_chapters_project_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    plan_id: Mapped[str] = mapped_column(String(36), ForeignKey("memoir_book_plans.id"), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    include_in_book: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)

    plan: Mapped[MemoirBookPlan] = orm_relationship("MemoirBookPlan", back_populates="chapters")
    draft: Mapped["MemoirBookChapterDraft | None"] = orm_relationship(
        "MemoirBookChapterDraft",
        back_populates="chapter",
        cascade="all, delete-orphan",
        uselist=False,
    )
    sections: Mapped[list["MemoirSection"]] = orm_relationship(
        "MemoirSection",
        back_populates="chapter",
        cascade="all, delete-orphan",
        order_by="MemoirSection.sort_order",
    )


class MemoirSection(Base):
    __tablename__ = "memoir_sections"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "id",
            name="uq_memoir_sections_project_id",
        ),
        UniqueConstraint(
            "chapter_id",
            "sort_order",
            name="uq_memoir_sections_chapter_order",
        ),
        ForeignKeyConstraint(
            ["project_id", "chapter_id"],
            [
                "memoir_book_plan_chapters.project_id",
                "memoir_book_plan_chapters.id",
            ],
            name="fk_memoir_sections_project_chapter",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            f"section_type IN ({_enum_values(MemoirSectionType)})",
            name="ck_memoir_sections_type",
        ),
        CheckConstraint(
            f"status IN ({_enum_values(MemoirSectionStatus)})",
            name="ck_memoir_sections_status",
        ),
        CheckConstraint(
            "version > 0",
            name="ck_memoir_sections_positive_version",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chapter_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    section_type: Mapped[str] = mapped_column(
        String(40),
        default=MemoirSectionType.EVENT.value,
        nullable=False,
        index=True,
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        default=MemoirSectionStatus.ACTIVE.value,
        nullable=False,
        index=True,
    )
    include_in_book: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utcnow_naive,
        onupdate=utcnow_naive,
    )

    chapter: Mapped[MemoirBookPlanChapter] = orm_relationship(
        "MemoirBookPlanChapter",
        back_populates="sections",
    )
    materials: Mapped[list["MemoirSectionMaterial"]] = orm_relationship(
        "MemoirSectionMaterial",
        back_populates="section",
        cascade="all, delete-orphan",
        order_by="MemoirSectionMaterial.sort_order",
    )


class MemoirSectionMaterial(Base):
    __tablename__ = "memoir_section_materials"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "utterance_id",
            name="uq_memoir_section_materials_project_utterance",
        ),
        UniqueConstraint(
            "section_id",
            "sort_order",
            name="uq_memoir_section_materials_section_order",
        ),
        ForeignKeyConstraint(
            ["project_id", "section_id"],
            ["memoir_sections.project_id", "memoir_sections.id"],
            name="fk_memoir_section_materials_project_section",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            f"assigned_by IN ({_enum_values(MemoirSectionMaterialAssignedBy)})",
            name="ck_memoir_section_materials_assigned_by",
        ),
        CheckConstraint(
            "assignment_confidence IS NULL OR (assignment_confidence >= 0 AND assignment_confidence <= 1)",
            name="ck_memoir_section_materials_confidence",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    section_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        index=True,
    )
    utterance_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("memoir_voice_utterances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    transcript_version_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("memoir_transcript_versions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    assigned_by: Mapped[str] = mapped_column(
        String(20),
        default=MemoirSectionMaterialAssignedBy.SYSTEM.value,
        nullable=False,
        index=True,
    )
    assignment_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    assignment_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    include_in_book: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utcnow_naive,
        onupdate=utcnow_naive,
    )

    section: Mapped[MemoirSection] = orm_relationship(
        "MemoirSection",
        back_populates="materials",
    )
    utterance: Mapped[MemoirVoiceUtterance] = orm_relationship("MemoirVoiceUtterance")
    transcript_version: Mapped[MemoirTranscriptVersion | None] = orm_relationship("MemoirTranscriptVersion")


class MemoirSectionDigest(Base):
    __tablename__ = "memoir_section_digests"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "section_id",
            "engine",
            "input_signature",
            name="uq_memoir_section_digests_signature",
        ),
        CheckConstraint(
            f"status IN ({_enum_values(MemoirSemanticCacheStatus)})",
            name="ck_memoir_section_digests_status",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    section_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("memoir_sections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    input_signature: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        index=True,
    )
    engine: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(20),
        default=MemoirSemanticCacheStatus.READY.value,
        nullable=False,
        index=True,
    )
    digest_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utcnow_naive,
        onupdate=utcnow_naive,
    )

    section: Mapped[MemoirSection] = orm_relationship("MemoirSection")


class MemoirCompletionIssue(Base):
    __tablename__ = "memoir_completion_issues"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "fingerprint",
            name="uq_memoir_completion_issues_project_fingerprint",
        ),
        ForeignKeyConstraint(
            ["project_id", "chapter_id"],
            [
                "memoir_book_plan_chapters.project_id",
                "memoir_book_plan_chapters.id",
            ],
            name="fk_memoir_completion_issues_project_chapter",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            f"scope IN ({_enum_values(MemoirCompletionIssueScope)})",
            name="ck_memoir_completion_issues_scope",
        ),
        CheckConstraint(
            f"issue_type IN ({_enum_values(MemoirCompletionIssueType)})",
            name="ck_memoir_completion_issues_type",
        ),
        CheckConstraint(
            f"channel IN ({_enum_values(MemoirCompletionIssueChannel)})",
            name="ck_memoir_completion_issues_channel",
        ),
        CheckConstraint(
            f"priority IN ({_enum_values(MemoirCompletionIssuePriority)})",
            name="ck_memoir_completion_issues_priority",
        ),
        CheckConstraint(
            f"status IN ({_enum_values(MemoirCompletionIssueStatus)})",
            name="ck_memoir_completion_issues_status",
        ),
        CheckConstraint(
            f"outcome = '' OR outcome IN ({_enum_values(MemoirCompletionIssueOutcome)})",
            name="ck_memoir_completion_issues_outcome",
        ),
        CheckConstraint(
            "(scope = 'chapter' AND chapter_id IS NOT NULL) "
            "OR (scope = 'book' "
            "AND chapter_id IS NULL)",
            name="ck_memoir_completion_issues_target",
        ),
        CheckConstraint(
            "(status = 'closed' AND outcome != '') OR (status != 'closed' AND outcome = '')",
            name="ck_memoir_completion_issues_lifecycle",
        ),
        CheckConstraint(
            "length(trim(fingerprint)) = 64",
            name="ck_memoir_completion_issues_fingerprint",
        ),
        CheckConstraint(
            "length(trim(question)) > 0",
            name="ck_memoir_completion_issues_question",
        ),
        Index(
            "ix_memoir_completion_issues_project_current",
            "project_id",
            "status",
            "priority",
            "created_at",
        ),
        Index(
            "ix_memoir_completion_issues_chapter_current",
            "project_id",
            "chapter_id",
            "status",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    scope: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    chapter_id: Mapped[str | None] = mapped_column(
        String(36),
        nullable=True,
        index=True,
    )
    issue_type: Mapped[str] = mapped_column(String(24), nullable=False)
    channel: Mapped[str] = mapped_column(String(24), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    priority: Mapped[str] = mapped_column(String(12), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    related_material_ids_json: Mapped[list] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    related_chapter_ids_json: Mapped[list] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    resolution: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    input_signature: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        default=MemoirCompletionIssueStatus.OPEN.value,
        nullable=False,
        index=True,
    )
    outcome: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    outcome_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    answered_material_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey(
            "memoir_voice_utterances.id",
            name="fk_memoir_completion_issues_answered_material",
            ondelete="SET NULL",
            use_alter=True,
        ),
        nullable=True,
        index=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utcnow_naive,
        onupdate=utcnow_naive,
    )

    chapter: Mapped[MemoirBookPlanChapter | None] = orm_relationship(
        "MemoirBookPlanChapter",
    )
    answered_material: Mapped[MemoirVoiceUtterance | None] = orm_relationship(
        "MemoirVoiceUtterance",
        foreign_keys=[answered_material_id],
    )


class MemoirChapterDigest(Base):
    __tablename__ = "memoir_chapter_digests"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "chapter_id",
            "engine",
            "input_signature",
            name="uq_memoir_chapter_digests_signature",
        ),
        CheckConstraint(
            f"status IN ({_enum_values(MemoirSemanticCacheStatus)})",
            name="ck_memoir_chapter_digests_status",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chapter_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("memoir_book_plan_chapters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    input_signature: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        index=True,
    )
    engine: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(20),
        default=MemoirSemanticCacheStatus.READY.value,
        nullable=False,
        index=True,
    )
    digest_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utcnow_naive,
        onupdate=utcnow_naive,
    )

    chapter: Mapped[MemoirBookPlanChapter] = orm_relationship("MemoirBookPlanChapter")


class MemoirBookReview(Base):
    __tablename__ = "memoir_book_reviews"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "engine",
            "input_signature",
            name="uq_memoir_book_reviews_signature",
        ),
        CheckConstraint(
            f"status IN ({_enum_values(MemoirSemanticCacheStatus)})",
            name="ck_memoir_book_reviews_status",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    input_signature: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        index=True,
    )
    engine: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(20),
        default=MemoirSemanticCacheStatus.READY.value,
        nullable=False,
        index=True,
    )
    review_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utcnow_naive,
        onupdate=utcnow_naive,
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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)

    chapter: Mapped[MemoirBookPlanChapter] = orm_relationship("MemoirBookPlanChapter", back_populates="draft")
    versions: Mapped[list["MemoirBookChapterDraftVersion"]] = orm_relationship(
        "MemoirBookChapterDraftVersion",
        back_populates="draft",
        cascade="all, delete-orphan",
        order_by="MemoirBookChapterDraftVersion.version",
    )


class MemoirBookChapterDraftVersion(Base):
    __tablename__ = "memoir_book_chapter_draft_versions"
    __table_args__ = (UniqueConstraint("draft_id", "version", name="uq_memoir_book_chapter_draft_versions_version"),)

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
    input_signature: Mapped[str] = mapped_column(
        String(128),
        default="",
        server_default=sql_text("''"),
        nullable=False,
        index=True,
    )
    used_material_ids_json: Mapped[list] = mapped_column(
        JSON,
        default=list,
        server_default=sql_text("'[]'"),
        nullable=False,
    )
    material_snapshot_json: Mapped[list] = mapped_column(
        JSON,
        default=list,
        server_default=sql_text("'[]'"),
        nullable=False,
    )
    section_snapshot_json: Mapped[list] = mapped_column(
        JSON,
        default=list,
        server_default=sql_text("'[]'"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)

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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)

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
    __table_args__ = (UniqueConstraint("output_id", "version", name="uq_memoir_book_output_versions_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    output_id: Mapped[str] = mapped_column(String(36), ForeignKey("memoir_book_outputs.id"), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    plan_id: Mapped[str] = mapped_column(String(36), ForeignKey("memoir_book_plans.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    chapter_snapshot_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    manuscript_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_by: Mapped[str] = mapped_column(String(20), default=MemoirCreatedBy.SYSTEM.value, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)

    output: Mapped[MemoirBookOutput] = orm_relationship(
        "MemoirBookOutput",
        back_populates="versions",
        foreign_keys=[output_id],
    )


class MemoirBookShare(Base):
    __tablename__ = "memoir_book_shares"

    output_id: Mapped[str] = mapped_column(String(36), ForeignKey("memoir_book_outputs.id", ondelete="CASCADE"), primary_key=True)
    version_id: Mapped[str] = mapped_column(String(36), ForeignKey("memoir_book_output_versions.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)


class MemoirExcerptShare(Base):
    allow_book_link: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    __tablename__ = "memoir_excerpt_shares"
    __table_args__ = (CheckConstraint("char_length(content) BETWEEN 1 AND 512", name="ck_memoir_excerpt_share_length"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    chapter_id: Mapped[str] = mapped_column(String(36), ForeignKey("memoir_book_plan_chapters.id", ondelete="CASCADE"), nullable=False)
    version_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("memoir_book_chapter_draft_versions.id", ondelete="SET NULL"), nullable=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    book_title: Mapped[str] = mapped_column(Text, nullable=False)
    chapter_title: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    summary: Mapped[str] = mapped_column(String(48), nullable=False, default="", server_default="")
    image_asset_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("memoir_image_assets.id", ondelete="SET NULL"), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)


class MemoirChapterShare(Base):
    __tablename__ = "memoir_chapter_shares"
    __table_args__ = (CheckConstraint("card_excerpt_start >= 0 AND card_excerpt_length BETWEEN 0 AND 48", name="ck_memoir_chapter_shares_card_excerpt"),)

    card_version_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("memoir_book_chapter_draft_versions.id", ondelete="SET NULL"), nullable=True)
    card_excerpt_start: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    card_excerpt_length: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)

    chapter_id: Mapped[str] = mapped_column(String(36), ForeignKey("memoir_book_plan_chapters.id", ondelete="CASCADE"), primary_key=True)
    version_id: Mapped[str] = mapped_column(String(36), ForeignKey("memoir_book_chapter_draft_versions.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)


class MemoirSectionShare(Base):
    __tablename__ = "memoir_section_shares"
    __table_args__ = (CheckConstraint("card_excerpt_start >= 0 AND card_excerpt_length BETWEEN 0 AND 48", name="ck_memoir_section_shares_card_excerpt"),)

    card_version_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("memoir_book_chapter_draft_versions.id", ondelete="SET NULL"), nullable=True)
    card_excerpt_start: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    card_excerpt_length: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)

    section_id: Mapped[str] = mapped_column(String(36), ForeignKey("memoir_sections.id", ondelete="CASCADE"), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)


class MemoirShareLike(Base):
    """One family member's like on a shared book, chapter or story video."""

    __tablename__ = "memoir_share_likes"
    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(book_output_id, chapter_id, section_id, share_video_id) = 1",
            name="ck_memoir_share_likes_one_target",
        ),
        UniqueConstraint("user_id", "book_output_id", name="uq_memoir_share_likes_user_book"),
        UniqueConstraint("user_id", "chapter_id", name="uq_memoir_share_likes_user_chapter"),
        UniqueConstraint("user_id", "section_id", name="uq_memoir_share_likes_user_section"),
        UniqueConstraint("user_id", "share_video_id", name="uq_memoir_share_likes_user_video"),
        Index("ix_memoir_share_likes_project_created", "project_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # Likes belong to the content, so they survive link renewal and new versions.
    book_output_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memoir_book_outputs.id", ondelete="CASCADE"), nullable=True, index=True,
    )
    chapter_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memoir_book_plan_chapters.id", ondelete="CASCADE"), nullable=True, index=True,
    )
    section_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memoir_sections.id", ondelete="CASCADE"), nullable=True, index=True,
    )
    share_video_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memoir_material_share_videos.id", ondelete="CASCADE"), nullable=True, index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)


class MemoirShareResponse(Base):
    """Private family contributions; adoption is an explicit author action."""
    __tablename__ = "memoir_share_responses"
    __table_args__ = (
        UniqueConstraint("user_id", "request_id", name="uq_memoir_share_responses_request"),
        CheckConstraint("target_kind IN ('book','chapter','story','video','excerpt')", name="ck_memoir_share_responses_target"),
        Index("ix_memoir_share_responses_project_created", "project_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    request_id: Mapped[str] = mapped_column(String(80), nullable=False)
    target_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    # Identity and title are retained if the author later removes the shared object.
    target_id: Mapped[str] = mapped_column(String(36), nullable=False)
    target_title: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    voice_capture_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memoir_share_response_voices.id", ondelete="SET NULL"), unique=True, nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    can_feature: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    featured_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    featured_chapter_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("memoir_book_plan_chapters.id", ondelete="SET NULL"))
    adopted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    adopted_utterance_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memoir_voice_utterances.id", ondelete="SET NULL"), nullable=True,
    )


class MemoirShareResponseVoice(Base):
    """A visitor-owned original recording with immutable ASR text and source timing."""
    __tablename__ = "memoir_share_response_voices"
    __table_args__ = (
        CheckConstraint("target_kind IN ('book','chapter','story','video','excerpt')", name="ck_memoir_share_response_voices_target"),
        Index("ix_memoir_share_response_voices_project_created", "project_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    target_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    target_id: Mapped[str] = mapped_column(String(36), nullable=False)
    share_key: Mapped[str] = mapped_column(String(64), nullable=False)
    audio_file_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("audio_files.id"), nullable=True)
    asr_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    provider_payload_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    start_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class MemoirBookRevisionProposal(Base):
    __tablename__ = "memoir_book_revision_proposals"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_enum_values(MemoirBookRevisionProposalStatus)})",
            name="ck_memoir_book_revision_proposals_status",
        ),
        Index(
            "ix_memoir_book_revision_proposals_project_status",
            "project_id",
            "status",
            "updated_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    review_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("memoir_book_reviews.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    review_input_signature: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        index=True,
    )
    base_output_version_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("memoir_book_output_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        default=MemoirBookRevisionProposalStatus.READY.value,
        nullable=False,
        index=True,
    )
    selected_actions_json: Mapped[dict] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    phase: Mapped[str] = mapped_column(
        String(20),
        default=MemoirBookRevisionProposalPhase.PLAN_READY.value,
        server_default=MemoirBookRevisionProposalPhase.PLAN_READY.value,
        nullable=False,
    )
    revision_plan_json: Mapped[dict] = mapped_column(
        JSON,
        default=dict,
        server_default=sql_text("'{}'"),
        nullable=False,
    )
    candidate_chapter_snapshot_json: Mapped[list] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    generation_json: Mapped[dict] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    result_output_version_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("memoir_book_output_versions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utcnow_naive,
        onupdate=utcnow_naive,
    )
