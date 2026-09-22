from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
import logging
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from app.services.ai_costs import (
    AICostEstimate,
    NormalizedAIUsage,
    estimate_ai_cost,
    normalize_ai_usage,
)
from app.services.ai_types import AITextClient
from app.services.ai_streaming import AITextStreamChunk
from app.services.json_utils import extract_json_payload


StructuredModel = TypeVar("StructuredModel", bound=BaseModel)
logger = logging.getLogger(__name__)
TRUNCATED_FINISH_REASONS = {
    "length",
    "max_tokens",
    "incomplete",
    "model_context_window_exceeded",
}


@dataclass(frozen=True)
class AITextCallResult:
    content: str
    provider: str | None
    requested_model: str | None
    resolved_model: str | None
    response_model: str | None
    finish_reason: str
    usage: dict[str, Any] = field(default_factory=dict)
    elapsed_ms: int = 0
    attempt_count: int = 1
    response_json: dict[str, Any] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )

    @property
    def model_mismatch(self) -> bool:
        return bool(
            self.resolved_model
            and self.response_model
            and self.resolved_model != self.response_model
        )

    @property
    def is_truncated(self) -> bool:
        return self.finish_reason in TRUNCATED_FINISH_REASONS

    @property
    def normalized_usage(self) -> NormalizedAIUsage:
        return normalize_ai_usage(self.usage)

    @property
    def cost_estimate(self) -> AICostEstimate:
        return estimate_ai_cost(
            provider=self.provider,
            model=self.response_model or self.resolved_model,
            usage=self.normalized_usage,
        )

    def diagnostics(self) -> dict[str, object]:
        values: dict[str, object] = {
            "requested_model": self.requested_model,
            "provider": self.provider,
            "model": self.resolved_model,
            "response_model": self.response_model,
            "finish_reason": self.finish_reason,
            "response_char_count": len(self.content),
            "elapsed_ms": self.elapsed_ms,
            "attempt_count": self.attempt_count,
            "model_mismatch": self.model_mismatch,
        }
        if self.usage:
            values["usage"] = self.usage
        values["normalized_usage"] = self.normalized_usage.as_dict()
        values["estimated_cost"] = self.cost_estimate.as_dict()
        return values


@dataclass(frozen=True)
class AIStructuredCallResult(Generic[StructuredModel]):
    value: StructuredModel
    response: AITextCallResult


class AIStructuredOutputError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        response: AITextCallResult | None = None,
    ) -> None:
        super().__init__(message)
        self.response = response

    @property
    def diagnostics(self) -> dict[str, object]:
        return self.response.diagnostics() if self.response is not None else {}


class AIOutputTruncatedError(AIStructuredOutputError):
    pass


class AIInvalidStructuredOutputError(AIStructuredOutputError):
    pass


def response_parts(response: str | tuple[str, dict]) -> tuple[str, dict[str, Any]]:
    if isinstance(response, tuple):
        content, response_json = response
        return (
            str(content),
            response_json if isinstance(response_json, dict) else {},
        )
    return str(response), {}


def response_finish_reason(response_json: dict[str, Any]) -> str:
    choices = response_json.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        reason = choices[0].get("finish_reason")
        if isinstance(reason, str):
            return reason
    reason = response_json.get("stop_reason")
    if isinstance(reason, str):
        return reason
    incomplete = response_json.get("incomplete_details")
    if isinstance(incomplete, dict):
        reason = incomplete.get("reason")
        if isinstance(reason, str):
            return reason
    status = response_json.get("status")
    return status if isinstance(status, str) else ""


def build_text_call_result(
    response: str | tuple[str, dict],
    *,
    client: AITextClient,
    requested_model: str | None,
    elapsed_ms: int,
    attempt_count: int,
) -> AITextCallResult:
    content, response_json = response_parts(response)
    response_model = response_json.get("model")
    usage = response_json.get("usage")
    result = AITextCallResult(
        content=content,
        provider=getattr(client, "provider_name", None),
        requested_model=requested_model,
        resolved_model=getattr(client, "model_name", requested_model),
        response_model=response_model if isinstance(response_model, str) else None,
        finish_reason=response_finish_reason(response_json),
        usage=usage if isinstance(usage, dict) else {},
        elapsed_ms=elapsed_ms,
        attempt_count=attempt_count,
        response_json=response_json,
    )
    if result.model_mismatch:
        logger.warning(
            "AI response model differs from resolved model "
            "requested=%s resolved=%s response=%s",
            requested_model or "<default>",
            result.resolved_model,
            result.response_model,
        )
    return result


def _merge_usage_values(values: list[Any]) -> Any:
    if values and all(
        isinstance(value, (int, float)) and not isinstance(value, bool)
        for value in values
    ):
        return sum(values)
    if values and all(isinstance(value, dict) for value in values):
        keys = {
            key
            for value in values
            for key in value
            if isinstance(key, str)
        }
        return {
            key: _merge_usage_values(
                [value[key] for value in values if key in value]
            )
            for key in keys
        }
    return values[-1] if values else None


def with_attempt_totals(
    response: AITextCallResult,
    *,
    attempts: list[AITextCallResult],
    elapsed_ms: int,
) -> AITextCallResult:
    usages = [attempt.usage for attempt in attempts if attempt.usage]
    return replace(
        response,
        usage=_merge_usage_values(usages) if usages else {},
        elapsed_ms=elapsed_ms,
        attempt_count=len(attempts),
    )


async def generate_text_with_client(
    client: AITextClient,
    system_prompt: str,
    user_prompt: str,
    *,
    requested_model: str | None,
    temperature: float,
    max_tokens: int,
    timeout: float,
) -> AITextCallResult:
    return await client.generate_text(
        system_prompt,
        user_prompt,
        requested_model=requested_model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )


async def stream_text_with_client(
    client: AITextClient,
    system_prompt: str,
    user_prompt: str,
    *,
    requested_model: str | None,
    temperature: float,
    max_tokens: int,
    timeout: float,
) -> AsyncIterator[AITextStreamChunk]:
    provider = getattr(client, "provider_name", None)
    resolved_model = getattr(client, "model_name", requested_model)
    mismatch_logged = False
    async for chunk in client.stream_llm(
        system_prompt,
        user_prompt,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    ):
        enriched = replace(
            chunk,
            provider=provider,
            requested_model=requested_model,
            resolved_model=resolved_model,
        )
        if enriched.model_mismatch and not mismatch_logged:
            logger.warning(
                "AI streaming response model differs from resolved model "
                "requested=%s resolved=%s response=%s",
                requested_model or "<default>",
                resolved_model,
                enriched.response_model,
            )
            mismatch_logged = True
        yield enriched


def validate_structured_response(
    response: AITextCallResult,
    *,
    output_model: type[StructuredModel],
    payload: Any | None = None,
) -> AIStructuredCallResult[StructuredModel]:
    if response.is_truncated:
        raise AIOutputTruncatedError(
            "模型结构化输出达到长度上限，请重试",
            response=response,
        )
    candidate = payload
    if candidate is None:
        candidate = extract_json_payload(response.content)
    if not isinstance(candidate, dict):
        raise AIInvalidStructuredOutputError(
            "模型没有返回可识别的结构化结果，请重试",
            response=response,
        )
    try:
        value = output_model.model_validate(candidate)
    except ValidationError as exc:
        raise AIInvalidStructuredOutputError(
            "模型返回的结构化结果缺少必要字段，请重试",
            response=response,
        ) from exc
    return AIStructuredCallResult(value=value, response=response)


async def generate_structured_with_client(
    client: AITextClient,
    system_prompt: str,
    user_prompt: str,
    *,
    output_model: type[StructuredModel],
    requested_model: str | None,
    temperature: float,
    max_tokens: int,
    timeout: float,
    max_attempts: int,
) -> AIStructuredCallResult[StructuredModel]:
    return await client.generate_structured(
        system_prompt,
        user_prompt,
        output_model=output_model,
        requested_model=requested_model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        max_attempts=max_attempts,
    )
