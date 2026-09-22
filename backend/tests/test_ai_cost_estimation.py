from app.services.ai_calls import AITextCallResult
from app.services.ai_costs import normalize_ai_usage


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
