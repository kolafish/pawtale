import json
from pathlib import Path

import pytest

from scripts.eval_memoir_interview import EvalCase, run_case
from scripts.review_memoir_interview import Rating, packet, summarize
from app.services.memoir.interview_agent import InterviewConfig, InterviewInput
from app.services.memoir.voice_followup_ai import InterviewDialogue


def make_run(path, *, group="person-1", text="我在听。", status="completed", repeat=1,
             prompt="请邀请讲述者继续。", system_prompt="你是一位访谈员。"):
    path.mkdir()
    case = EvalCase(
        id="case-1", split="dev", split_group=group, track="observed_interview", mode="autonomous",
        scenario="narration_follow_up", snapshot=InterviewInput(InterviewDialogue("open_narration", ())),
    )
    (path / "manifest.json").write_text(json.dumps({"case_ids": [case.id], "repeat": repeat}))
    row = {
        "case": case.model_dump(mode="json"), "repeat": 0, "status": status,
        "prompt": prompt, "system_prompt": system_prompt,
        "turn": {"speech_text": text, "conversation_action": "listen"} if status == "completed" else None,
        "first_text_seconds": 0.4, "policy_error": None, "policy_calls": [], "reply_estimated_cost": None,
    }
    (path / "results.jsonl").write_text(json.dumps(row) + "\n")
    return path


def test_review_packet_hides_run_identity_and_keeps_pending_scores(tmp_path):
    run = make_run(tmp_path / "secret-model-name")
    output = tmp_path / "packet"
    packet([run], output, 41)
    assert "secret-model-name" not in (output / "review.md").read_text()
    assert "secret-model-name" not in (output / "review.json").read_text()
    result = summarize(output, output / "ratings.jsonl")
    assert result["human_review_complete"] is False
    assert result["groups"] == []
    assert result["runtime"][0]["unknown_cost_items"] == 1


def test_mismatched_or_incomplete_runs_cannot_be_compared(tmp_path):
    a = make_run(tmp_path / "a")
    b = make_run(tmp_path / "b", group="different-person")
    with pytest.raises(ValueError, match="snapshot mismatch"):
        packet([a, b], tmp_path / "packet", 41)
    incomplete = make_run(tmp_path / "incomplete", repeat=2)
    with pytest.raises(ValueError, match="Incomplete run"):
        packet([incomplete], tmp_path / "packet2", 41)


def test_agent_scores_never_become_human_scores_and_severe_failure_is_separate(tmp_path):
    run = make_run(tmp_path / "run")
    output = tmp_path / "packet"
    packet([run], output, 41)
    rating = Rating(
        packet_sha256=json.loads((output / "ratings.jsonl").read_text())["packet_sha256"],
        item_id="1:A", reviewer_kind="agent", reviewer="codex", input_checked=True,
        scores={key: 2 for key in ["continuity", "value", "grounding", "boundaries", "non_repetition"]},
        severe_failures=["ignored_correction"], rationale="回复仍使用了已经被用户纠正的人物。",
    )
    (output / "agent.jsonl").write_text(rating.model_dump_json() + "\n")
    result = summarize(output, output / "agent.jsonl")
    assert result["human_checked_items"] == 0
    assert result["human_review_complete"] is False
    assert result["aggregates"][0]["reviewer_kind"] == "agent"
    assert result["severe_failures"][0]["severe_failures"] == ["ignored_correction"]
    rating.scores = None
    (output / "agent.jsonl").write_text(rating.model_dump_json() + "\n")
    assert summarize(output, output / "agent.jsonl")["severe_failures"]


def test_scoring_requires_attribution_and_bounded_dimensions():
    with pytest.raises(ValueError):
        Rating(packet_sha256="a" * 64, item_id="x",
               scores=dict(continuity=2, value=2, grounding=2, boundaries=2, non_repetition=2))
    with pytest.raises(ValueError):
        Rating(packet_sha256="a" * 64, item_id="x", reviewer_kind="human", reviewer="reviewer", rationale="checked",
               scores=dict(continuity=3, value=2, grounding=2, boundaries=2, non_repetition=2))


@pytest.mark.parametrize("change", ["run_order", "seed", "candidate"])
def test_new_packet_rejects_old_scores_even_when_item_ids_match(tmp_path, change):
    a = make_run(tmp_path / "a", text="说说小时候吧。")
    b = make_run(tmp_path / "b", text="说说工作吧。")
    old = tmp_path / "old"
    new = tmp_path / "new"
    packet([a, b], old, 41)
    rows = [json.loads(line) for line in (old / "ratings.jsonl").read_text().splitlines()]
    rows[0].update(reviewer_kind="agent", reviewer="codex", rationale="加入了未说明的经历。",
                   severe_failures=["invented_fact"],
                   scores=dict(continuity=1, value=1, grounding=0, boundaries=2, non_repetition=2))
    scores = old / "scored.jsonl"
    scores.write_text("".join(json.dumps(row) + "\n" for row in rows))
    assert summarize(old, scores)["severe_failures"]
    if change == "candidate":
        b = make_run(tmp_path / "c", text="您可以接着讲。")
    packet([b, a] if change == "run_order" else [a, b], new, 42 if change == "seed" else 41)
    with pytest.raises(ValueError, match="different review packet"):
        summarize(new, scores)


def test_each_candidate_keeps_its_actual_task_and_cropped_context(tmp_path):
    a = make_run(tmp_path / "secret-a", text="说说童年吧。", prompt="已知：出生在上海。当前策略：邀请聊童年。")
    b = make_run(tmp_path / "secret-b", text="说说工作吧。", prompt="已知：在北京工作。当前策略：邀请聊工作。",
                 system_prompt="你是一位耐心的访谈员。")
    output = tmp_path / "packet"
    packet([a, b], output, 41)
    review = json.loads((output / "review.json").read_text())[0]
    assert "observed_prompt" not in review["context"]
    assert "dialogue" not in review["context"]
    by_reply = {candidate["speech_text"]: candidate for candidate in review["candidates"]}
    markdown = (output / "review.md").read_text()
    for run in (a, b):
        row = json.loads((run / "results.jsonl").read_text())
        candidate = by_reply[row["turn"]["speech_text"]]
        assert candidate["observed_input"] == {key: row[key] for key in ("prompt", "system_prompt")}
        label = candidate["item_id"].split(":")[1]
        section = markdown.split(f"### {label}\n", 1)[1].split("### ", 1)[0]
        assert row["prompt"] in section and row["system_prompt"] in section
        assert run.name not in markdown


@pytest.mark.parametrize("name", ["mapping.private.json", "review.json", "review.md"])
def test_changed_review_content_invalidates_scores(tmp_path, name):
    run = make_run(tmp_path / "run")
    output = tmp_path / "packet"
    packet([run], output, 41)
    content = output / name
    content.write_text(content.read_text() + "\nchanged")
    with pytest.raises(ValueError, match="Review packet content changed"):
        summarize(output, output / "ratings.jsonl")


def test_legacy_scores_require_regeneration_and_review(tmp_path):
    run = make_run(tmp_path / "run")
    output = tmp_path / "packet"
    packet([run], output, 41)
    rating = json.loads((output / "ratings.jsonl").read_text())
    del rating["packet_sha256"]
    (output / "ratings.jsonl").write_text(json.dumps(rating) + "\n")
    with pytest.raises(ValueError, match="packet_sha256"):
        summarize(output, output / "ratings.jsonl")
    (output / "manifest.json").write_text(json.dumps({"schema_version": 1}))
    with pytest.raises(ValueError, match="Regenerate the review packet"):
        summarize(output, output / "ratings.jsonl")


def test_changed_results_cannot_reuse_existing_reviews(tmp_path):
    run = make_run(tmp_path / "run")
    output = tmp_path / "packet"
    packet([run], output, 41)
    result = run / "results.jsonl"
    row = json.loads(result.read_text())
    row["turn"]["speech_text"] = "换一个回答。"
    result.write_text(json.dumps(row) + "\n")
    with pytest.raises(ValueError, match="Source results changed"):
        summarize(output, output / "ratings.jsonl")


async def test_product_scene_snapshots_are_valid_and_terminal_completion_skips_policy():
    fixture = Path(__file__).parents[1] / "evals/memoir_interview_product_cases.jsonl"
    cases = [EvalCase.model_validate_json(line) for line in fixture.read_text().splitlines()]
    assert len({case.id for case in cases}) == len(cases)
    for case in cases:
        result = await run_case(case, InterviewConfig(), live=False, policy_mode="shadow")
        assert result["status"] == "dry_run", result.get("error")
        if case.id == "product-completion-done":
            assert result["policy_input"] is None
