from datetime import UTC, datetime

import pytest

from app.services.ai_calls import AITextCallResult, with_attempt_totals
from app.services.ai_costs import (
    estimate_audio_cost,
    estimate_image_cost,
    estimate_ai_cost,
    estimate_text_cost,
    normalize_ai_usage,
)


def test_normalizes_provider_usage_and_estimates_deepseek_cost():
    usage = normalize_ai_usage(
        {
            "prompt_tokens": 1_000,
            "completion_tokens": 200,
            "prompt_cache_hit_tokens": 400,
            "total_tokens": 1_200,
        }
    )
    assert usage.input_tokens == 1_000
    assert usage.output_tokens == 200
    assert usage.cached_input_tokens == 400
    assert usage.total_tokens == 1_200

    result = AITextCallResult(
        content="完成",
        provider="deepseek",
        requested_model="deepseek-flash",
        resolved_model="deepseek-flash",
        response_model="deepseek-flash",
        finish_reason="stop",
        usage={
            "prompt_tokens": 1_000,
            "completion_tokens": 200,
            "prompt_cache_hit_tokens": 400,
            "total_tokens": 1_200,
        },
        # Wednesday 10:00 Beijing time, inside DeepSeek peak hours.
        completed_at=datetime(2026, 10, 7, 2, 0, tzinfo=UTC),
    )
    assert result.cost_estimate.status == "estimated"
    # 600 uncached * 2 + 400 cached * 0.04 + 200 output * 8 (RMB per million)
    assert result.cost_estimate.amount_rmb == 0.002816
    assert result.diagnostics()["normalized_usage"] == {
        "input_tokens": 1_000,
        "output_tokens": 200,
        "total_tokens": 1_200,
        "cached_input_tokens": 400,
    }


def test_unknown_model_or_incomplete_usage_does_not_invent_cost():
    unknown = AITextCallResult(
        content="完成",
        provider="unknown",
        requested_model="unknown",
        resolved_model="unknown",
        response_model="unknown",
        finish_reason="stop",
        usage={"total_tokens": 10},
    )
    assert unknown.cost_estimate.amount_rmb is None
    assert unknown.cost_estimate.status == "unknown"


def test_normalizes_anthropic_cache_tokens_into_total_input():
    usage = normalize_ai_usage(
        {
            "input_tokens": 80,
            "cache_read_input_tokens": 15,
            "cache_creation_input_tokens": 5,
            "output_tokens": 20,
        }
    )
    assert usage.input_tokens == 100
    assert usage.cached_input_tokens == 15
    assert usage.cache_write_input_tokens == 5
    assert usage.total_tokens == 120


@pytest.mark.parametrize("model", [
    "doubao-seedream-5-0-lite-260128",
    "doubao-seedream-5-0-260128",
])
@pytest.mark.parametrize(("image_count", "expected_cost"), [(1, 0.22), (5, 1.10)])
def test_seedream_requested_and_response_models_share_image_pricing(model, image_count, expected_cost):
    image = estimate_image_cost(
        provider="volc_ark",
        model=model,
        image_count=image_count,
    )
    assert image.amount_rmb == expected_cost
    assert image.status == "estimated"


def test_unconfigured_image_model_does_not_inherit_seedream_pricing():
    image = estimate_image_cost(provider="volc_ark", model="doubao-seedream-unconfigured", image_count=1)
    assert image.amount_rmb is None
    assert image.status == "unknown"


def test_estimates_audio_and_tts_costs_and_removes_claude_pricing():
    asr = estimate_audio_cost(
        provider="stepfun",
        model="stepaudio-3-asr-max",
        duration_seconds=90,
    )
    assert asr.amount_rmb == 0.07

    tts = estimate_text_cost(
        provider="volc_tts",
        model="seed-tts-2.0",
        character_count=1_000,
    )
    assert tts.amount_rmb == 0.3

    claude = estimate_ai_cost(
        provider="claude",
        model="claude-opus-4-8",
        usage={"input_tokens": 10, "output_tokens": 10},
    )
    assert claude.amount_rmb is None
    assert claude.status == "unknown"


@pytest.mark.parametrize("provider", ["volc_streaming_asr", "volc_streaming_asr_reprocess"])
@pytest.mark.parametrize(("duration_seconds", "expected_cost"), [
    (60, 0.016667),
    # A real 4m18s recording was charged 0.322499 RMB at legacy ASR 1.0's 4.5 RMB/hour.
    (257.998875, 0.071666),
])
def test_seed_asr_2_prices_realtime_and_reprocessing_at_one_rmb_per_hour(
    provider, duration_seconds, expected_cost,
):
    cost = estimate_audio_cost(
        provider=provider,
        model="volc.seedasr.sauc.duration",
        duration_seconds=duration_seconds,
    )
    assert cost.status == "estimated"
    assert cost.amount_rmb == expected_cost


def test_volc_seed_2_pro_uses_input_context_pricing_tiers():
    short = estimate_ai_cost(
        provider="volc_ark",
        model="doubao-seed-2-0-pro-260215",
        usage={"input_tokens": 1_000, "output_tokens": 1_000},
    )
    long = estimate_ai_cost(
        provider="volc_ark",
        model="doubao-seed-2-0-pro-260215",
        usage={"input_tokens": 40_000, "output_tokens": 1_000},
    )
    assert short.amount_rmb == 0.0192
    assert long.amount_rmb == 0.216


@pytest.mark.parametrize(("input_tokens", "expected_cost"), [
    (20_000, 0.0544),
    (40_000, 0.1776),
    (200_000, 1.8912),
])
def test_volc_seed_2_pro_charges_cached_input_at_its_tier_discount(input_tokens, expected_cost):
    cost = estimate_ai_cost(
        provider="volc_ark",
        model="doubao-seed-2-0-pro-260215",
        usage={
            "input_tokens": input_tokens,
            "input_tokens_details": {"cached_tokens": 10_000},
            "output_tokens": 1_000,
        },
    )
    assert cost.status == "estimated"
    assert cost.amount_rmb == expected_cost


@pytest.mark.parametrize("text, expected", [("字" * 10000, 0.9), ("你好，Hi!", 0.00036)])
def test_step_mini_prices_chinese_letters_and_punctuation(text, expected):
    cost = estimate_text_cost(provider="stepfun", model="step-tts-mini", character_count=len(text), text=text)
    assert cost.status == "estimated"
    assert cost.amount_rmb == expected


def test_deepseek_off_peak_hours_cost_half_of_list_price():
    usage = {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}

    def cost(at: datetime) -> float | None:
        return estimate_ai_cost(provider="deepseek", model="deepseek-flash", usage=usage, at=at).amount_rmb

    # Wednesday 2026-10-07, Beijing time = UTC + 8.
    assert cost(datetime(2026, 10, 7, 3, 59, tzinfo=UTC)) == 10.0  # 11:59 peak
    assert cost(datetime(2026, 10, 7, 4, 0, tzinfo=UTC)) == 5.0  # 12:00 lunch break
    assert cost(datetime(2026, 10, 7, 6, 0, tzinfo=UTC)) == 10.0  # 14:00 peak
    assert cost(datetime(2026, 10, 7, 10, 0, tzinfo=UTC)) == 5.0  # 18:00 evening
    assert cost(datetime(2026, 10, 10, 2, 0, tzinfo=UTC)) == 5.0  # Saturday 10:00
    assert estimate_ai_cost(
        provider="deepseek", model="deepseek-v4-pro", usage=usage,
        at=datetime(2026, 10, 7, 2, 0, tzinfo=UTC),
    ).amount_rmb == 36.0


def test_retried_structured_output_prices_each_attempt_on_its_own_tier():
    attempts = [
        AITextCallResult(
            content="{}",
            provider="volc_ark",
            requested_model=None,
            resolved_model="doubao-seed-2-0-pro-260215",
            response_model="doubao-seed-2-0-pro-260215",
            finish_reason="stop",
            usage={"input_tokens": 20_000, "output_tokens": 1_000},
        )
        for _ in range(2)
    ]
    total = with_attempt_totals(attempts[-1], attempts=attempts, elapsed_ms=0)

    assert total.normalized_usage.input_tokens == 40_000
    # Each 20k-token request stays on the <=32k tier: 2 * (0.064 + 0.016).
    assert total.cost_estimate.amount_rmb == 0.16


async def test_interrupted_stream_still_records_final_usage(monkeypatch):
    import asyncio

    from app.services import ai_calls
    from app.services.ai_streaming import AITextStreamChunk

    recorded: list[dict] = []
    finished = asyncio.Event()

    async def fake_record(**kwargs):
        recorded.append(kwargs)
        finished.set()

    class FakeClient:
        provider_name = "deepseek"
        model_name = "deepseek-flash"

        async def stream_llm(self, *_args, **_kwargs):
            yield AITextStreamChunk(text_delta="你好")
            await asyncio.sleep(0)
            yield AITextStreamChunk(text_delta="，再讲讲")
            yield AITextStreamChunk(usage={"prompt_tokens": 100, "completion_tokens": 20})

    monkeypatch.setattr(ai_calls, "record_ai_usage", fake_record)
    stream = ai_calls.stream_text_with_client(
        FakeClient(), "system", "user",
        requested_model=None, temperature=0.2, max_tokens=100, timeout=10,
    )
    async for chunk in stream:
        assert chunk.text_delta == "你好"
        break
    await stream.aclose()

    await asyncio.wait_for(finished.wait(), timeout=1)
    [record] = recorded
    assert record["status"] == "succeeded"
    assert record["usage"].output_tokens == 20
    assert record["cost"].amount_rmb is not None


async def test_rejected_structured_output_bills_the_tokens_it_used(monkeypatch):
    from pydantic import BaseModel

    from app.services import ai_calls
    from app.services.ai_calls import AIInvalidStructuredOutputError, generate_structured_with_client

    recorded: list[dict] = []

    async def fake_record(**kwargs):
        recorded.append(kwargs)

    class Output(BaseModel):
        title: str

    class FakeClient:
        provider_name = "deepseek"
        model_name = "deepseek-flash"

        async def generate_structured(self, *_args, **_kwargs):
            raise AIInvalidStructuredOutputError("bad", response=AITextCallResult(
                content="{}",
                provider="deepseek",
                requested_model=None,
                resolved_model="deepseek-flash",
                response_model="deepseek-flash",
                finish_reason="stop",
                usage={"prompt_tokens": 1_000, "completion_tokens": 100},
            ))

    monkeypatch.setattr(ai_calls, "record_ai_usage", fake_record)
    try:
        await generate_structured_with_client(
            FakeClient(), "system", "user", output_model=Output,
            requested_model=None, temperature=0.2, max_tokens=100, timeout=10, max_attempts=1,
        )
    except AIInvalidStructuredOutputError:
        pass
    [record] = recorded
    assert record["status"] == "failed"
    assert record["billable"] is True
    assert record["cost"].amount_rmb is not None
