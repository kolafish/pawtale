from __future__ import annotations

from dataclasses import dataclass
import logging
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ai_calls import (
    AIInvalidStructuredOutputError,
    generate_structured_with_client,
)
from app.services.ai_factory import DEEPSEEK_V4_FLASH_MODEL, get_ai_service
from app.services.ai_timeouts import ai_text_timeout_seconds
from app.services.ai_types import AITextClient
from app.services.ai_workloads import AITextWorkload, ai_text_workload_policy
from app.services.memoir.hybrid_retrieval import (
    DEFAULT_BM25_LIMIT,
    DEFAULT_CANDIDATE_LIMIT,
    DEFAULT_VECTOR_LIMIT,
    EmbeddingClient,
    HybridRetrievalTrace,
    retrieve_hybrid_candidates,
)


logger = logging.getLogger(__name__)

RETRIEVAL_UNIT_PROMPT_VERSION = "memoir_retrieval_units_v1"
QUERY_PLANNING_MODEL = DEEPSEEK_V4_FLASH_MODEL
QUERY_PLANNING_TEMPERATURE = 0.1


class RetrievalUnitPurpose(StrEnum):
    QUERY = "query"
    INDEX = "index"


class _RetrievalUnitOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: RetrievalUnitPurpose
    units: list[str] = Field(default_factory=list)
    decision_reason: str


@dataclass(frozen=True)
class RetrievalUnitGenerationTrace:
    purpose: RetrievalUnitPurpose
    prompt_version: str
    input_text: str
    context_anchor: str
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


@dataclass(frozen=True)
class RetrievalQueryPlanningTrace:
    generation: RetrievalUnitGenerationTrace
    retrieval_queries: tuple[str, ...]
    hybrid_retrieval: HybridRetrievalTrace | None

    @property
    def should_retrieve(self) -> bool:
        return bool(self.retrieval_queries)


COMMON_PROMPT = """你是中文回忆录产品“老余口述”的语义检索转换器。

“老余口述”只是产品名称。“老余”不是讲述者的默认姓名、称呼、性别或身份。除非输入文字明确提到一个叫“老余”的人，否则禁止把任何人物写成“老余”。

每个 unit 必须是一个自然、完整、可独立检索的中文语义单元。不要套用固定分类，不输出标签、关键词列表或知识图谱结构，不为同一方向生成近义重复项，不为了凑数量强行拆分或合并。只能使用输入表达的信息，不补充外部事实，并保留输入中的不确定性。

decision_reason 必须解释是否生成 unit，并与 units 保持一致。如果理由判断输入不清晰、不具体或没有独立检索价值，units 必须返回空数组。
"""

PURPOSE_PROMPTS = {
    RetrievalUnitPurpose.QUERY: """当前任务标识：purpose=query

判断 current_narration 是否包含清晰、具体、值得从历史讲述中召回的方向，并把这些方向转换成零个或多个 unit。

要求：
1. unit 直接描述希望召回的具体人物、事件、关系、地点、时间、因果或细节，保留有区分度的名称、事实和原话。
2. 不要描述检索行为，避免“相关经历”“相关安排”“之前有没有”“关于……的信息”等空泛表达。
3. 只依据 current_narration，不从之前的 AI 问题或其他上下文补充人物、事件、时间、地点、关系或感受。
4. 如果当前讲述只是寒暄、操作指令、无意义填充，或没有清晰具体的历史召回方向，units 返回空数组。
5. 系统仅在 units 非空时检索，并会另外把 current_narration 原文作为基础 Query；不要为了覆盖原文而机械复述整段话。
""",
    RetrievalUnitPurpose.INDEX: """当前任务标识：purpose=index

把 source_text 转换成零个或多个作为原讲述检索入口的 unit。unit 不是长期记忆、文章摘要或知识图谱。

要求：
1. 只提取能够确认属于 source_text，并且具有独立检索价值的信息。
2. context_anchor 只用于理解 source_text 中的指代和省略，不能据此生成 source_text 没有表达的新信息。
3. 如果内容含义、来源或独立检索价值无法可靠判断，units 返回空数组。
""",
}


def retrieval_unit_system_prompt(purpose: RetrievalUnitPurpose) -> str:
    return (
        COMMON_PROMPT
        + PURPOSE_PROMPTS[purpose]
        + "\n输出结构："
        + '{"purpose":"'
        + purpose.value
        + '","units":["..."],"decision_reason":"..."}。只返回 JSON。'
    )


def _user_prompt(
    purpose: RetrievalUnitPurpose,
    *,
    input_text: str,
    context_anchor: str,
) -> str:
    input_name = (
        "current_narration"
        if purpose is RetrievalUnitPurpose.QUERY
        else "source_text"
    )
    if purpose is RetrievalUnitPurpose.QUERY:
        return f"{input_name}:\n{input_text}"
    return f"{input_name}:\n{input_text}\n\ncontext_anchor:\n{context_anchor or '无'}"


def _unique_texts(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value.strip() for value in values if value.strip()))


async def generate_retrieval_units(
    *,
    purpose: RetrievalUnitPurpose,
    input_text: str,
    context_anchor: str = "",
    requested_model: str = QUERY_PLANNING_MODEL,
    ai_client: AITextClient | None = None,
) -> RetrievalUnitGenerationTrace:
    normalized_input = input_text.strip()
    supplied_context = context_anchor.strip()
    if purpose is RetrievalUnitPurpose.QUERY and supplied_context:
        raise ValueError("query retrieval units do not accept context")
    normalized_context = supplied_context
    workload = (
        AITextWorkload.REALTIME_STRUCTURED
        if purpose is RetrievalUnitPurpose.QUERY
        else AITextWorkload.INTERACTIVE_STRUCTURED
    )
    policy = ai_text_workload_policy(workload)
    timeout_seconds = ai_text_timeout_seconds(policy.timeout_profile)
    if not normalized_input:
        return RetrievalUnitGenerationTrace(
            purpose=purpose,
            prompt_version=RETRIEVAL_UNIT_PROMPT_VERSION,
            input_text="",
            context_anchor=normalized_context,
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
            temperature=QUERY_PLANNING_TEMPERATURE,
            finish_reason="",
        )

    client = ai_client or get_ai_service(requested_model)
    generated = await generate_structured_with_client(
        client,
        retrieval_unit_system_prompt(purpose),
        _user_prompt(
            purpose,
            input_text=normalized_input,
            context_anchor=normalized_context,
        ),
        output_model=_RetrievalUnitOutput,
        requested_model=requested_model,
        temperature=QUERY_PLANNING_TEMPERATURE,
        max_tokens=policy.max_tokens,
        timeout=timeout_seconds,
        max_attempts=policy.max_attempts,
    )
    if generated.value.purpose != purpose:
        raise AIInvalidStructuredOutputError(
            "模型返回了错误的检索转换目的",
            response=generated.response,
        )

    units = _unique_texts(generated.value.units)
    response = generated.response
    logger.info(
        "memoir retrieval units generated purpose=%s input_chars=%d units=%d "
        "requested_model=%s resolved_model=%s provider=%s elapsed_ms=%d attempts=%d",
        purpose.value,
        len(normalized_input),
        len(units),
        requested_model,
        response.resolved_model,
        response.provider,
        response.elapsed_ms,
        response.attempt_count,
    )
    return RetrievalUnitGenerationTrace(
        purpose=purpose,
        prompt_version=RETRIEVAL_UNIT_PROMPT_VERSION,
        input_text=normalized_input,
        context_anchor=normalized_context,
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
        temperature=QUERY_PLANNING_TEMPERATURE,
        finish_reason=response.finish_reason,
    )


async def plan_and_retrieve_memory_candidates(
    db: AsyncSession,
    *,
    project_id: str,
    current_narration: str,
    excluded_voice_session_id: str | None = None,
    requested_model: str = QUERY_PLANNING_MODEL,
    ai_client: AITextClient | None = None,
    embedding_client: EmbeddingClient | None = None,
    vector_limit: int = DEFAULT_VECTOR_LIMIT,
    bm25_limit: int = DEFAULT_BM25_LIMIT,
    candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
) -> RetrievalQueryPlanningTrace:
    generation = await generate_retrieval_units(
        purpose=RetrievalUnitPurpose.QUERY,
        input_text=current_narration,
        requested_model=requested_model,
        ai_client=ai_client,
    )
    if not generation.units:
        return RetrievalQueryPlanningTrace(
            generation=generation,
            retrieval_queries=(),
            hybrid_retrieval=None,
        )

    retrieval_queries = _unique_texts(
        [generation.input_text, *generation.units]
    )
    hybrid_trace = await retrieve_hybrid_candidates(
        db,
        project_id=project_id,
        queries=list(retrieval_queries),
        excluded_voice_session_id=excluded_voice_session_id,
        vector_limit=vector_limit,
        bm25_limit=bm25_limit,
        candidate_limit=candidate_limit,
        embedding_client=embedding_client,
    )
    return RetrievalQueryPlanningTrace(
        generation=generation,
        retrieval_queries=retrieval_queries,
        hybrid_retrieval=hybrid_trace,
    )
