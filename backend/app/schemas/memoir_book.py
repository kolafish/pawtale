from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirBookPlanStatus,
    MemoirCreatedBy,
    MemoirOutputStatus,
)
from app.schemas.memoir import MemoirTTSAlignmentSegmentResponse


class MemoirBookChapterSourceRef(BaseModel):
    voice_session_id: str
    message_id: str
    utterance_id: str
    transcript_version_id: str | None = None
    audio_file_id: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    created_at: datetime | None = None
    quote: str = ""


class MemoirBookSourceIncludedChapter(BaseModel):
    chapter_id: str
    title: str
    sort_order: int


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


class MemoirBookPlanChapterUpdateInput(MemoirBookPlanChapterInput):
    id: str | None = None

    @field_validator("id")
    @classmethod
    def _valid_optional_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        chapter_id = value.strip()
        return chapter_id or None


class MemoirBookPlanUpdateRequest(BaseModel):
    title: str | None = None
    chapters: list[MemoirBookPlanChapterUpdateInput]

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


class MemoirBookChapterTitleUpdateRequest(BaseModel):
    title: str

    @field_validator("title")
    @classmethod
    def _valid_title(cls, value: str) -> str:
        title = value.strip()
        if not title:
            raise ValueError("章节标题不能为空")
        if len(title) > 80:
            raise ValueError("章节标题不能超过 80 个字")
        return title


class MemoirBookChapterTitleGenerateRequest(BaseModel):
    ai_model: str | None = None


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


class MemoirBookSourceCoverageItem(BaseModel):
    source_index: int
    voice_session_id: str
    message_id: str
    utterance_id: str
    transcript_version_id: str | None = None
    audio_file_id: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    text: str
    created_at: datetime
    is_included: bool = False
    included_in_chapters: list[MemoirBookSourceIncludedChapter] = Field(default_factory=list)


class MemoirBookSourceCoverageResponse(BaseModel):
    project_id: str
    plan_id: str | None = None
    total_source_count: int
    covered_source_count: int
    uncovered_source_count: int
    sources: list[MemoirBookSourceCoverageItem] = Field(default_factory=list)
    uncovered_sources: list[MemoirBookSourceCoverageItem] = Field(default_factory=list)


class MemoirBookSourceAssignmentInput(BaseModel):
    chapter_id: str
    utterance_ids: list[str]

    @field_validator("chapter_id")
    @classmethod
    def _valid_chapter_id(cls, value: str) -> str:
        chapter_id = value.strip()
        if not chapter_id:
            raise ValueError("章节不能为空")
        return chapter_id

    @field_validator("utterance_ids")
    @classmethod
    def _valid_utterance_ids(cls, value: list[str]) -> list[str]:
        utterance_ids = [item.strip() for item in value if item.strip()]
        if not utterance_ids:
            raise ValueError("至少选择一条转写")
        return list(dict.fromkeys(utterance_ids))


class MemoirBookSourceAssignmentRequest(BaseModel):
    assignments: list[MemoirBookSourceAssignmentInput]

    @field_validator("assignments")
    @classmethod
    def _valid_assignments(cls, value: list[MemoirBookSourceAssignmentInput]) -> list[MemoirBookSourceAssignmentInput]:
        if not value:
            raise ValueError("至少需要一条归入操作")
        return value


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


class MemoirBookAudioRequest(BaseModel):
    voice: str | None = None
    speed_ratio: float | None = None
    force_regenerate: bool = False

    @field_validator("voice")
    @classmethod
    def _valid_voice(cls, value: str | None) -> str | None:
        if value is None:
            return None
        voice = value.strip()
        return voice or None

    @field_validator("speed_ratio")
    @classmethod
    def _valid_speed_ratio(cls, value: float | None) -> float | None:
        if value is None:
            return None
        return max(0.5, min(2.0, round(float(value), 2)))


class MemoirBookAudioResponse(BaseModel):
    audio_file_ids: list[str]
    audio_file_durations_seconds: list[float | None] = Field(default_factory=list)
    provider: str
    model: str
    mime_type: str
    chunk_count: int
    source_text_hash: str
    duration_seconds: float | None = None
    alignment_provider: str | None = None
    alignment: list[MemoirTTSAlignmentSegmentResponse] = Field(default_factory=list)


class MemoirBookOutputGenerateRequest(BaseModel):
    force_regenerate: bool = False


class MemoirBookOutputVersionResponse(BaseModel):
    id: str
    output_id: str
    project_id: str
    plan_id: str
    version: int
    content: str
    chapter_snapshot_json: list[dict[str, object]] = Field(default_factory=list)
    created_by: MemoirCreatedBy
    created_at: datetime


class MemoirBookOutputResponse(BaseModel):
    id: str
    project_id: str
    plan_id: str
    title: str
    status: MemoirOutputStatus
    metadata_json: dict[str, object] = Field(default_factory=dict)
    is_stale: bool = False
    stale_chapter_titles: list[str] = Field(default_factory=list)
    latest_version: MemoirBookOutputVersionResponse | None = None
    versions: list[MemoirBookOutputVersionResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
