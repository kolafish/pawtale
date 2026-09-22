from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


def _count(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float) and value.is_integer():
        return max(0, int(value))
    return None


def _first(*values: object) -> int | None:
    for value in values:
        count = _count(value)
        if count is not None:
            return count
    return None


def _mapping(value: object) -> dict:
    return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class NormalizedAIUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    reasoning_tokens: int = 0

    def as_dict(self) -> dict[str, int]:
        values: dict[str, int] = {}
        for name in ("input_tokens", "output_tokens", "total_tokens"):
            value = getattr(self, name)
            if value is not None:
                values[name] = value
        for name in ("cached_input_tokens", "cache_write_input_tokens", "reasoning_tokens"):
            value = getattr(self, name)
            if value:
                values[name] = value
        return values


def normalize_ai_usage(raw_usage: object) -> NormalizedAIUsage:
    if not isinstance(raw_usage, dict):
        return NormalizedAIUsage()
    input_details = _mapping(raw_usage.get("input_tokens_details"))
    prompt_details = _mapping(raw_usage.get("prompt_tokens_details"))
    output_details = _mapping(raw_usage.get("output_tokens_details"))
    completion_details = _mapping(raw_usage.get("completion_tokens_details"))
    input_tokens = _first(raw_usage.get("input_tokens"), raw_usage.get("prompt_tokens"))
    output_tokens = _first(raw_usage.get("output_tokens"), raw_usage.get("completion_tokens"))
    cached = _first(
        raw_usage.get("cached_input_tokens"),
        raw_usage.get("cache_read_input_tokens"),
        raw_usage.get("prompt_cache_hit_tokens"),
        input_details.get("cached_tokens"),
        prompt_details.get("cached_tokens"),
    ) or 0
    cache_write = _first(
        raw_usage.get("cache_creation_input_tokens"),
        raw_usage.get("prompt_cache_write_tokens"),
    ) or 0
    if input_tokens is not None and {
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    } & raw_usage.keys():
        input_tokens += cached + cache_write
    if input_tokens is None:
        cache_miss = _count(raw_usage.get("prompt_cache_miss_tokens"))
        if cache_miss is not None:
            input_tokens = cache_miss + cached + cache_write
    total = _count(raw_usage.get("total_tokens"))
    if total is None and input_tokens is not None and output_tokens is not None:
        total = input_tokens + output_tokens
    reasoning = _first(
        raw_usage.get("reasoning_tokens"),
        output_details.get("reasoning_tokens"),
        completion_details.get("reasoning_tokens"),
    ) or 0
    return NormalizedAIUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total,
        cached_input_tokens=cached,
        cache_write_input_tokens=cache_write,
        reasoning_tokens=reasoning,
    )


@dataclass(frozen=True)
class AITokenPricing:
    provider: str
    model: str
    input_rmb_per_million: float
    output_rmb_per_million: float
    cache_hit_rmb_per_million: float | None = None
    cache_write_rmb_per_million: float | None = None
    source: str = "configured estimate"


CostStatus = Literal["estimated", "unknown"]


@dataclass(frozen=True)
class AICostEstimate:
    amount_rmb: float | None
    status: CostStatus
    provider: str | None
    model: str | None
    reason: str = ""
    source: str | None = None

    def as_dict(self) -> dict[str, object]:
        values: dict[str, object] = {
            "status": self.status,
            "provider": self.provider,
            "model": self.model,
        }
        if self.amount_rmb is not None:
            values["amount_rmb"] = self.amount_rmb
        if self.reason:
            values["reason"] = self.reason
        if self.source:
            values["source"] = self.source
        return values


def _usd(
    provider: str,
    model: str,
    input_usd: float,
    output_usd: float,
    *,
    cache_hit_usd: float | None = None,
    cache_write_usd: float | None = None,
) -> AITokenPricing:
    rate = 7.2
    return AITokenPricing(
        provider=provider,
        model=model,
        input_rmb_per_million=round(input_usd * rate, 6),
        output_rmb_per_million=round(output_usd * rate, 6),
        cache_hit_rmb_per_million=(
            round(cache_hit_usd * rate, 6) if cache_hit_usd is not None else None
        ),
        cache_write_rmb_per_million=(
            round(cache_write_usd * rate, 6) if cache_write_usd is not None else None
        ),
        source="public list estimate; USD/CNY=7.2",
    )


_PRICING: dict[tuple[str, str], AITokenPricing] = {
    # DeepSeek peak rates are used so the estimate does not understate cost.
    ("deepseek", "deepseek-flash"): _usd(
        "deepseek", "deepseek-flash", 0.30, 1.20, cache_hit_usd=0.006
    ),
    ("deepseek", "deepseek-v4-pro"): _usd(
        "deepseek", "deepseek-v4-pro", 1.32, 3.96, cache_hit_usd=0.044
    ),
    ("volc_ark", "doubao-seed-2-0-pro-260215"): AITokenPricing(
        provider="volc_ark",
        model="doubao-seed-2-0-pro-260215",
        input_rmb_per_million=6.0,
        output_rmb_per_million=30.0,
        cache_hit_rmb_per_million=1.2,
        source="configured estimate; verify against Ark account pricing",
    ),
    ("claude", "claude-opus-4-8"): _usd(
        "claude", "claude-opus-4-8", 15.0, 75.0,
        cache_hit_usd=1.5, cache_write_usd=18.75,
    ),
    ("claude", "claude-sonnet-4-6"): _usd(
        "claude", "claude-sonnet-4-6", 3.0, 15.0,
        cache_hit_usd=0.3, cache_write_usd=3.75,
    ),
}
_ALIASES = {
    ("deepseek", "deepseek"): "deepseek-flash",
    ("deepseek", "deepseek-v4-flash"): "deepseek-flash",
    ("volc_ark", "volc-doubao-pro"): "doubao-seed-2-0-pro-260215",
    ("claude", "claude-sonnet-4"): "claude-sonnet-4-6",
}


def token_pricing(provider: str | None, model: str | None) -> AITokenPricing | None:
    if not provider or not model:
        return None
    key = (provider.strip().lower(), model.strip().lower())
    return _PRICING.get((key[0], _ALIASES.get(key, key[1])))


def estimate_ai_cost(
    *,
    provider: str | None,
    model: str | None,
    usage: NormalizedAIUsage | object,
) -> AICostEstimate:
    normalized = usage if isinstance(usage, NormalizedAIUsage) else normalize_ai_usage(usage)
    pricing = token_pricing(provider, model)
    if pricing is None:
        return AICostEstimate(None, "unknown", provider, model, "no pricing configured for provider/model")
    if normalized.input_tokens is None or normalized.output_tokens is None:
        return AICostEstimate(None, "unknown", provider, model, "provider returned incomplete token usage", pricing.source)
    cached = normalized.cached_input_tokens
    cache_write = normalized.cache_write_input_tokens
    uncached = normalized.input_tokens - cached - cache_write
    if uncached < 0:
        return AICostEstimate(None, "unknown", provider, model, "provider token usage has an invalid cache breakdown", pricing.source)
    if cached and pricing.cache_hit_rmb_per_million is None:
        return AICostEstimate(None, "unknown", provider, model, "cache hit pricing is not configured", pricing.source)
    if cache_write and pricing.cache_write_rmb_per_million is None:
        return AICostEstimate(None, "unknown", provider, model, "cache write pricing is not configured", pricing.source)
    amount = (
        uncached * pricing.input_rmb_per_million
        + cached * (pricing.cache_hit_rmb_per_million or 0.0)
        + cache_write * (pricing.cache_write_rmb_per_million or 0.0)
        + normalized.output_tokens * pricing.output_rmb_per_million
    ) / 1_000_000
    return AICostEstimate(round(amount, 6), "estimated", provider, model, source=pricing.source)
