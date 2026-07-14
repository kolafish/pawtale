from __future__ import annotations

import json
import re
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.memoir.narrative_types import MemoirDraftBlockRole
from app.services.ai_types import AITextClient
from app.services.json_utils import extract_json_payload


SYSTEM_PROMPT = (
    "你是一位严谨的中文回忆录编辑。你只依据给定的结构节点、Story、Claim 和证据写一个正文块，"
    "不得补充常识性细节、推测事实或未提供的人物关系。"
)
COMPARISON_PUNCTUATION_PATTERN = re.compile(r"[\s，。！？!?、；;：:\"“”'‘’…—-]+")
SENTENCE_PATTERN = re.compile(r"[^。！？!?]+")


def _normalized_for_comparison(value: str) -> str:
    return COMPARISON_PUNCTUATION_PATTERN.sub("", value)


def _normalized_sentences(value: str) -> set[str]:
    return {
        normalized
        for sentence in SENTENCE_PATTERN.findall(value)
        if (normalized := _normalized_for_comparison(sentence))
    }


def _sentence_pairs(value: str) -> list[tuple[str, str]]:
    return [
        (sentence.strip(), normalized)
        for sentence in SENTENCE_PATTERN.findall(value)
        if (normalized := _normalized_for_comparison(sentence))
    ]


class DraftBlockEvidenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    quote: str
    evidence_note: str = ""


class DraftBlockClaimInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    statement: str
    certainty: str
    verification_status: str
    narrative_role: str
    is_core: bool
    evidence: list[DraftBlockEvidenceInput] = Field(default_factory=list)


class DraftBlockStoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    story_id: str
    story_revision_id: str
    title: str
    summary: str
    story_role: str
    claims: list[DraftBlockClaimInput] = Field(default_factory=list)


class DraftBlockGenerationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_id: str
    contract_objective: str
    reader_takeaway: str
    time_scope_text: str = ""
    constraints: dict[str, object] = Field(default_factory=dict)
    node_id: str
    node_type: str
    node_title: str
    node_purpose: str
    block_role: MemoirDraftBlockRole
    stories: list[DraftBlockStoryInput] = Field(default_factory=list)
    previous_block_context: str = ""
    next_block_context: str = ""
    feedback: str = ""

    @property
    def allowed_claim_ids(self) -> set[str]:
        return {
            claim.claim_id
            for story in self.stories
            for claim in story.claims
        }


class DraftBlockLLMOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=20_000)
    used_claim_ids: list[str] = Field(default_factory=list)
    unsupported_fact_candidates: list[str] = Field(default_factory=list)

    @field_validator("content")
    @classmethod
    def strip_content(cls, value: str) -> str:
        content = value.strip()
        if not content:
            raise ValueError("draft block content cannot be empty")
        return content

    @field_validator("used_claim_ids", "unsupported_fact_candidates")
    @classmethod
    def unique_strings(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))


@dataclass(frozen=True)
class GeneratedDraftBlock:
    content: str
    used_claim_ids: list[str]
    prompt_snapshot: str


def _prompt(context: DraftBlockGenerationInput) -> str:
    payload = context.model_dump(mode="json")
    return f"""请为当前节点生成一个回忆录正文块。

输入数据：
{json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}

输出严格 JSON，不要 Markdown，不要解释：
{{
  "content": "正文块内容",
  "used_claim_ids": ["实际写入正文的 claim_id"],
  "unsupported_fact_candidates": []
}}

规则：
- 只写当前 node_purpose 所需内容，不扩写整章。
- used_claim_ids 只能来自输入 Story.claims；正文中的每个事实性陈述都必须能映射到其中一个 ID。
- 如果想写但没有 Claim 支撑，把该表述放入 unsupported_fact_candidates，并从 content 中完全删除；仅报告但未写入正文的候选不会导致本次生成失败。
- certainty 不是 exact 时保留原有不确定语气，不能改成确定事实。
- opening、reflection、closing 只要输入包含 Story，就必须引用实际使用的 Claim；没有 Story 时不得写人物、职务、地点、时间、关系、事件或结果等具体事实。
- opening 只选一个具体钩子，最多两句，不概括整个 Story，也不复述 next_block_context。
- factual 按事件顺序写清经过，不替讲述者补感受或评价。
- reflection 只对 narrative_role=reflection 的 Claim statement 做克制改写，最多两句；不得把职务、疾病或结果推导成“辛苦”“压力”“衰败”“遗憾”等输入中没有的感受或评价。
- closing 最多一句，只使用 outcome/reflection Claim，不重复 reflection 或 factual 块。
- transition 块只衔接相邻短上下文，不新增事实，used_claim_ids 必须为空。
- previous_block_context 和 next_block_context 只用于避免断裂，不得据此创造新事实。
- feedback 只控制当前块的表达，不能改变事实。
"""


def validate_generation_output(
    context: DraftBlockGenerationInput,
    output: DraftBlockLLMOutput,
) -> None:
    unexpected = set(output.used_claim_ids) - context.allowed_claim_ids
    if unexpected:
        raise ValueError(
            "draft block used claims outside allowlist: " + ", ".join(sorted(unexpected))
        )
    normalized_content = _normalized_for_comparison(output.content)
    embedded_unsupported = [
        candidate
        for candidate in output.unsupported_fact_candidates
        if _normalized_for_comparison(candidate)
        and _normalized_for_comparison(candidate) in normalized_content
    ]
    if embedded_unsupported:
        raise ValueError("draft block contains unsupported fact candidates")
    if context.block_role == MemoirDraftBlockRole.FACTUAL and not output.used_claim_ids:
        raise ValueError("factual draft block must cite at least one allowed claim")
    if (
        context.block_role != MemoirDraftBlockRole.TRANSITION
        and context.allowed_claim_ids
        and not output.used_claim_ids
    ):
        raise ValueError("draft block with fact sources must cite at least one allowed claim")
    if context.block_role == MemoirDraftBlockRole.TRANSITION and output.used_claim_ids:
        raise ValueError("transition draft block cannot introduce claim-backed facts")
    adjacent_contexts = (
        context.previous_block_context,
        context.next_block_context,
    )
    adjacent_sentences = {
        sentence
        for adjacent in adjacent_contexts
        for sentence in _normalized_sentences(adjacent)
    }
    duplicate_sentence = next(
        (
            sentence
            for sentence, normalized in _sentence_pairs(output.content)
            if normalized in adjacent_sentences
        ),
        None,
    )
    if duplicate_sentence is not None:
        raise ValueError(
            f'draft block duplicates an adjacent sentence: "{duplicate_sentence}"'
        )


async def generate_draft_block_text(
    client: AITextClient,
    *,
    context: DraftBlockGenerationInput,
) -> GeneratedDraftBlock:
    prompt = _prompt(context)
    prompt_snapshot = prompt
    for attempt in range(2):
        raw = await client._call_llm(
            SYSTEM_PROMPT,
            prompt_snapshot,
            temperature=0.1,
            max_tokens=1800,
            timeout=75.0,
        )
        content = raw[0] if isinstance(raw, tuple) else raw
        output = DraftBlockLLMOutput.model_validate(
            extract_json_payload(str(content))
        )
        try:
            validate_generation_output(context, output)
        except ValueError as exc:
            if attempt == 1:
                raise
            prompt_snapshot = (
                f"{prompt}\n\n上一次输出未通过服务端事实与重复校验：{exc}。"
                "如有重复句，请保持 Claim 事实不变但换一种不重复的表述，"
                "或改写当前节点的其他已支持内容。请严格修正并重新输出完整 JSON。"
            )
            continue
        return GeneratedDraftBlock(
            content=output.content,
            used_claim_ids=output.used_claim_ids,
            prompt_snapshot=prompt_snapshot,
        )
    raise AssertionError("draft block validation loop did not return or raise")
