import argparse
import asyncio
import json

import pytest

from app.services.ai_base import AITextServiceBase
from app.services.ai_streaming import AITextStreamChunk
from app.services.memoir.interview_agent import InterviewConfig
from scripts.eval_memoir_interview import EvalCase, load_dataset, run_case
from scripts import eval_memoir_interview as evaluator


def dataset(tmp_path, *, mode="autonomous", role="interviewee_candidate"):
    source = {
        "business_goal": "讲清自己的故事",
        "context": [{"speaker_label": "讲述者", "role": role, "text": "我改一下，是姐姐陪我去的。"}],
    }
    if mode == "guided":
        source["turn_goal"] = "承接更正，不猜测其他人物。"
    row = {"id": "case-1", "messages": [
        {"role": "system", "content": "DATASET_SYSTEM_MUST_NOT_REPLACE_PRODUCT_PROMPT"},
        {"role": "user", "content": json.dumps(source)},
    ]}
    (tmp_path / f"dev.observed_interview.{mode}.inputs.jsonl").write_text(json.dumps(row) + "\n")
    meta = {
        "id": "case-1", "status": "active", "split": "dev", "split_group": "person-1",
        "track": "observed_interview", "difficulty": ["correction"],
        "quality": {"review_status": "pending_human_review"},
        "provenance": {"source_url": "https://example.org/source", "content_sha256": "abc"},
        "annotations_judge_only": {"reference_response": "HIDDEN_REFERENCE"},
        "evidence_judge_only": {"future": "HIDDEN_FUTURE"},
    }
    (tmp_path / "cases.jsonl").write_text(json.dumps(meta) + "\n")
    (tmp_path / "manifest.json").write_text('{}')
    return load_dataset(tmp_path, split="dev", track="observed_interview", mode=mode)[0][0]


async def test_dataset_adapter_isolates_judge_data_and_preserves_correction(tmp_path):
    case = dataset(tmp_path)
    restored = EvalCase.model_validate_json(case.model_dump_json())
    assert restored == case
    result = await run_case(case, InterviewConfig(), live=False, policy_mode="off")
    assert result["status"] == "dry_run"
    assert "是姐姐陪我去的" in result["prompt"]
    assert "HIDDEN" not in json.dumps(result)
    assert "DATASET_SYSTEM" not in json.dumps(result)
    assert case.split_group == "person-1"
    assert case.review_status == "pending_human_review"


async def test_given_goal_is_separate_from_autonomous_mode(tmp_path):
    case = dataset(tmp_path, mode="guided")
    result = await run_case(case, InterviewConfig(), live=False, policy_mode="off")
    assert "本轮给定访谈目标：承接更正" in result["prompt"]
    assert case.mode == "guided"


def test_unknown_speaker_role_is_rejected_instead_of_guessed(tmp_path):
    with pytest.raises(ValueError):
        dataset(tmp_path, role="unknown")


class EvaluationAI(AITextServiceBase):
    provider_name = "evaluation-fake"
    model_name = "evaluation-fake-model"

    async def _call_llm(self, *args, **kwargs):
        return '{"selected_action":"continue_listening"}', {
            "model": self.model_name, "usage": {"input_tokens": 11, "output_tokens": 3},
            "choices": [{"finish_reason": "stop"}],
        }

    async def stream_llm(self, *args, **kwargs):
        yield AITextStreamChunk(text_delta="[listen]\n是姐姐陪您去的，您接着说。")
        yield AITextStreamChunk(usage={"input_tokens": 21, "output_tokens": 13}, finish_reason="stop")


async def test_active_policy_uses_shared_application_rule_and_records_both_calls(tmp_path):
    result = await run_case(
        dataset(tmp_path), InterviewConfig(requested_model="evaluation-fake-model"),
        live=True, policy_mode="active", client_factory=lambda model: EvaluationAI(),
    )
    assert result["status"] == "completed"
    assert result["policy_decision"]["applies_to_reply"] is True
    assert "继续倾听" in result["prompt"]
    assert result["policy_calls"][0]["usage"] == {"input_tokens": 11, "output_tokens": 3}
    assert result["run"]["usage"] == {"input_tokens": 21, "output_tokens": 13}
    assert result["turn"]["speech_text"] == "是姐姐陪您去的，您接着说。"
    assert result["first_text_seconds"] is not None


async def test_provider_failure_keeps_case_and_unknown_usage(tmp_path):
    class BrokenAI(EvaluationAI):
        async def stream_llm(self, *args, **kwargs):
            raise ConnectionError("provider unavailable")
            yield

    result = await run_case(
        dataset(tmp_path), InterviewConfig(), live=True, policy_mode="off", client_factory=lambda model: BrokenAI(),
    )
    assert result["status"] == "failed"
    assert result["error"]["type"] == "ConnectionError"
    assert result["case"]["id"] == "case-1"
    assert result["reply_estimated_cost"] is None


async def test_shadow_policy_is_measured_without_changing_reply_prompt(tmp_path):
    case = dataset(tmp_path)
    result = await run_case(
        case, InterviewConfig(), live=True, policy_mode="shadow", client_factory=lambda model: EvaluationAI(),
    )
    baseline = await run_case(case, InterviewConfig(), live=False, policy_mode="off")
    assert result["prompt"] == baseline["prompt"]
    assert result["policy_decision"]["execution_mode"] == "shadow"
    assert result["policy_decision"]["applies_to_reply"] is False
    assert len(result["policy_calls"]) == 1
    assert result["reply_seconds"] <= result["seconds"]


async def test_policy_failure_preserves_live_reply_fallback_and_exposes_failure(tmp_path):
    class FailedPolicy(EvaluationAI):
        async def _call_llm(self, *args, **kwargs):
            raise ConnectionError("policy unavailable")

    result = await run_case(
        dataset(tmp_path), InterviewConfig(), live=True, policy_mode="active",
        client_factory=lambda model: FailedPolicy(),
    )
    assert result["status"] == "completed"
    assert result["policy_error"]["type"] == "ConnectionError"
    assert result["policy_decision"] is None
    assert result["turn"]["speech_text"]


async def test_concurrent_evaluation_retains_all_repeats_and_failures(tmp_path, monkeypatch):
    first = dataset(tmp_path)
    second = first.model_copy(update={"id": "case-2"})
    inputs = tmp_path / "inputs.jsonl"
    inputs.write_text(first.model_dump_json() + "\n" + second.model_dump_json() + "\n")
    active = 0
    maximum = 0

    async def fake_run(case, config, **kwargs):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0)
        active -= 1
        return {"case": {"id": case.id}, "status": "failed" if case.id == "case-2" else "completed",
                "policy_error": None, "seconds": 0.01}

    monkeypatch.setattr(evaluator, "run_case", fake_run)
    monkeypatch.setattr(evaluator, "git_output", lambda *args: "test-snapshot")
    output = tmp_path / "run"
    args = argparse.Namespace(dataset=None, cases=inputs, split="dev", case=None, max_cases=10,
                              ai_model="test", temperature=0.72, prompt_variant="baseline",
                              policy_mode="shadow", live=True, output=output, repeat=2, concurrency=2)
    assert await evaluator.evaluate(args) == 1
    rows = evaluator.read_jsonl(output / "results.jsonl")
    assert {(row["case"]["id"], row["repeat"]) for row in rows} == {
        (identifier, repeat) for identifier in ("case-1", "case-2") for repeat in range(2)
    }
    assert len(rows) == 4 and maximum == 2
    assert json.loads((output / "summary.json").read_text()) == {"attempts": 4, "failed": 2, "policy_failed": 0}
