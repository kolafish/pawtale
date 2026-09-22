from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirBookPlanStatus,
    MemoirCreatedBy,
    MemoirOutputStatus,
)
from app.schemas.memoir_voice import (
    MemoirAutomationEventResponse as MemoirAutomationEventResponse,
    MemoirTTSAlignmentSegmentResponse,
)
from app.schemas.memoir_sections import (
    MemoirChapterDraftSectionResponse,
    MemoirChapterSectionSummary,
)


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
    include_in_book: bool = True


class MemoirBookPlanChapterInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    include_in_book: bool = True
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
    chapters: list[MemoirBookPlanChapterInput] = Field(min_length=1, max_length=12)

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
    chapters: list[MemoirBookPlanChapterUpdateInput] = Field(min_length=1, max_length=12)

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


class MemoirBookPlanStructureCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal[
        "delete_chapter",
        "merge_chapters",
        "move_chapter",
        "rename_chapter",
        "set_chapter_book_inclusion",
        "split_chapter",
    ]
    chapter_id: str | None = None
    target_chapter_id: str | None = None
    direction: Literal["up", "down"] | None = None
    target_position: Literal["first", "last"] | None = None
    target_index: int | None = Field(default=None, ge=1, le=99)
    relative_chapter_id: str | None = None
    relative_position: Literal["before", "after"] | None = None
    title: str | None = None
    include_in_book: bool | None = None
    utterance_ids: list[str] = Field(default_factory=list)

    @field_validator("chapter_id", "target_chapter_id", "relative_chapter_id")
    @classmethod
    def _valid_optional_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("title")
    @classmethod
    def _valid_optional_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        title = value.strip()
        if not title:
            return None
        if len(title) > 80:
            raise ValueError("章节标题不能超过 80 个字")
        return title

    @field_validator("utterance_ids")
    @classmethod
    def _valid_utterance_ids(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))


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
    sort_order: int
    include_in_book: bool = True
    source_refs: list[MemoirBookChapterSourceRef] = Field(default_factory=list)
    sections: list[MemoirChapterSectionSummary] = Field(default_factory=list)
    metadata_json: dict[str, object] = Field(default_factory=dict)
    pending_source_count: int = 0
    pending_completion_source_count: int = 0
    pending_new_source_count: int = 0
    removed_source_count: int = 0
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


class MemoirBookChapterDraftVersionResponse(BaseModel):
    id: str
    draft_id: str
    project_id: str
    chapter_id: str
    version: int
    content: str
    sections: list[MemoirChapterDraftSectionResponse] = Field(
        default_factory=list
    )
    created_by: MemoirCreatedBy
    model: str | None = None
    prompt_snapshot: str
    feedback: str | None = None
    created_at: datetime


class MemoirBookChapterDraftSectionRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_id: str = Field(min_length=1, max_length=36)
    content: str = Field(min_length=1, max_length=100_000)

    @field_validator("section_id", "content")
    @classmethod
    def _strip_section_revision(cls, value: str) -> str:
        return value.strip()


class MemoirBookChapterDraftRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version_id: str = Field(min_length=1, max_length=36)
    sections: list[MemoirBookChapterDraftSectionRevision] = Field(
        min_length=1,
        max_length=100,
    )

    @field_validator("expected_version_id")
    @classmethod
    def _strip_expected_version_id(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def _unique_section_ids(self):
        section_ids = [section.section_id for section in self.sections]
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("section_id 不能重复")
        if sum(len(section.content) for section in self.sections) > 200_000:
            raise ValueError("整章正文不能超过 200000 个字符")
        return self


class MemoirBookChapterDraftResponse(BaseModel):
    id: str
    project_id: str
    chapter_id: str
    status: MemoirBookChapterDraftStatus
    metadata_json: dict[str, object] = Field(default_factory=dict)
    pending_source_count: int = 0
    removed_source_count: int = 0
    latest_version: MemoirBookChapterDraftVersionResponse | None = None
    versions: list[MemoirBookChapterDraftVersionResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class MemoirBookAssemblyResponse(BaseModel):
    omitted_chapter_ids: list[str] = Field(default_factory=list)
    preserved_manual: bool = False
    project_id: str
    output_id: str
    output_version_id: str
    version: int
    content: str
    chapter_snapshot_json: list[dict[str, object]]
    created_at: datetime


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


MemoirBookCoverStyle = Literal["classic_plain", "warm_gray", "dark_album", "moonlit_courtyard", "navy_gilt"]


class MemoirBookOutputUpdateRequest(BaseModel):
    title: str | None = None
    subtitle: str | None = None
    narrator_name: str | None = None
    prepared_by: str | None = None
    preface: str | None = None
    afterword: str | None = None
    back_cover_quote: str | None = None
    cover_style: MemoirBookCoverStyle | None = None

    @field_validator("title")
    @classmethod
    def _valid_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        title = value.strip()
        if not title:
            raise ValueError("书名不能为空")
        return title

    @field_validator("subtitle", "narrator_name", "prepared_by", "preface", "afterword", "back_cover_quote")
    @classmethod
    def _valid_optional_book_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()


MemoirBookEditorialCopyTarget = Literal[
    "preface",
    "afterword",
    "back_cover_quote",
    "chapter_intros",
    "pull_quotes",
]


class MemoirBookOutputEditorialCopyRegenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ai_model: str | None = None
    targets: list[MemoirBookEditorialCopyTarget] = Field(default_factory=list)
    chapter_ids: list[str] = Field(default_factory=list)
    feedback: str | None = None

    @field_validator("ai_model", "feedback")
    @classmethod
    def _valid_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return text or None

    @field_validator("chapter_ids")
    @classmethod
    def _valid_chapter_ids(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))

    @field_validator("targets")
    @classmethod
    def _valid_targets(cls, value: list[MemoirBookEditorialCopyTarget]) -> list[MemoirBookEditorialCopyTarget]:
        return list(dict.fromkeys(value))


class MemoirBookOutputChapterEditorialCopyUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intro: str | None = None
    pull_quote: str | None = None
    pull_quote_source_ref_id: str | None = None

    @field_validator("intro", "pull_quote", "pull_quote_source_ref_id")
    @classmethod
    def _valid_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()


class MemoirBookTitleSuggestionRequest(BaseModel):
    ai_model: str | None = None
    count: int = 6

    @field_validator("ai_model")
    @classmethod
    def _valid_ai_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        model = value.strip()
        return model or None

    @field_validator("count")
    @classmethod
    def _valid_count(cls, value: int) -> int:
        return max(3, min(8, int(value)))


class MemoirBookTitleSuggestionResponse(BaseModel):
    suggestions: list[str]
    provider: str | None = None
    model: str | None = None


class MemoirBookOutputSectionTableOfContentsItemResponse(BaseModel):
    section_id: str
    index: int
    title: str
    char_count: int = 0
    source_count: int = 0


class MemoirBookOutputSectionResponse(
    MemoirBookOutputSectionTableOfContentsItemResponse
):
    section_type: str = ""
    content: str
    source_ref_ids: list[str] = Field(default_factory=list)


class MemoirBookOutputTableOfContentsItemResponse(BaseModel):
    chapter_id: str
    index: int
    title: str
    char_count: int = 0
    source_count: int = 0
    sections: list[MemoirBookOutputSectionTableOfContentsItemResponse] = Field(
        default_factory=list
    )


class MemoirBookManuscriptProfile(BaseModel):
    template: Literal["classic_family", "photo_memory", "oral_archive"] = "classic_family"
    title: str
    subtitle: str = ""
    narrator_name: str = ""
    prepared_by: str = "家人整理"
    interview_date_range: str = ""
    privacy_note: str = "本书仅供家庭内部保存与阅读。未经本人或家属同意，不得公开传播。"
    ai_disclosure_note: str = (
        "本书根据口述访谈整理而成。AI 的作用包括章节归类、语句通顺、目录梳理和标题生成，"
        "不主动新增未经口述确认的事实。"
    )


class MemoirBookManuscriptSection(BaseModel):
    kind: str
    title: str
    content: str = ""
    enabled: bool = True


class MemoirBookManuscriptQuote(BaseModel):
    text: str
    source_ref_ids: list[str] = Field(default_factory=list)
    source: Literal["original_quote", "editorial_generated", "manual"] = "original_quote"


class MemoirBookManuscriptPersonNote(BaseModel):
    name: str
    note: str = ""


class MemoirBookManuscriptUnconfirmedFact(BaseModel):
    text: str
    source_ref_ids: list[str] = Field(default_factory=list)


class MemoirBookManuscriptSourceRef(BaseModel):
    utterance_id: str = ""
    transcript_version_id: str | None = None
    voice_session_id: str | None = None
    quote: str = ""


class MemoirBookManuscriptIllustration(BaseModel):
    image_asset_id: str
    storage_path: str
    mime_type: str = ""
    caption: str = ""
    alt_text: str = ""
    placement: Literal["chapter_opening", "chapter_inline", "appendix", "cover", "back_cover"] = "chapter_opening"
    display_size: str = "full_width"
    source_quote: str = ""
    source_utterance_id: str | None = None
    section_id: str | None = None
    disclosure: str = "AI 根据讲述内容生成的意象插图，不是真实照片。"


class MemoirBookManuscriptChapter(BaseModel):
    chapter_id: str
    index: int
    title: str
    subtitle: str = ""
    intro: str = ""
    time_hint: str = ""
    place_hint: str = ""
    pull_quotes: list[MemoirBookManuscriptQuote] = Field(default_factory=list)
    people_notes: list[MemoirBookManuscriptPersonNote] = Field(default_factory=list)
    uncertain_facts: list[MemoirBookManuscriptUnconfirmedFact] = Field(default_factory=list)
    content: str
    sections: list[MemoirBookOutputSectionResponse] = Field(default_factory=list)
    source_refs: list[MemoirBookManuscriptSourceRef] = Field(default_factory=list)
    illustrations: list[MemoirBookManuscriptIllustration] = Field(default_factory=list)


class MemoirBookManuscriptRenderSettings(BaseModel):
    page_size: Literal["a5"] = "a5"
    layout: Literal["reading", "print"] = "reading"
    font_scale: Literal["standard", "large"] = "standard"
    show_headers: bool = True
    show_source_appendix: bool = True
    photo_mode: Literal["none", "chapter", "appendix"] = "none"
    cover_style: MemoirBookCoverStyle = "classic_plain"
    back_cover_quote: str = ""
    editorial_copy_model: str | None = None


class MemoirBookManuscriptBackMatter(BaseModel):
    afterword: MemoirBookManuscriptSection
    interview_records: MemoirBookManuscriptSection
    version_note: MemoirBookManuscriptSection
    unconfirmed_facts: MemoirBookManuscriptSection
    source_appendix: MemoirBookManuscriptSection


class MemoirBookManuscript(BaseModel):
    schema_version: int = 2
    profile: MemoirBookManuscriptProfile
    front_matter: list[MemoirBookManuscriptSection] = Field(default_factory=list)
    table_of_contents: list[MemoirBookOutputTableOfContentsItemResponse] = Field(default_factory=list)
    chapters: list[MemoirBookManuscriptChapter] = Field(default_factory=list)
    image_appendix: list[MemoirBookManuscriptIllustration] = Field(default_factory=list)
    back_matter: MemoirBookManuscriptBackMatter
    render_settings: MemoirBookManuscriptRenderSettings = Field(default_factory=MemoirBookManuscriptRenderSettings)


class MemoirBookOutputVersionResponse(BaseModel):
    id: str
    version: int
    content: str
    manuscript: MemoirBookManuscript | None = None
    created_by: MemoirCreatedBy
    created_at: datetime


class MemoirBookOutputVersionSummaryResponse(BaseModel):
    id: str
    version: int
    created_by: MemoirCreatedBy
    created_at: datetime


class MemoirBookOutputResponse(BaseModel):
    id: str
    project_id: str
    plan_id: str
    title: str
    status: MemoirOutputStatus
    is_stale: bool = False
    stale_chapter_titles: list[str] = Field(default_factory=list)
    latest_version: MemoirBookOutputVersionResponse | None = None
    versions: list[MemoirBookOutputVersionSummaryResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class MemoirBookShareStatusResponse(BaseModel):
    active: bool
    expires_at: datetime | None = None


class MemoirBookShareCreateResponse(BaseModel):
    token: str
    expires_at: datetime


class MemoirBookShareReadRequest(BaseModel):
    token: str
