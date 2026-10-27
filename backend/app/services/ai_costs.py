from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Literal
import unicodedata

from chinese_calendar import is_holiday


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
    # Optional context-length tiers. Each tuple is
    # (max_input_tokens, input_rmb_per_million, output_rmb_per_million,
    #  cache_hit_rmb_per_million, cache_write_rmb_per_million). A None max
    # value is the final open-ended tier.
    context_tiers: tuple[tuple[int | None, float, float, float | None, float | None], ...] = ()
    # Multiplier applied to every rate outside DeepSeek's peak hours; None means
    # the provider has no time-of-day pricing.
    off_peak_multiplier: float | None = None

    def rates_for_input_tokens(
        self,
        input_tokens: int,
        *,
        at: datetime | None = None,
    ) -> tuple[float, float, float | None, float | None]:
        rates = (
            self.input_rmb_per_million,
            self.output_rmb_per_million,
            self.cache_hit_rmb_per_million,
            self.cache_write_rmb_per_million,
        )
        for max_input_tokens, input_rate, output_rate, cache_hit_rate, cache_write_rate in (
            self.context_tiers
        ):
            if max_input_tokens is None or input_tokens <= max_input_tokens:
                rates = (input_rate, output_rate, cache_hit_rate, cache_write_rate)
                break
        if self.off_peak_multiplier is None or is_deepseek_peak_time(at):
            return rates
        multiplier = self.off_peak_multiplier
        return tuple(  # type: ignore[return-value]
            rate * multiplier if rate is not None else None for rate in rates
        )


_BEIJING = timezone(timedelta(hours=8))
# DeepSeek excludes Chinese public holidays and all weekends from peak pricing,
# even when a weekend is a make-up workday. Update chinesecalendar each year
# after the State Council publishes the next holiday schedule.
_DEEPSEEK_PEAK_HOURS = ((9, 12), (14, 18))


def is_deepseek_peak_time(at: datetime | None = None) -> bool:
    moment = at or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    local = moment.astimezone(_BEIJING)
    if local.weekday() >= 5 or not any(start <= local.hour < end for start, end in _DEEPSEEK_PEAK_HOURS):
        return False
    return not is_holiday(local.date())


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


@dataclass(frozen=True)
class AIUnitPricing:
    provider: str
    model: str
    unit: str
    rmb_per_unit: float
    source: str


_PRICING: dict[tuple[str, str], AITokenPricing] = {
    # DeepSeek list prices are peak rates; off-peak hours cost half.
    ("deepseek", "deepseek-flash"): AITokenPricing(
        provider="deepseek",
        model="deepseek-flash",
        input_rmb_per_million=2.0,
        output_rmb_per_million=8.0,
        cache_hit_rmb_per_million=0.04,
        source="https://api-docs.deepseek.com/zh-cn/quick_start/pricing",
        off_peak_multiplier=0.5,
    ),
    ("deepseek", "deepseek-v4-pro"): AITokenPricing(
        provider="deepseek",
        model="deepseek-v4-pro",
        input_rmb_per_million=9.0,
        output_rmb_per_million=27.0,
        cache_hit_rmb_per_million=0.30,
        source="https://api-docs.deepseek.com/zh-cn/quick_start/pricing",
        off_peak_multiplier=0.5,
    ),
    ("volc_ark", "doubao-seed-2-0-pro-260215"): AITokenPricing(
        provider="volc_ark",
        model="doubao-seed-2-0-pro-260215",
        input_rmb_per_million=3.2,
        output_rmb_per_million=16.0,
        cache_hit_rmb_per_million=0.64,
        source="https://docs.volcengine.com/docs/ark/model-pricing?lang=zh",
        context_tiers=(
            (32_000, 3.2, 16.0, 0.64, None),
            (128_000, 4.8, 24.0, 0.96, None),
            (None, 9.6, 48.0, 1.92, None),
        ),
    ),
}
_ALIASES = {
    ("deepseek", "deepseek"): "deepseek-flash",
    ("deepseek", "deepseek-v4-flash"): "deepseek-flash",
    ("volc_ark", "volc-doubao-pro"): "doubao-seed-2-0-pro-260215",
    ("volc_ark", "doubao-seedream-5-0-lite-260128"): "doubao-seedream-5-0-260128",
}

_UNIT_PRICING: dict[tuple[str, str], AIUnitPricing] = {
    ("volc_ark", "doubao-seedream-5-0-flash-260915"): AIUnitPricing(
        provider="volc_ark", model="doubao-seedream-5-0-flash-260915", unit="image", rmb_per_unit=0.12,
        source="https://docs.volcengine.com/docs/ark/model-pricing?lang=en&redirect=1",
    ),
    ("volc_ark", "doubao-seedream-5-0-260128"): AIUnitPricing(
        provider="volc_ark",
        model="doubao-seedream-5-0-260128",
        unit="image",
        rmb_per_unit=0.22,
        source="https://docs.volcengine.com/docs/ark/model-pricing?lang=zh&redirect=1",
    ),
    ("stepfun", "stepaudio-3-asr-max"): AIUnitPricing(
        provider="stepfun",
        model="stepaudio-3-asr-max",
        unit="audio_hour",
        rmb_per_unit=2.8,
        source="https://platform.stepfun.com/docs/zh/guides/pricing/details",
    ),
    ("stepfun", "step-tts-mini"): AIUnitPricing(
        provider="stepfun",
        model="step-tts-mini",
        unit="text_character",
        rmb_per_unit=0.00009,
        source="https://platform.stepfun.com/docs/zh/guides/pricing/details",
    ),
    ("volc_streaming_asr", "volc.seedasr.sauc.duration"): AIUnitPricing(
        provider="volc_streaming_asr",
        model="volc.seedasr.sauc.duration",
        unit="audio_hour",
        rmb_per_unit=1.0,
        source="https://docs.volcengine.com/docs/DoubaoVoice/Billinginstructions-21?lang=zh",
    ),
    ("volc_streaming_asr_reprocess", "volc.seedasr.sauc.duration"): AIUnitPricing(
        provider="volc_streaming_asr_reprocess",
        model="volc.seedasr.sauc.duration",
        unit="audio_hour",
        rmb_per_unit=1.0,
        source="https://docs.volcengine.com/docs/DoubaoVoice/Billinginstructions-21?lang=zh",
    ),
    ("volc_tts", "seed-tts-2.0"): AIUnitPricing(
        provider="volc_tts",
        model="seed-tts-2.0",
        unit="text_character",
        rmb_per_unit=0.0003,
        source="https://docs.volcengine.com/docs/DoubaoVoice/Billinginstructions-21?lang=zh",
    ),
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
    at: datetime | None = None,
) -> AICostEstimate:
    """Estimate one upstream request; ``at`` picks time-of-day rates (default: now)."""
    normalized = usage if isinstance(usage, NormalizedAIUsage) else normalize_ai_usage(usage)
    pricing = token_pricing(provider, model)
    if pricing is None:
        return AICostEstimate(None, "unknown", provider, model, "no pricing configured for provider/model")
    if normalized.input_tokens is None or normalized.output_tokens is None:
        return AICostEstimate(None, "unknown", provider, model, "provider returned incomplete token usage", pricing.source)
    try:
        input_rate, output_rate, cache_hit_rate, cache_write_rate = pricing.rates_for_input_tokens(
            normalized.input_tokens,
            at=at,
        )
    except NotImplementedError:
        # A missing calendar year must remain visibly unpriced, not silently
        # charge the peak rate or discard an otherwise successful AI response.
        return AICostEstimate(
            None, "unknown", provider, model,
            "DeepSeek holiday calendar does not cover this date; update chinesecalendar",
            pricing.source,
        )
    cached = normalized.cached_input_tokens
    cache_write = normalized.cache_write_input_tokens
    uncached = normalized.input_tokens - cached - cache_write
    if uncached < 0:
        return AICostEstimate(None, "unknown", provider, model, "provider token usage has an invalid cache breakdown", pricing.source)
    if cached and cache_hit_rate is None:
        return AICostEstimate(None, "unknown", provider, model, "cache hit pricing is not configured", pricing.source)
    if cache_write and cache_write_rate is None:
        return AICostEstimate(None, "unknown", provider, model, "cache write pricing is not configured", pricing.source)
    amount = (
        uncached * input_rate
        + cached * (cache_hit_rate or 0.0)
        + cache_write * (cache_write_rate or 0.0)
        + normalized.output_tokens * output_rate
    ) / 1_000_000
    return AICostEstimate(round(amount, 6), "estimated", provider, model, source=pricing.source)


def _unit_pricing(provider: str | None, model: str | None) -> AIUnitPricing | None:
    if not provider or not model:
        return None
    key = (provider.strip().lower(), model.strip().lower())
    return _UNIT_PRICING.get((key[0], _ALIASES.get(key, key[1])))


def estimate_image_cost(
    *,
    provider: str | None,
    model: str | None,
    image_count: int = 1,
) -> AICostEstimate:
    pricing = _unit_pricing(provider, model)
    if pricing is None or pricing.unit != "image":
        return AICostEstimate(None, "unknown", provider, model, "no image pricing configured")
    if image_count <= 0:
        return AICostEstimate(None, "unknown", provider, model, "image count must be positive", pricing.source)
    return AICostEstimate(
        round(image_count * pricing.rmb_per_unit, 6),
        "estimated",
        provider,
        model,
        source=pricing.source,
    )


def estimate_audio_cost(
    *,
    provider: str | None,
    model: str | None,
    duration_seconds: float | int | None,
) -> AICostEstimate:
    pricing = _unit_pricing(provider, model)
    if pricing is None or pricing.unit != "audio_hour":
        return AICostEstimate(None, "unknown", provider, model, "no audio pricing configured")
    if not isinstance(duration_seconds, (int, float)) or isinstance(duration_seconds, bool):
        return AICostEstimate(None, "unknown", provider, model, "audio duration is required", pricing.source)
    if duration_seconds <= 0:
        return AICostEstimate(None, "unknown", provider, model, "audio duration must be positive", pricing.source)
    return AICostEstimate(
        round(float(duration_seconds) / 3600 * pricing.rmb_per_unit, 6),
        "estimated",
        provider,
        model,
        source=pricing.source,
    )


def estimate_text_cost(
    *,
    provider: str | None,
    model: str | None,
    character_count: int | None,
    text: str | None = None,
) -> AICostEstimate:
    pricing = _unit_pricing(provider, model)
    if pricing is None or pricing.unit != "text_character":
        return AICostEstimate(None, "unknown", provider, model, "no text pricing configured")
    if not isinstance(character_count, int) or isinstance(character_count, bool):
        return AICostEstimate(None, "unknown", provider, model, "text character count is required", pricing.source)
    if character_count <= 0:
        return AICostEstimate(None, "unknown", provider, model, "text character count must be positive", pricing.source)
    billable_characters = float(character_count)
    if pricing.provider == "stepfun" and pricing.model == "step-tts-mini" and text is not None:
        # Step counts two English letters or punctuation marks as one character.
        billable_characters = sum(
            0.5 if (char.isascii() and char.isalpha()) or unicodedata.category(char).startswith("P") else 1
            for char in text
        )
    return AICostEstimate(
        round(billable_characters * pricing.rmb_per_unit, 6),
        "estimated",
        provider,
        model,
        source=pricing.source,
    )


def sum_cost_estimates(estimates: list[AICostEstimate]) -> AICostEstimate:
    """Add up separately billed upstream requests; any unknown part makes the total unknown."""
    first = estimates[0]
    unknown = next((estimate for estimate in estimates if estimate.amount_rmb is None), None)
    if unknown is not None:
        return unknown
    return AICostEstimate(
        round(sum(estimate.amount_rmb or 0.0 for estimate in estimates), 6),
        "estimated",
        first.provider,
        first.model,
        source=first.source,
    )
