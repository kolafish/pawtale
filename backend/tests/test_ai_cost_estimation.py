from app.services.ai_calls import AITextCallResult
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
    )
    assert result.cost_estimate.status == "estimated"
    assert result.cost_estimate.amount_rmb == 0.003041
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


def test_estimates_off_text_provider_costs_and_removes_claude_pricing():
    image = estimate_image_cost(
        provider="volc_ark",
        model="doubao-seedream-5-0-lite-260128",
        image_count=2,
    )
    assert image.amount_rmb == 0.44

    asr = estimate_audio_cost(
        provider="stepfun",
        model="stepaudio-3-asr-max",
        duration_seconds=90,
    )
    assert asr.amount_rmb == 0.07

    realtime_asr = estimate_audio_cost(
        provider="volc_streaming_asr",
        model="volc.seedasr.sauc.duration",
        duration_seconds=60,
    )
    assert realtime_asr.amount_rmb == 0.075

    tts = estimate_text_cost(
        provider="volc_tts",
        model="seed-tts-2.0",
        character_count=1_000,
    )
    assert tts.amount_rmb == 0.5

    claude = estimate_ai_cost(
        provider="claude",
        model="claude-opus-4-8",
        usage={"input_tokens": 10, "output_tokens": 10},
    )
    assert claude.amount_rmb is None
    assert claude.status == "unknown"


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
