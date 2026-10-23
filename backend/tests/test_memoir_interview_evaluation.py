import json

import pytest

from app.services.ai_base import AITextServiceBase
from app.services.ai_streaming import AITextStreamChunk
from app.services.memoir.interview_agent import InterviewConfig
from scripts.eval_memoir_interview import EvalCase, load_dataset, run_case


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
    result = await run_case(case, InterviewConfig(), live=False)
    assert result["status"] == "dry_run"
    assert "是姐姐陪我去的" in result["prompt"]
    assert "HIDDEN" not in json.dumps(result)
    assert "DATASET_SYSTEM" not in json.dumps(result)
    assert case.split_group == "person-1"
    assert case.review_status == "pending_human_review"


async def test_given_goal_is_separate_from_autonomous_mode(tmp_path):
    case = dataset(tmp_path, mode="guided")
    result = await run_case(case, InterviewConfig(), live=False)
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
