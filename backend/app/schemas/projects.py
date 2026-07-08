from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator

from app.domain.projects.types import ProjectType
from app.domain.references import (
    ReferenceType,
    SettingReferenceType,
    coerce_setting_reference_type,
)


# ── Project ──────────────────────────────────────────

class ProjectCreate(BaseModel):
    type: ProjectType
    title: str
    description: str | None = None
    word_count_target: int | None = None
    daily_word_goal: int | None = None


class ProjectUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    word_count_target: int | None = None
    daily_word_goal: int | None = None


class DocumentMeta(BaseModel):
    """Document metadata returned inside project detail (no content)."""
    id: str
    group_id: str
    name: str
    status: str = "draft"
    label: str | None = None
    word_count: int = 0
    sort_order: int = 0
    updated_at: datetime

    class Config:
        from_attributes = True


class GroupResponse(BaseModel):
    id: str
    name: str
    sort_order: int = 0
    documents: list[DocumentMeta] = Field(default_factory=list)

    class Config:
        from_attributes = True


class ReferenceItemBrief(BaseModel):
    id: str
    type: ReferenceType
    name: str
    sort_order: int = 0

    class Config:
        from_attributes = True


class ProjectListItem(BaseModel):
    id: str
    type: ProjectType
    title: str
    description: str | None = None
    word_count_target: int | None = None
    owner_user_id: str | None = None
    owner_email: str | None = None
    owner_display_name: str | None = None
    can_edit: bool = True
    can_edit_memoir_materials: bool = True
    total_word_count: int = 0
    group_count: int = 0
    document_count: int = 0
    metadata_json: dict = Field(default_factory=dict)
    updated_at: datetime

    class Config:
        from_attributes = True


class ProjectResponse(BaseModel):
    id: str
    type: ProjectType
    title: str
    description: str | None = None
    word_count_target: int | None = None
    daily_word_goal: int | None = None
    owner_user_id: str | None = None
    owner_email: str | None = None
    owner_display_name: str | None = None
    can_edit: bool = True
    can_edit_memoir_materials: bool = True
    metadata_json: dict = Field(default_factory=dict)
    total_word_count: int = 0
    groups: list[GroupResponse] = Field(default_factory=list)
    references: list[ReferenceItemBrief] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ── Document Group ───────────────────────────────────

class GroupCreate(BaseModel):
    name: str


class GroupUpdate(BaseModel):
    name: str | None = None


class GroupReorderItem(BaseModel):
    id: str
    sort_order: int


# ── Document ────────────────────────────────────────

class DocumentCreate(BaseModel):
    name: str = "未命名文档"


class DocumentUpdate(BaseModel):
    name: str | None = None
    content: str | None = None
    synopsis: str | None = None
    status: str | None = None  # draft/revision/done
    label: str | None = None
    word_count_target: int | None = None


class DocumentResponse(BaseModel):
    id: str
    group_id: str
    name: str
    content: str
    synopsis: str | None = None
    status: str = "draft"
    label: str | None = None
    word_count_target: int | None = None
    word_count: int = 0
    sort_order: int = 0
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class DocumentReorderItem(BaseModel):
    id: str
    group_id: str
    sort_order: int


# ── Reference Item ──────────────────────────────────

class ReferenceItemCreate(BaseModel):
    type: ReferenceType
    name: str
    content: str = ""
    metadata_json: dict = Field(default_factory=dict)


class ReferenceItemUpdate(BaseModel):
    name: str | None = None
    content: str | None = None
    metadata_json: dict | None = None


class ReferenceItemResponse(BaseModel):
    id: str
    project_id: str
    type: ReferenceType
    name: str
    content: str
    metadata_json: dict = Field(default_factory=dict)
    sort_order: int = 0
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ── Document References (links) ────────────────────

class DocumentReferencesUpdate(BaseModel):
    reference_ids: list[str]


# ── Writing Session ─────────────────────────────────

class WritingSessionCreate(BaseModel):
    date: date
    words_written: int


class WritingSessionResponse(BaseModel):
    id: str
    project_id: str
    date: date
    words_written: int
    created_at: datetime

    class Config:
        from_attributes = True


# ── Export ───────────────────────────────────────────

class ProjectExportResponse(BaseModel):
    title: str
    sections: list[dict]  # [{group: str, documents: [{name, content}]}]
    total_words: int
    references: list[dict] = Field(default_factory=list)


# ── GitHub one-way sync ─────────────────────────────

class GitSyncInstallationResponse(BaseModel):
    installation_id: int
    account_login: str | None = None
    account_type: str | None = None
    repository_selection: str | None = None


class GitHubSetupUrlResponse(BaseModel):
    install_url: str
    expires_at: datetime


class GitSyncRepositoryOption(BaseModel):
    installation_id: int
    owner: str
    name: str
    full_name: str
    private: bool = True
    default_branch: str = "main"


class ProjectGitSyncConfigRequest(BaseModel):
    installation_id: int
    repository_owner: str
    repository_name: str
    branch: str = "main"
    base_path: str = ""

    @field_validator("repository_owner", "repository_name", "branch", mode="before")
    @classmethod
    def _required_trimmed(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("不能为空")
        return value.strip()

    @field_validator("base_path", mode="before")
    @classmethod
    def _clean_base_path(cls, value: str | None) -> str:
        raw = (value or "").strip().strip("/")
        if ".." in raw.split("/"):
            raise ValueError("路径不能包含 ..")
        return raw


class ProjectGitSyncConfigResponse(BaseModel):
    project_id: str
    installation_id: int
    repository_owner: str
    repository_name: str
    branch: str
    base_path: str
    last_synced_at: datetime | None = None
    last_commit_sha: str | None = None
    last_error: str | None = None

    class Config:
        from_attributes = True


class GitSyncStatusResponse(BaseModel):
    configured: bool
    install_url: str | None = None
    installations: list[GitSyncInstallationResponse] = Field(default_factory=list)
    repositories: list[GitSyncRepositoryOption] = Field(default_factory=list)
    project_config: ProjectGitSyncConfigResponse | None = None


class ProjectGitSyncRunResponse(BaseModel):
    ok: bool
    synced_files: int
    commit_sha: str | None = None
    synced_at: datetime
    error: str | None = None


# ── AI Continue ─────────────────────────────────────

class DocumentAIContinueRequest(BaseModel):
    instruction: str | None = None
    ai_model: str | None = None
    debug: bool = False


class DocumentAIContinueResponse(BaseModel):
    document_id: str
    content: str
    generated_chunk: str
    ai_model: str
    debug_info: dict | None = None


# ── AI Revise ──────────────────────────────────────

class DocumentAIReviseRequest(BaseModel):
    instruction: str
    selected_text: str | None = None  # None = revise entire document
    full_content: str | None = None   # context for selection mode
    ai_model: str | None = None
    debug: bool = False


class DocumentAIReviseResponse(BaseModel):
    document_id: str
    revised_text: str       # revised version (full doc or selection)
    original_text: str      # original text for diff comparison
    scope: str              # "full" | "selection"
    ai_model: str
    debug_info: dict | None = None


# ── AI Revise Multi-model ─────────────────────────

class DocumentAIReviseMultiRequest(BaseModel):
    instruction: str
    selected_text: str | None = None
    full_content: str | None = None
    models: list[str] = Field(
        default_factory=lambda: [
            "claude-opus-4",
            "claude-sonnet-4",
            "deepseek-v4-flash",
            "deepseek-v4-pro",
            "doubao-seed-2-0-pro-260215",
        ]
    )
    debug: bool = False


class DocumentAIReviseMultiResultItem(BaseModel):
    ai_model: str
    revised_text: str | None = None   # None if failed
    error: str | None = None          # None if succeeded
    debug_info: dict | None = None


class DocumentAIReviseMultiResponse(BaseModel):
    document_id: str
    original_text: str
    scope: str
    results: list[DocumentAIReviseMultiResultItem]


# ── Project Analysis (AI extract from manuscript) ──

class ProjectAnalyzeRequest(BaseModel):
    ai_model: str | None = None
    debug: bool = False


class AnalyzedCharacter(BaseModel):
    name: str
    gender: str | None = None
    identity: str | None = None
    personality: list[str] = Field(default_factory=list)
    appearance: str | None = None
    relationships: str | None = None
    summary: str | None = None


class AnalyzedOutline(BaseModel):
    name: str
    content: str = ""


class AnalyzedSetting(BaseModel):
    name: str
    type: SettingReferenceType = SettingReferenceType.NOTE
    content: str = ""

    @field_validator("type", mode="before")
    @classmethod
    def normalize_type(cls, value):
        return coerce_setting_reference_type(value)


class ProjectAnalyzeResponse(BaseModel):
    characters: list[AnalyzedCharacter] = Field(default_factory=list)
    outlines: list[AnalyzedOutline] = Field(default_factory=list)
    settings: list[AnalyzedSetting] = Field(default_factory=list)
    total_words_analyzed: int = 0
    debug_info: dict | None = None


class AnalyzeApplyRequest(BaseModel):
    characters: list[AnalyzedCharacter] = Field(default_factory=list)
    outlines: list[AnalyzedOutline] = Field(default_factory=list)
    settings: list[AnalyzedSetting] = Field(default_factory=list)


class AnalyzeApplyResponse(BaseModel):
    created_count: int
    references: list[ReferenceItemBrief]
