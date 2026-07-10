from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class AuditSession(Base):
    __tablename__ = "audit_sessions"
    __table_args__ = (
        UniqueConstraint("user_id", "client_session_id", name="uq_audit_sessions_user_client_session"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    client_session_id: Mapped[str] = mapped_column(String(120), nullable=False)
    platform: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    active_ms: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    exit_page_path: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    exit_feature_code: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    exit_reason: Mapped[str] = mapped_column(String(60), default="", nullable=False)
    app_version: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    device_summary: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user: Mapped["User"] = relationship("User")
    events: Mapped[list["AuditEvent"]] = relationship(
        "AuditEvent",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="AuditEvent.occurred_at",
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        UniqueConstraint("user_id", "client_event_id", name="uq_audit_events_user_client_event"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("audit_sessions.id"), nullable=False, index=True)
    client_event_id: Mapped[str] = mapped_column(String(120), nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    event_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    platform: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    page_path: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    feature_code: Mapped[str] = mapped_column(String(120), default="", nullable=False, index=True)
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    voice_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    active_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    exit_reason: Mapped[str] = mapped_column(String(60), default="", nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship("User")
    session: Mapped[AuditSession] = relationship("AuditSession", back_populates="events")


class AuditDailyUserUsage(Base):
    __tablename__ = "audit_daily_user_usage"
    __table_args__ = (
        UniqueConstraint("user_id", "date", "platform", name="uq_audit_daily_user_usage_user_date_platform"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    platform: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    active_ms: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    session_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    event_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_exit_page_path: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    last_exit_feature_code: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    last_exit_reason: Mapped[str] = mapped_column(String(60), default="", nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user: Mapped["User"] = relationship("User")


class AuditDailyFeatureUsage(Base):
    __tablename__ = "audit_daily_feature_usage"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "date",
            "platform",
            "feature_code",
            name="uq_audit_daily_feature_usage_user_date_platform_feature",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    platform: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    feature_code: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    active_ms: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    event_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user: Mapped["User"] = relationship("User")
