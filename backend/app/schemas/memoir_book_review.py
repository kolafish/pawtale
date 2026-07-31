from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from app.domain.memoir.types import (
    MemoirBookRevisionProposalPhase,
    MemoirBookRevisionProposalStatus,
    MemoirSemanticCacheStatus,
)
from app.schemas.memoir_book import MemoirBookManuscript


class MemoirBookReviewGenerateRequest(BaseModel):
    ai_model: str | None = None
    force_regenerate: bool = False


class MemoirBookReviewAnswerApplyRequest(BaseModel):
    ai_model: str | None = None


class MemoirBookEditorialRevisionRequest(BaseModel):
    action_ids: list[str] = Field(min_length=1)
    ai_model: str | None = None


class MemoirBookRevisionFeedbackRequest(BaseModel):
    feedback: str = Field(min_length=1, max_length=1000)
    ai_model: str | None = None


class MemoirBookRevisionDraftRequest(BaseModel):
    ai_model: str | None = None


MemoirBookRevisionMetadataField = Literal[
    "title",
    "subtitle",
    "narrator_name",
    "prepared_by",
    "preface",
    "afterword",
    "back_cover_quote",
]


class MemoirBookEditorialActionResponse(BaseModel):
    id: str
    action_type: Literal[
        "remove_repetition",
        "improve_transition",
        "reorder_chapters",
        "revise_book_profile",
    ]
    title: str
    reason: str
    chapter_ids: list[str] = Field(default_factory=list)
    metadata_fields: list[MemoirBookRevisionMetadataField] = Field(
        default_factory=list
    )


class MemoirBookReviewContentResponse(BaseModel):
    summary: str = ""
    strengths: list[str] = Field(default_factory=list)
    structure_guidance: list[str] = Field(default_factory=list)
    editorial_actions: list[MemoirBookEditorialActionResponse] = Field(
        default_factory=list
    )


class MemoirBookReviewResponse(BaseModel):
    id: str
    project_id: str
    input_signature: str
    engine: str
    status: MemoirSemanticCacheStatus
    review: MemoirBookReviewContentResponse
    error_message: str
    created_at: datetime
    updated_at: datetime


MemoirBookReviewJobStatus = Literal[
    "idle",
    "pending",
    "running",
    "succeeded",
    "failed",
    "cancelled",
]
MemoirBookReviewJobStage = Literal[
    "",
    "queued",
    "preparing_chapters",
    "reviewing",
    "saving",
    "completed",
    "failed",
    "cancelled",
]


class MemoirBookReviewJobResponse(BaseModel):
    job_id: str | None = None
    project_id: str
    status: MemoirBookReviewJobStatus
    stage: MemoirBookReviewJobStage = ""
    total_chapter_count: int = 0
    processed_chapter_count: int = 0
    current_chapter_title: str = ""
    requested_ai_model: str | None = None
    resolved_ai_model: str | None = None
    review_id: str | None = None
    can_cancel: bool = False
    can_retry: bool = False
    error_message: str = ""


MemoirBookReviewAnswerApplicationStage = Literal[
    "",
    "queued",
    "assembling",
    "revising",
    "saving",
    "completed",
    "failed",
    "cancelled",
]


class MemoirBookRevisionJobResponse(BaseModel):
    job_id: str | None = None
    project_id: str
    status: MemoirBookReviewJobStatus
    stage: MemoirBookReviewAnswerApplicationStage = ""
    proposal_id: str | None = None
    proposal_status: MemoirBookRevisionProposalStatus | None = None
    proposal_phase: MemoirBookRevisionProposalPhase | None = None
    requested_ai_model: str | None = None
    resolved_ai_model: str | None = None
    can_cancel: bool = False
    can_retry: bool = False
    error_message: str = ""


class MemoirBookReviewAnswerApplicationJobResponse(MemoirBookRevisionJobResponse):
    answer_count: int = 0
    unresolved_count: int = 0
    changed_chapter_count: int = 0
    summary: str = ""
    already_applied: bool = False
    can_apply: bool = False


class MemoirBookEditorialRevisionJobResponse(MemoirBookRevisionJobResponse):
    selected_action_count: int = 0


class MemoirBookRevisionFeedbackJobResponse(MemoirBookRevisionJobResponse):
    changed_chapter_count: int = 0
    summary: str = ""


class MemoirBookRevisionDraftJobResponse(MemoirBookRevisionJobResponse):
    changed_chapter_count: int = 0
    summary: str = ""


class MemoirBookRevisionPlanItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    reason: str
    approach: str
    targets: list[str] = Field(default_factory=list)


class MemoirBookRevisionMetadataPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=200)
    subtitle: str | None = Field(default=None, max_length=180)
    narrator_name: str | None = Field(default=None, max_length=180)
    prepared_by: str | None = Field(default=None, max_length=180)
    preface: str | None = Field(default=None, max_length=1200)
    afterword: str | None = Field(default=None, max_length=1200)
    back_cover_quote: str | None = Field(default=None, max_length=160)

    @field_validator("*")
    @classmethod
    def _strip_text(
        cls,
        value: str | None,
        info: ValidationInfo,
    ) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return (text or None) if info.field_name == "title" else text


class MemoirBookRevisionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    items: list[MemoirBookRevisionPlanItem] = Field(default_factory=list)
    book_metadata_patch: dict[MemoirBookRevisionMetadataField, str] = Field(
        default_factory=dict
    )

    @field_validator("book_metadata_patch", mode="before")
    @classmethod
    def _validate_metadata_patch(cls, value: object) -> dict[str, str]:
        return MemoirBookRevisionMetadataPatch.model_validate(
            {} if value is None else value
        ).model_dump(exclude_none=True)


class MemoirBookRevisionPreviewVersion(BaseModel):
    version: int
    manuscript: MemoirBookManuscript | None = None


class MemoirBookRevisionSectionComparison(BaseModel):
    section_id: str
    title: str
    before_content: str
    after_content: str
    before_char_count: int
    after_char_count: int


class MemoirBookRevisionChapterComparison(BaseModel):
    chapter_id: str
    title: str
    sections: list[MemoirBookRevisionSectionComparison]


class MemoirBookRevisionChapterOrderItem(BaseModel):
    chapter_id: str
    title: str


class MemoirBookRevisionMetadataChange(BaseModel):
    field: MemoirBookRevisionMetadataField
    before_value: str
    after_value: str


class MemoirBookRevisionComparison(BaseModel):
    changed_chapter_count: int
    changed_section_count: int
    before_char_count: int
    after_char_count: int
    char_count_delta: int
    chapters: list[MemoirBookRevisionChapterComparison]
    chapter_order_changed: bool = False
    before_chapter_order: list[MemoirBookRevisionChapterOrderItem] = Field(default_factory=list)
    after_chapter_order: list[MemoirBookRevisionChapterOrderItem] = Field(default_factory=list)
    metadata_changes: list[MemoirBookRevisionMetadataChange] = Field(
        default_factory=list
    )


class MemoirBookRevisionProposalResponse(BaseModel):
    id: str
    project_id: str
    status: MemoirBookRevisionProposalStatus
    phase: MemoirBookRevisionProposalPhase
    revision_plan: MemoirBookRevisionPlan | None = None
    summary: str
    base_output_version_id: str
    result_output_version_id: str | None = None
    comparison: MemoirBookRevisionComparison
    before: MemoirBookRevisionPreviewVersion
    after: MemoirBookRevisionPreviewVersion
    can_apply: bool
    can_dismiss: bool
    created_at: datetime
    updated_at: datetime
