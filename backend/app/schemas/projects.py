from pydantic import BaseModel
from datetime import datetime, date

from app.domain.projects.types import ProjectType


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
    documents: list[DocumentMeta] = []

    class Config:
        from_attributes = True


class ReferenceItemBrief(BaseModel):
    id: str
    type: str
    name: str
    sort_order: int = 0
    global_id: str | None = None  # linked global character/scene id

    class Config:
        from_attributes = True


class ProjectListItem(BaseModel):
    id: str
    type: ProjectType
    title: str
    description: str | None = None
    word_count_target: int | None = None
    total_word_count: int = 0
    group_count: int = 0
    document_count: int = 0
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
    total_word_count: int = 0
    groups: list[GroupResponse] = []
    references: list[ReferenceItemBrief] = []
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
    type: str  # character/location/note
    name: str
    content: str = ""
    metadata_json: dict = {}


class ReferenceItemUpdate(BaseModel):
    name: str | None = None
    content: str | None = None
    metadata_json: dict | None = None


class ReferenceItemResponse(BaseModel):
    id: str
    project_id: str
    type: str
    name: str
    content: str
    metadata_json: dict = {}
    sort_order: int = 0
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ImportGlobalRequest(BaseModel):
    character_ids: list[str] = []
    scene_ids: list[str] = []


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
    models: list[str] = ["claude-opus-4", "claude-sonnet-4", "deepseek"]
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
    personality: list[str] = []
    appearance: str | None = None
    relationships: str | None = None
    summary: str | None = None


class AnalyzedOutline(BaseModel):
    name: str
    content: str = ""


class AnalyzedSetting(BaseModel):
    name: str
    type: str = "note"  # location / note
    content: str = ""


class ProjectAnalyzeResponse(BaseModel):
    characters: list[AnalyzedCharacter] = []
    outlines: list[AnalyzedOutline] = []
    settings: list[AnalyzedSetting] = []
    total_words_analyzed: int = 0
    debug_info: dict | None = None


class AnalyzeApplyRequest(BaseModel):
    characters: list[AnalyzedCharacter] = []
    outlines: list[AnalyzedOutline] = []
    settings: list[AnalyzedSetting] = []


class AnalyzeApplyResponse(BaseModel):
    created_count: int
    references: list[ReferenceItemBrief]


# ── Export to Global Library ───────────────────────

class ExportGlobalRequest(BaseModel):
    action: str  # "preview" | "create" | "update"
    global_id: str | None = None  # required when action="update"


class ExportGlobalMatch(BaseModel):
    global_id: str
    global_name: str
    global_type: str  # "character" | "scene"


class ExportGlobalResponse(BaseModel):
    status: str  # "preview" | "created" | "updated" | "no_match"
    match: ExportGlobalMatch | None = None
    global_id: str | None = None


# ── Import with duplicate detection ───────────────

class ImportPreviewItem(BaseModel):
    global_id: str
    global_name: str
    global_type: str  # "character" | "scene"
    existing_ref_id: str | None = None
    existing_ref_name: str | None = None
    match_by: str | None = None  # "global_id" | "name" | None


class ImportPreviewResponse(BaseModel):
    items: list[ImportPreviewItem]
