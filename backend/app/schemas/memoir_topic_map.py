from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.domain.memoir.narrative_types import (
    MemoirFrameworkEvidenceState,
    MemoirFrameworkUserResolution,
)


MemoirTopicMapDisplayState = Literal[
    "not_started",
    "touched",
    "covered",
    "enough_for_now",
    "not_applicable",
    "do_not_discuss",
]


class MemoirTopicMapResolutionRequest(BaseModel):
    user_resolution: MemoirFrameworkUserResolution


class MemoirTopicMapItemResponse(BaseModel):
    id: str
    item_key: str
    title: str
    intent: str
    evidence_state: MemoirFrameworkEvidenceState
    user_resolution: MemoirFrameworkUserResolution
    display_state: MemoirTopicMapDisplayState
    state_text: str
    state_detail: str
    resolved_at: datetime | None = None


class MemoirTopicMapModuleResponse(BaseModel):
    module_key: str
    title: str
    items: list[MemoirTopicMapItemResponse] = Field(default_factory=list)


class MemoirTopicMapResponse(BaseModel):
    project_id: str
    template_key: str
    template_version: str
    headline: str
    detail: str
    modules: list[MemoirTopicMapModuleResponse] = Field(default_factory=list)
