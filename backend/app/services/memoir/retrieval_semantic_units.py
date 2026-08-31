from __future__ import annotations

from dataclasses import dataclass
import logging

from pydantic import BaseModel, ConfigDict, Field

from app.services.ai_calls import generate_structured_with_client
from app.services.ai_timeouts import ai_text_timeout_seconds
from app.services.ai_types import AITextClient
from app.services.ai_workloads import AITextWorkload, ai_text_workload_policy


logger = logging.getLogger(__name__)

SEMANTIC_UNIT_PROMPT_VERSION = "memoir_semantic_index_units_v1"
SEMANTIC_UNIT_TEMPERATURE = 0.1


class _SemanticUnitOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    units: list[str] = Field(default_factory=list)
    decision_reason: str


@dataclass(frozen=True)
class SemanticUnitGenerationTrace:
    prompt_version: str
    input_text: str
    units: tuple[str, ...]
    decision_reason: str
    requested_model: str | None
    resolved_model: str | None
    response_model: str | None
    provider: str | None
    raw_output: str
    usage: dict[str, object]
    elapsed_ms: int
    attempt_count: int
    timeout_seconds: float
    max_tokens: int
    temperature: float
    finish_reason: str


def semantic_unit_system_prompt() -> str:
    return """你是中文回忆录产品“老余口述”的语义索引生成器。

“老余口述”只是产品名称。“老余”不是讲述者的默认姓名、称呼、性别或身份。除非 source_text 明确提到一个叫“老余”的人，否则禁止把任何人物写成“老余”。

把 source_text 转换成零个或多个原讲述的检索入口。每个 unit 必须是一个自然、完整、可独立检索的中文语义单元。

要求：
1. 只提取能够确认属于 source_text，并且具有独立检索价值的信息。
2. 不套用固定分类，不输出标签、关键词列表或知识图谱结构。
3. 不为同一方向生成近义重复项，不为了凑数量强行拆分或合并。
4. 不补充外部事实，保留 source_text 中的不确定性。
5. 如果内容含义、来源或独立检索价值无法可靠判断，units 返回空数组。
6. decision_reason 必须解释是否生成 unit，并与 units 保持一致。

只返回 JSON：{"units":["..."],"decision_reason":"..."}。"""


def semantic_unit_workload_policy():
    return ai_text_workload_policy(AITextWorkload.INTERACTIVE_STRUCTURED)


def _unique_texts(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value.strip() for value in values if value.strip()))


async def generate_semantic_units(
    *,
    source_text: str,
    requested_model: str,
    ai_client: AITextClient,
) -> SemanticUnitGenerationTrace:
    normalized_input = source_text.strip()
    policy = semantic_unit_workload_policy()
    timeout_seconds = ai_text_timeout_seconds(policy.timeout_profile)
    if not normalized_input:
        return SemanticUnitGenerationTrace(
            prompt_version=SEMANTIC_UNIT_PROMPT_VERSION,
            input_text="",
            units=(),
            decision_reason="输入为空",
            requested_model=None,
            resolved_model=None,
            response_model=None,
            provider=None,
            raw_output="",
            usage={},
            elapsed_ms=0,
            attempt_count=0,
            timeout_seconds=timeout_seconds,
            max_tokens=policy.max_tokens,
            temperature=SEMANTIC_UNIT_TEMPERATURE,
            finish_reason="",
        )

    generated = await generate_structured_with_client(
        ai_client,
        semantic_unit_system_prompt(),
        f"source_text:\n{normalized_input}",
        output_model=_SemanticUnitOutput,
        requested_model=requested_model,
        temperature=SEMANTIC_UNIT_TEMPERATURE,
        max_tokens=policy.max_tokens,
        timeout=timeout_seconds,
        max_attempts=policy.max_attempts,
    )
    units = _unique_texts(generated.value.units)
    response = generated.response
    logger.info(
        "memoir semantic retrieval units generated input_chars=%d units=%d "
        "requested_model=%s resolved_model=%s provider=%s elapsed_ms=%d attempts=%d",
        len(normalized_input),
        len(units),
        requested_model,
        response.resolved_model,
        response.provider,
        response.elapsed_ms,
        response.attempt_count,
    )
    return SemanticUnitGenerationTrace(
        prompt_version=SEMANTIC_UNIT_PROMPT_VERSION,
        input_text=normalized_input,
        units=units,
        decision_reason=generated.value.decision_reason.strip(),
        requested_model=response.requested_model,
        resolved_model=response.resolved_model,
        response_model=response.response_model,
        provider=response.provider,
        raw_output=response.content,
        usage=dict(response.usage),
        elapsed_ms=response.elapsed_ms,
        attempt_count=response.attempt_count,
        timeout_seconds=timeout_seconds,
        max_tokens=policy.max_tokens,
        temperature=SEMANTIC_UNIT_TEMPERATURE,
        finish_reason=response.finish_reason,
    )
