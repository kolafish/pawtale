from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StructureFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["chapter_structure", "section_structure", "title_scope"]
    section_ids: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1, max_length=1000)


class BookStructureFinding(StructureFinding):
    chapter_ids: list[str] = Field(min_length=1)
