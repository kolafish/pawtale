from __future__ import annotations

from dataclasses import dataclass
import json

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.ai_types import AITextClient
from app.services.json_utils import extract_json_payload


SYSTEM_PROMPT = (
    "你是一位严谨的中文回忆录编辑。你只能依据输入的 Story、Claim 和原始讲述摘录"
    "整理整章正文，不得创造时间、地点、人物关系、事件、因果或感受。"
)


class ChapterDraftClaimInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    statement: str
    certainty: str
    narrative_role: str
    is_core: bool
    evidence_quotes: list[str] = Field(default_factory=list)


class ChapterDraftStoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    story_id: str
    title: str
    summary: str
    story_type: str
    time_start: str | None = None
    time_end: str | None = None
    claims: list[ChapterDraftClaimInput] = Field(default_factory=list)


class ChapterDraftSupplementInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    utterance_id: str
    text: str = Field(min_length=1, max_length=20_000)
    question: str
    purpose: str = ""
    dimension: str = ""
    claim_ids: list[str] = Field(default_factory=list)


class ChapterDraftGenerationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_id: str
    chapter_title: str
    stories: list[ChapterDraftStoryInput]
    supplement_blocks: list[ChapterDraftSupplementInput] = Field(default_factory=list)
    existing_draft: str = ""
    editorial_guidance: list[str] = Field(default_factory=list)

    @property
    def allowed_claim_ids(self) -> set[str]:
        return {
            claim.claim_id
            for story in self.stories
            for claim in story.claims
        }


class ChapterDraftLLMOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=200_000)
    used_claim_ids: list[str] = Field(min_length=1)
    unsupported_fact_candidates: list[str] = Field(default_factory=list)

    @field_validator("content")
    @classmethod
    def _strip_content(cls, value: str) -> str:
        return value.strip()

    @field_validator("used_claim_ids", "unsupported_fact_candidates")
    @classmethod
    def _unique_strings(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))


@dataclass(frozen=True)
class GeneratedChapterDraft:
    content: str
    used_claim_ids: tuple[str, ...]
    prompt_snapshot: str


def build_chapter_draft_prompt(context: ChapterDraftGenerationInput) -> str:
    return f"""请把现有素材整理成“{context.chapter_title}”这一章的完整回忆录正文。

输入：
{json.dumps(context.model_dump(mode="json"), ensure_ascii=False)}

只输出严格 JSON：
{{
  "content": "完整章节正文",
  "used_claim_ids": ["实际写入正文的 claim_id"],
  "unsupported_fact_candidates": []
}}

要求：
- 正文采用讲述者第一人称，结构自然连贯，不输出标题编号、编辑说明或数据 ID。
- 根据素材本身决定详略；次要事件可以一笔带过，不强求每件事都有完整起因经过结果。
- 优先保证章节背景、开头、收束、人物关系、指代和转场清楚，避免履历式罗列和重复。
- 所有事实性陈述必须由输入 Claim 及 evidence_quotes 支撑；used_claim_ids 只能来自输入。
- certainty 不是 exact 时保留不确定语气，不得改写成确定事实。
- supplement_blocks 是用户针对补全问题所作的一次次完整回答。每个 block 内的内容和先后关系应作为
  一个连贯叙事单元处理，不要让 existing_draft 或其他素材插入 block 中间；可以润色或取舍次要细节，
  但不能打乱其内部事件顺序。若它补充的是背景或衔接，应整体放在它所解释的事件之前。
- supplement_blocks 只能作为其 claim_ids 的原始证据使用，不能据此扩张出输入 Claim 未表达的新事实。
- 可以调整 existing_draft 的结构和表达，但它不是事实来源；没有 Claim 支撑的旧内容必须删除。
- editorial_guidance 只控制本章组织和表达，不能成为事实来源。
- 想写但缺少依据的内容放入 unsupported_fact_candidates，且不得出现在 content 中。
"""


def validate_chapter_draft_output(
    context: ChapterDraftGenerationInput,
    output: ChapterDraftLLMOutput,
) -> None:
    unexpected = set(output.used_claim_ids) - context.allowed_claim_ids
    if unexpected:
        raise ValueError(
            "chapter draft used claims outside allowlist: "
            + ", ".join(sorted(unexpected))
        )
    embedded = [
        item
        for item in output.unsupported_fact_candidates
        if item and item in output.content
    ]
    if embedded:
        raise ValueError("chapter draft contains unsupported fact candidates")
    if "叙述者" in output.content:
        raise ValueError("chapter draft must use first-person narration")


async def generate_chapter_draft_text(
    client: AITextClient,
    *,
    context: ChapterDraftGenerationInput,
) -> GeneratedChapterDraft:
    prompt = build_chapter_draft_prompt(context)
    prompt_snapshot = prompt
    for attempt in range(2):
        raw = await client._call_llm(
            SYSTEM_PROMPT,
            prompt_snapshot,
            temperature=0.1,
            max_tokens=6000,
            timeout=120.0,
        )
        content = raw[0] if isinstance(raw, tuple) else raw
        output = ChapterDraftLLMOutput.model_validate(
            extract_json_payload(str(content))
        )
        try:
            validate_chapter_draft_output(context, output)
        except ValueError as exc:
            if attempt == 1:
                raise
            prompt_snapshot = (
                f"{prompt}\n\n上一次输出未通过事实约束：{exc}。"
                "请删除无依据内容并重新输出完整 JSON。"
            )
            continue
        return GeneratedChapterDraft(
            content=output.content,
            used_claim_ids=tuple(output.used_claim_ids),
            prompt_snapshot=prompt_snapshot,
        )
    raise AssertionError("chapter draft validation loop did not return or raise")
