from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.domain.memoir.types import MemoirSectionStatus, MemoirSectionType


class MemoirSectionMaterialResponse(BaseModel):
    id: str
    utterance_id: str
    transcript_version_id: str | None = None
    sort_order: int
    text: str
    include_in_book: bool
    assigned_by: str
    assignment_confidence: float | None = None
    assignment_reason: str
    created_at: datetime
    updated_at: datetime


class MemoirChapterSectionMaterialSummary(BaseModel):
    utterance_id: str
    sort_order: int
    text: str
    include_in_book: bool


class MemoirChapterSectionSummary(BaseModel):
    id: str
    title: str
    section_type: MemoirSectionType
    sort_order: int
    status: MemoirSectionStatus
    include_in_book: bool
    requires_user_assignment: bool = False
    version: int
    material_count: int
    included_material_count: int
    materials: list[MemoirChapterSectionMaterialSummary] = Field(
        default_factory=list
    )


class MemoirChapterDraftSectionResponse(BaseModel):
    section_id: str
    title: str
    section_type: MemoirSectionType
    section_version: int
    sort_order: int
    content: str
    used_material_ids: list[str] = Field(default_factory=list)


class MemoirSectionResponse(BaseModel):
    id: str
    project_id: str
    chapter_id: str
    chapter_title: str
    title: str
    section_type: MemoirSectionType
    sort_order: int
    status: MemoirSectionStatus
    include_in_book: bool
    requires_user_assignment: bool = False
    version: int
    materials: list[MemoirSectionMaterialResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class MemoirSectionCollectionResponse(BaseModel):
    project_id: str
    sections: list[MemoirSectionResponse] = Field(default_factory=list)
    unassigned_material_count: int = 0


class MemoirSectionCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    section_type: MemoirSectionType = MemoirSectionType.EVENT
    sort_order: int | None = Field(default=None, ge=0)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("title must not be blank")
        return normalized


class MemoirSectionUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    section_type: MemoirSectionType | None = None
    status: MemoirSectionStatus | None = None
    include_in_book: bool | None = None
    expected_version: int = Field(ge=1)

    @field_validator("title")
    @classmethod
    def normalize_optional_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("title must not be blank")
        return normalized


class MemoirSectionReorderRequest(BaseModel):
    section_ids: list[str] = Field(min_length=1)

    @field_validator("section_ids")
    @classmethod
    def unique_section_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("section_ids must be unique")
        return value


class MemoirSectionVersionReference(BaseModel):
    id: str = Field(min_length=1, max_length=36)
    version: int = Field(ge=1)


class MemoirSectionRegenerateRequest(BaseModel):
    expected_sections: list[MemoirSectionVersionReference] = Field(min_length=1)
    ai_model: str | None = Field(default=None, max_length=120)

    @field_validator("expected_sections")
    @classmethod
    def unique_section_ids(
        cls,
        value: list[MemoirSectionVersionReference],
    ) -> list[MemoirSectionVersionReference]:
        ids = [section.id for section in value]
        if len(ids) != len(set(ids)):
            raise ValueError("expected_sections must contain unique ids")
        return value


class MemoirSectionRegenerateResponse(BaseModel):
    project_id: str
    chapter_id: str
    material_count: int
    assigned_count: int
    unassigned_count: int
    section_count: int
    ai_model: str | None = None


class MemoirSectionRegenerationJobResponse(BaseModel):
    job_id: str | None = None
    project_id: str
    chapter_id: str
    status: Literal[
        "idle",
        "pending",
        "running",
        "succeeded",
        "failed",
        "cancelled",
    ] = "idle"
    stage: str = "idle"
    material_count: int = 0
    processed_material_count: int = 0
    total_char_count: int = 0
    processed_char_count: int = 0
    batch_count: int = 0
    completed_batch_count: int = 0
    section_count: int = 0
    assigned_count: int = 0
    unassigned_count: int = 0
    requested_ai_model: str | None = None
    resolved_ai_model: str | None = None
    can_cancel: bool = False
    error_message: str = ""


class MemoirSectionMaterialAssignmentRequest(BaseModel):
    utterance_ids: list[str] = Field(min_length=1)

    @field_validator("utterance_ids")
    @classmethod
    def unique_utterance_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("utterance_ids must be unique")
        return value


class MemoirSectionMaterialMoveRequest(BaseModel):
    target_section_id: str
    target_sort_order: int | None = Field(default=None, ge=0)


class MemoirSectionMaterialPlacementRequest(BaseModel):
    target_section_id: str | None = None
    target_chapter_id: str | None = None
    new_chapter_title: str | None = Field(default=None, max_length=80)
    new_section_title: str | None = Field(default=None, max_length=200)

    @field_validator(
        "target_section_id",
        "target_chapter_id",
        "new_chapter_title",
        "new_section_title",
    )
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_target(self):
        existing_section = bool(
            self.target_section_id
            and not self.target_chapter_id
            and not self.new_chapter_title
            and not self.new_section_title
        )
        new_section = bool(
            self.target_chapter_id
            and self.new_section_title
            and not self.target_section_id
            and not self.new_chapter_title
        )
        new_chapter = bool(
            self.new_chapter_title
            and self.new_section_title
            and not self.target_section_id
            and not self.target_chapter_id
        )
        if sum((existing_section, new_section, new_chapter)) != 1:
            raise ValueError("请选择已有小节，或提供新章节/小节名称")
        return self


class MemoirSectionMaterialPlacementResponse(BaseModel):
    project_id: str
    utterance_id: str
    chapter_id: str
    section_id: str
    created_chapter: bool = False
    created_section: bool = False


class MemoirSectionMergeRequest(BaseModel):
    source_section_ids: list[str] = Field(min_length=1)
    title: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("source_section_ids")
    @classmethod
    def unique_source_section_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("source_section_ids must be unique")
        return value

    @field_validator("title")
    @classmethod
    def normalize_optional_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("title must not be blank")
        return normalized


class MemoirSectionSplitRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    section_type: MemoirSectionType = MemoirSectionType.EVENT
    utterance_ids: list[str] = Field(min_length=1)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("title must not be blank")
        return normalized

    @field_validator("utterance_ids")
    @classmethod
    def unique_utterance_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("utterance_ids must be unique")
        return value
