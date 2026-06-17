from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirBookPlanStatus,
    MemoirCreatedBy,
)


class MemoirBookChapterSourceRef(BaseModel):
    voice_session_id: str
    message_id: str
    utterance_id: str
    transcript_version_id: str | None = None
    audio_file_id: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    quote: str = ""


class MemoirBookPlanChapterInput(BaseModel):
    title: str
    summary: str = ""
    source_refs: list[MemoirBookChapterSourceRef] = Field(default_factory=list)

    @field_validator("title")
    @classmethod
    def _valid_title(cls, value: str) -> str:
        title = value.strip()
        if not title:
            raise ValueError("章节标题不能为空")
        if len(title) > 80:
            raise ValueError("章节标题不能超过 80 个字")
        return title

    @field_validator("summary")
    @classmethod
    def _valid_summary(cls, value: str) -> str:
        return value.strip()


class MemoirBookPlanGenerateRequest(BaseModel):
    ai_model: str | None = None
    max_chapters: int = 8

    @field_validator("max_chapters")
    @classmethod
    def _valid_max_chapters(cls, value: int) -> int:
        if value < 1 or value > 12:
            raise ValueError("章节数量需要在 1 到 12 之间")
        return value


class MemoirBookPlanConfirmRequest(BaseModel):
    title: str | None = None
    chapters: list[MemoirBookPlanChapterInput]

    @field_validator("title")
    @classmethod
    def _valid_optional_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        title = value.strip()
        if not title:
            return None
        if len(title) > 120:
            raise ValueError("规划标题不能超过 120 个字")
        return title


class MemoirBookPlanChapterResponse(BaseModel):
    id: str
    plan_id: str
    project_id: str
    title: str
    summary: str
    sort_order: int
    source_refs: list[MemoirBookChapterSourceRef] = Field(default_factory=list)
    metadata_json: dict[str, object] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class MemoirBookPlanResponse(BaseModel):
    id: str
    project_id: str
    title: str
    status: MemoirBookPlanStatus
    created_by: MemoirCreatedBy
    model: str | None = None
    metadata_json: dict[str, object] = Field(default_factory=dict)
    chapters: list[MemoirBookPlanChapterResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class MemoirBookChapterDraftGenerateRequest(BaseModel):
    ai_model: str | None = None
    force_regenerate: bool = False
    feedback: str | None = None

    @field_validator("feedback")
    @classmethod
    def _valid_feedback(cls, value: str | None) -> str | None:
        if value is None:
            return None
        feedback = value.strip()
        return feedback or None


class MemoirBookChapterDraftUpdateRequest(BaseModel):
    content: str

    @field_validator("content")
    @classmethod
    def _valid_content(cls, value: str) -> str:
        content = value.strip()
        if not content:
            raise ValueError("章节稿内容不能为空")
        return content


class MemoirBookChapterDraftVersionResponse(BaseModel):
    id: str
    draft_id: str
    project_id: str
    chapter_id: str
    version: int
    content: str
    created_by: MemoirCreatedBy
    model: str | None = None
    prompt_snapshot: str
    feedback: str | None = None
    created_at: datetime


class MemoirBookChapterDraftResponse(BaseModel):
    id: str
    project_id: str
    chapter_id: str
    status: MemoirBookChapterDraftStatus
    metadata_json: dict[str, object] = Field(default_factory=dict)
    latest_version: MemoirBookChapterDraftVersionResponse | None = None
    versions: list[MemoirBookChapterDraftVersionResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
