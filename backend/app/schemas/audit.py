from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


AuditPlatform = Literal["miniprogram", "web"]
AuditEventType = Literal["page_view", "page_leave", "feature_start", "feature_end", "action", "heartbeat"]
AuditExitReason = Literal["app_background", "page_unload", "logout", "timeout", "navigate", "unknown"]


class AuditEventIngestItem(BaseModel):
    client_event_id: str
    event_type: AuditEventType
    occurred_at: datetime
    event_date: date | None = None
    page_path: str = ""
    feature_code: str = ""
    project_id: str | None = None
    voice_session_id: str | None = None
    duration_ms: int = Field(default=0, ge=0)
    exit_reason: AuditExitReason | str = ""
    metadata_json: dict[str, Any] = Field(default_factory=dict)

    @field_validator("client_event_id", "page_path", "feature_code", "exit_reason", mode="before")
    @classmethod
    def _trim_optional_text(cls, value: object) -> str:
        return value.strip() if isinstance(value, str) else ""

    @field_validator("client_event_id")
    @classmethod
    def _required_client_event_id(cls, value: str) -> str:
        if not value:
            raise ValueError("client_event_id 不能为空")
        if len(value) > 120:
            raise ValueError("client_event_id 不能超过 120 个字符")
        return value


class AuditEventsIngestRequest(BaseModel):
    client_session_id: str
    platform: AuditPlatform
    app_version: str = ""
    device_summary: str = ""
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    events: list[AuditEventIngestItem] = Field(default_factory=list, min_length=1, max_length=100)

    @field_validator("client_session_id", "app_version", "device_summary", mode="before")
    @classmethod
    def _trim_text(cls, value: object) -> str:
        return value.strip() if isinstance(value, str) else ""

    @field_validator("client_session_id")
    @classmethod
    def _required_client_session_id(cls, value: str) -> str:
        if not value:
            raise ValueError("client_session_id 不能为空")
        if len(value) > 120:
            raise ValueError("client_session_id 不能超过 120 个字符")
        return value


class AuditEventsIngestResponse(BaseModel):
    accepted: int
    duplicate: int
    session_id: str


class AuditUserBrief(BaseModel):
    id: str
    email: str
    display_name: str
    role: str = "user"


class AuditDashboardSummary(BaseModel):
    active_ms: int
    session_count: int
    event_count: int
    user_count: int
    top_exit_page_path: str | None = None
    top_exit_feature_code: str | None = None


class AuditDailyUserUsageResponse(BaseModel):
    user: AuditUserBrief
    date: date
    platform: str
    active_ms: int
    session_count: int
    event_count: int
    last_exit_page_path: str
    last_exit_feature_code: str
    last_exit_reason: str
    last_seen_at: datetime | None = None


class AuditDailyFeatureUsageResponse(BaseModel):
    user: AuditUserBrief
    date: date
    platform: str
    feature_code: str
    active_ms: int
    event_count: int
    last_seen_at: datetime | None = None


class AuditExitLocationResponse(BaseModel):
    page_path: str
    feature_code: str
    reason: str
    count: int


class AuditDashboardResponse(BaseModel):
    summary: AuditDashboardSummary
    daily_users: list[AuditDailyUserUsageResponse]
    feature_usage: list[AuditDailyFeatureUsageResponse]
    exit_locations: list[AuditExitLocationResponse]


class AuditEventResponse(BaseModel):
    id: str
    client_event_id: str
    event_type: str
    event_date: date
    platform: str
    page_path: str
    feature_code: str
    project_id: str | None = None
    voice_session_id: str | None = None
    occurred_at: datetime
    duration_ms: int
    active_ms: int
    exit_reason: str
    metadata_json: dict[str, Any] = Field(default_factory=dict)

    class Config:
        from_attributes = True


class AuditSessionResponse(BaseModel):
    id: str
    client_session_id: str
    platform: str
    started_at: datetime
    last_seen_at: datetime
    ended_at: datetime | None = None
    active_ms: int
    exit_page_path: str
    exit_feature_code: str
    exit_reason: str
    app_version: str
    device_summary: str
    events: list[AuditEventResponse] = Field(default_factory=list)


class AuditUserDayResponse(BaseModel):
    user: AuditUserBrief
    date: date
    sessions: list[AuditSessionResponse]
