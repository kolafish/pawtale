from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Actions available to every client version.
BASE_VOICE_EDIT_ACTIONS = frozenset({
    "delete_chapter",
    "delete_section",
    "delete_material",
    "rename_chapter",
    "rename_section",
    "regenerate_chapter_sections",
    "consolidate_chapter_sections",
    "split_chapter",
    "merge_sections",
})
# Actions offered only to clients that echo previewed operations unchanged.
EXTENDED_VOICE_EDIT_ACTIONS = frozenset({
    "merge_chapters",
    "move_chapter",
    "set_chapter_book_inclusion",
    "correct_term",
    "update_book_title",
    "update_narrator_profile",
})
BOOK_TARGET_REF = "book"
PROFILE_TARGET_REF = "profile"
VoiceEditUnsupportedIntent = Literal[
    "text_rewrite", "fact_correction", "book_copy", "section_reorder",
    "media", "other",
]
VoiceEditNavigationPage = Literal["section_revision", "chapter", "book_info", "project_profile", "transcripts"]


class VoiceEditOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal[
        "delete_chapter",
        "delete_section",
        "delete_material",
        "rename_chapter",
        "rename_section",
        "regenerate_chapter_sections",
        "consolidate_chapter_sections",
        "split_chapter",
        "merge_sections",
        "merge_chapters",
        "move_chapter",
        "set_chapter_book_inclusion",
        "correct_term",
        "update_book_title",
        "update_narrator_profile",
    ]
    target_ref: str = Field(min_length=1, max_length=80)
    target_chapter_ref: str | None = Field(default=None, min_length=1, max_length=80)
    new_title: str | None = Field(default=None, max_length=80)
    utterance_ids: list[str] = Field(default_factory=list, max_length=30)
    section_ids: list[str] = Field(default_factory=list, max_length=30)
    # move_chapter: exactly one of a 1-based final position, first/last, or a neighbour chapter.
    target_index: int | None = Field(default=None, ge=1, le=200)
    target_position: Literal["first", "last"] | None = None
    relative_ref: str | None = Field(default=None, max_length=80)
    relative_position: Literal["before", "after"] | None = None
    include_in_book: bool | None = None
    term_from: str | None = Field(default=None, max_length=40)
    term_to: str | None = Field(default=None, max_length=40)
    narrator_name: str | None = Field(default=None, max_length=40)
    narrator_gender: Literal["male", "female", "unspecified"] | None = None
    narrator_origin: str | None = Field(default=None, max_length=80)
    narrator_age: int | None = Field(default=None, ge=0, le=130)

    @model_validator(mode="after")
    def check_title(self) -> Self:
        if self.new_title is not None:
            self.new_title = self.new_title.strip()
        if self.action.startswith("rename_") or self.action == "update_book_title":
            if not self.new_title:
                raise ValueError("改名必须提供新标题")
        elif self.action != "split_chapter" and self.new_title is not None:
            raise ValueError("只有改名或拆分章节可以提供新标题")
        if self.action == "split_chapter":
            if bool(self.utterance_ids) == bool(self.section_ids):
                raise ValueError("拆分章节必须选择小节或讲述记录，不能混合选择")
            selected = self.section_ids or self.utterance_ids
            if len(set(selected)) != len(selected) or any(not item.strip() for item in selected):
                raise ValueError("拆分章节不能选择重复或空白的对象")
        elif self.action == "merge_sections":
            if not self.section_ids or self.utterance_ids:
                raise ValueError("合并小节必须选择来源小节，不能选择讲述记录")
            if len(set(self.section_ids)) != len(self.section_ids) or any(not item.strip() for item in self.section_ids):
                raise ValueError("合并小节不能选择重复或空白的对象")
            if self.target_ref in self.section_ids:
                raise ValueError("不能把小节合并到自身")
        elif self.utterance_ids or self.section_ids:
            raise ValueError("只有拆分章节或合并小节可以选择小节或讲述记录")
        self._check_extended_fields()
        return self

    def _check_extended_fields(self) -> None:
        if self.action == "merge_chapters":
            if not self.target_chapter_ref or not self.target_chapter_ref.strip():
                raise ValueError("合并章节必须提供目标章节")
            if self.target_ref == self.target_chapter_ref:
                raise ValueError("不能把章节合并到自身")
        elif self.target_chapter_ref is not None:
            raise ValueError("只有合并章节可以指定目标章节")
        move_fields = (self.target_index, self.target_position, self.relative_ref, self.relative_position)
        profile_fields = (self.narrator_name, self.narrator_gender, self.narrator_origin, self.narrator_age)
        if self.action == "move_chapter":
            modes = [self.target_index is not None, self.target_position is not None, self.relative_ref is not None]
            if sum(modes) != 1 or (self.relative_ref is not None) != (self.relative_position is not None):
                raise ValueError("移动章节必须给出一个目标位置")
        elif any(value is not None for value in move_fields):
            raise ValueError("只有移动章节可以指定位置")
        if self.action == "set_chapter_book_inclusion":
            if self.include_in_book is None:
                raise ValueError("请说明是否收入本书")
        elif self.include_in_book is not None:
            raise ValueError("只有设置收录可以指定是否收入本书")
        if self.action == "correct_term":
            self.term_from = (self.term_from or "").strip()
            self.term_to = (self.term_to or "").strip()
            if self.target_ref != BOOK_TARGET_REF:
                raise ValueError("用字更正作用于整本书")
            if len(self.term_from) < 2 or not self.term_to or self.term_from == self.term_to:
                raise ValueError("用字更正需要完整的原词和新词，原词至少两个字")
        elif self.term_from is not None or self.term_to is not None:
            raise ValueError("只有用字更正可以提供原词和新词")
        if self.action == "update_book_title" and self.target_ref != BOOK_TARGET_REF:
            raise ValueError("书名修改作用于整本书")
        if self.action == "update_narrator_profile":
            if self.target_ref != PROFILE_TARGET_REF:
                raise ValueError("讲述人资料修改作用于讲述人资料")
            if self.narrator_name is not None:
                self.narrator_name = self.narrator_name.strip()
            if self.narrator_origin is not None:
                self.narrator_origin = self.narrator_origin.strip()
            if all(value is None for value in profile_fields):
                raise ValueError("请说明要修改的讲述人资料")
        elif any(value is not None for value in profile_fields):
            raise ValueError("只有讲述人资料修改可以提供资料字段")


class VoiceEditQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_refs: list[str] = Field(default_factory=list)
    question: str


class VoiceEditNavigationProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: VoiceEditNavigationPage
    chapter_ref: str | None = None
    section_ref: str | None = None
    # The narrator's own words for the destination page; the edit is confirmed there.
    feedback: str | None = Field(default=None, max_length=500)


class VoiceEditProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operations: list[VoiceEditOperation] = Field(default_factory=list, max_length=30)
    questions: list[VoiceEditQuestion] = Field(default_factory=list, max_length=10)
    reply: str
    unsupported_intent: VoiceEditUnsupportedIntent | None = None
    navigation: VoiceEditNavigationProposal | None = None


class VoiceEditPreviewRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=2000)
    ai_model: str | None = None
    supports_chapter_split: bool = False
    supports_section_merge: bool = False
    supports_composed_chapter_edits: bool = False
    supports_multiple_chapter_splits: bool = False
    supports_extended_edits: bool = False


class VoiceEditItem(VoiceEditOperation):
    label: str
    detail: str


class VoiceEditNavigation(BaseModel):
    page: VoiceEditNavigationPage
    label: str
    chapter_id: str | None = None
    section_id: str | None = None
    section_title: str | None = None
    feedback: str | None = None


class VoiceEditPreviewResponse(BaseModel):
    signature: str
    operations: list[VoiceEditItem]
    questions: list[str]
    reply: str
    navigation: VoiceEditNavigation | None = None


class VoiceEditApplyRequest(BaseModel):
    signature: str = Field(min_length=2, max_length=8192)
    operations: list[VoiceEditOperation] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def require_split_title(self) -> Self:
        if any(op.action == "split_chapter" and not op.new_title for op in self.operations):
            raise ValueError("拆分章节必须确认新标题")
        return self


class VoiceEditApplyResponse(BaseModel):
    message: str
    undoable: bool = False


class VoiceEditUndoResponse(BaseModel):
    message: str
