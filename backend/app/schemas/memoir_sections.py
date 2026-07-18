from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

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
