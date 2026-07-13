from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.memoir.narrative_types import MemoirDraftBlockRole
from app.services.ai_types import AITextClient
from app.services.json_utils import extract_json_payload


SYSTEM_PROMPT = (
    "你是一位严谨的中文回忆录编辑。你只依据给定的结构节点、Story、Claim 和证据写一个正文块，"
    "不得补充常识性细节、推测事实或未提供的人物关系。"
)


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
- 如果想写但没有 Claim 支撑，把该表述放入 unsupported_fact_candidates，不要写进 content。
- certainty 不是 exact 时保留原有不确定语气，不能改成确定事实。
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
    if output.unsupported_fact_candidates:
        raise ValueError("draft block contains unsupported fact candidates")
    if context.block_role == MemoirDraftBlockRole.FACTUAL and not output.used_claim_ids:
        raise ValueError("factual draft block must cite at least one allowed claim")
    if context.block_role == MemoirDraftBlockRole.TRANSITION and output.used_claim_ids:
        raise ValueError("transition draft block cannot introduce claim-backed facts")


async def generate_draft_block_text(
    client: AITextClient,
    *,
    context: DraftBlockGenerationInput,
) -> GeneratedDraftBlock:
    prompt = _prompt(context)
    raw = await client._call_llm(
        SYSTEM_PROMPT,
        prompt,
        temperature=0.3,
        max_tokens=1800,
        timeout=75.0,
    )
    content = raw[0] if isinstance(raw, tuple) else raw
    output = DraftBlockLLMOutput.model_validate(extract_json_payload(str(content)))
    validate_generation_output(context, output)
    return GeneratedDraftBlock(
        content=output.content,
        used_claim_ids=output.used_claim_ids,
        prompt_snapshot=prompt,
    )
