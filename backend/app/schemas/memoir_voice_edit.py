from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class VoiceEditOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal[
        "delete_chapter",
        "delete_section",
        "delete_material",
        "rename_chapter",
        "rename_section",
        "regenerate_chapter_sections",
        "split_chapter",
    ]
    target_ref: str = Field(min_length=1, max_length=80)
    new_title: str | None = Field(default=None, max_length=80)
    utterance_ids: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def check_title(self) -> Self:
        if self.new_title is not None:
            self.new_title = self.new_title.strip()
        if self.action.startswith("rename_") or self.action == "split_chapter":
            if not self.new_title:
                raise ValueError("改名或拆分章节必须提供新标题")
        elif self.new_title is not None:
            raise ValueError("只有改名或拆分章节可以提供新标题")
        if self.action == "split_chapter":
            if not self.utterance_ids or len(set(self.utterance_ids)) != len(self.utterance_ids):
                raise ValueError("拆分章节必须提供不重复的讲述记录")
        elif self.utterance_ids:
            raise ValueError("只有拆分章节可以选择讲述记录")
        return self


class VoiceEditQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_refs: list[str] = Field(default_factory=list)
    question: str


class VoiceEditProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operations: list[VoiceEditOperation] = Field(default_factory=list, max_length=30)
    questions: list[VoiceEditQuestion] = Field(default_factory=list, max_length=10)
    reply: str


class VoiceEditPreviewRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=2000)
    ai_model: str | None = None
    supports_chapter_split: bool = False


class VoiceEditItem(VoiceEditOperation):
    label: str
    detail: str


class VoiceEditPreviewResponse(BaseModel):
    signature: str
    operations: list[VoiceEditItem]
    questions: list[str]
    reply: str


class VoiceEditApplyRequest(BaseModel):
    signature: str = Field(min_length=2, max_length=8192)
    operations: list[VoiceEditOperation] = Field(min_length=1, max_length=30)


class VoiceEditApplyResponse(BaseModel):
    message: str
