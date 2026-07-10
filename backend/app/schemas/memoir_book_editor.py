from pydantic import BaseModel, Field

from app.schemas.memoir_book import (
    MemoirBookChapterDraftResponse,
    MemoirBookOutputResponse,
    MemoirBookPlanResponse,
)
from app.schemas.memoir_chapter_source_editor import MemoirChapterSourceEditorResponse


class MemoirBookEditorResponse(BaseModel):
    project_id: str
    plan: MemoirBookPlanResponse | None = None
    chapter_source_editor: MemoirChapterSourceEditorResponse
    chapter_drafts: list[MemoirBookChapterDraftResponse] = Field(default_factory=list)
    output: MemoirBookOutputResponse | None = None
