"""Offline interview runner. Dry-run by default; --live explicitly calls paid models."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import aclosing
from dataclasses import asdict, replace
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import time
from typing import Literal, Mapping  # noqa: F401

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.domain.memoir.interview_policy import InterviewBrief, InterviewActionCandidate  # noqa: F401
from app.domain.memoir.narrative_types import (
    MemoirConversationMomentum, MemoirInterviewAction, MemoirInterviewActionRisk,  # noqa: F401
    MemoirInterviewBriefSourceKind, MemoirInterviewStoryState,  # noqa: F401
    MemoirInterviewPolicyExecutionMode,
)
from app.services.ai_calls import AITextCallResult
from app.services.ai_costs import estimate_ai_cost
from app.services.ai_factory import get_ai_service
from app.services.ai_usage_records import current_ai_usage_context
from app.services.memoir import completion_interviews, narrator_profile, narration_people  # noqa: F401
from app.services.memoir.completion_interviews import CompletionIssuePromptContext  # noqa: F401
from app.services.memoir.interview_agent import (
    STREAM_PROMPT_VERSION, InterviewConfig, InterviewInput, InterviewRun,
    prepare_interview_turn, stream_interview_turn,
)
from app.services.memoir.interview_book_context import InterviewBookContext, LifeTopic  # noqa: F401
from app.services.memoir.narrative import interview_policy, interview_policy_runtime as policy
from app.services.memoir.narrative.interview_topics import FrameworkTopicCandidate
from app.services.memoir.voice_followup_ai import (
    InterviewDialogue, InterviewMessage, InterviewerPersona, ProgressPromptContext,  # noqa: F401
)
from scripts.memoir_eval_io import write_json


# Pydantic 2.5 resolves stdlib dataclass forward annotations in this namespace.
INPUT_ADAPTER = TypeAdapter(InterviewInput)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceTurn(StrictModel):
    speaker_label: str
    role: Literal["interviewer", "interviewee_candidate", "narrator"]
    text: str


class SourceInput(StrictModel):
    business_goal: str
    context: list[SourceTurn]
    turn_goal: str | None = None


class SourceMessage(StrictModel):
    role: Literal["system", "user"]
    content: str


class SourceRow(StrictModel):
    id: str
    messages: list[SourceMessage]


class SourceQuality(BaseModel):
    review_status: str


class SourceProvenance(BaseModel):
    source_url: str
    content_sha256: str


class SourceMetadata(BaseModel):
    id: str
    status: Literal["active"]
    split: Literal["dev", "eval"]
    split_group: str
    track: Literal["observed_interview", "narrative_derived", "controlled_stress"]
    difficulty: tuple[str, ...]
    variant_family: str | None = None
    quality: SourceQuality
    provenance: SourceProvenance


class FrozenPolicy(StrictModel):
    brief: InterviewBrief
    completion_context: str = ""
    topic_candidates: tuple[FrameworkTopicCandidate, ...] = ()
    topic_briefs: dict[str, InterviewBrief] = Field(default_factory=dict)
    topic_offer_authorized: bool = False


class EvalCase(StrictModel):
    schema_version: Literal[1] = 1
    id: str
    split: Literal["dev", "eval"]
    split_group: str
    track: Literal["observed_interview", "narrative_derived", "controlled_stress", "product_scenario"]
    mode: Literal["autonomous", "guided"]
    scenario: str
    difficulty: tuple[str, ...] = ()
    variant_family: str | None = None
    review_status: str = "unreviewed"
    source_url: str = ""
    source_sha256: str = ""
    snapshot: InterviewInput
    policy_context: FrozenPolicy | None = None


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def load_dataset(root: Path, *, split: str, track: str, mode: str) -> tuple[list[EvalCase], list[Path]]:
    source = root / f"{split}.{track}.{mode}.inputs.jsonl"
    metadata_file = root / "cases.jsonl"
    metadata_rows = read_jsonl(metadata_file)
    metadata = {item["id"]: item for item in metadata_rows}
    if len(metadata) != len(metadata_rows):
        raise ValueError("Duplicate dataset metadata IDs")
    result = []
    for raw in read_jsonl(source):
        row = SourceRow.model_validate(raw)
        meta = SourceMetadata.model_validate(metadata[row.id])
        if (meta.split, meta.track) != (split, track):
            raise ValueError(f"Dataset split/track mismatch: {row.id}")
        if [item.role for item in row.messages] != ["system", "user"]:
            raise ValueError(f"Unexpected dataset message envelope: {row.id}")
        payload = SourceInput.model_validate_json(row.messages[1].content)
        if (mode == "guided") != bool(payload.turn_goal):
            raise ValueError(f"Dataset input mode mismatch: {row.id}")
        messages = tuple(
            InterviewMessage(
                role="assistant" if turn.role == "interviewer" else "user",
                content_text=turn.text, turn_index=index, id=f"{row.id}:{index}",
                created_at=datetime(2000, 1, 1),
            ) for index, turn in enumerate(payload.context)
        )
        if not messages or messages[-1].role != "user":
            raise ValueError(f"Dataset must end at narrator input: {row.id}")
        # The dataset's generic system prompt and all judge-only fields stay outside
        # the product prompt. Guided targets remain an explicitly separate mode.
        instruction = f"本轮给定访谈目标：{payload.turn_goal}" if payload.turn_goal else ""
        result.append(EvalCase(
            **meta.model_dump(exclude={"status", "quality", "provenance"}),
            review_status=meta.quality.review_status,
            source_url=meta.provenance.source_url,
            source_sha256=meta.provenance.content_sha256,
            mode=mode, scenario="narration_follow_up",
            snapshot=InterviewInput(InterviewDialogue("open_narration", messages), policy_instruction=instruction),
        ))
    return result, [source, metadata_file, root / "manifest.json"]


def policy_context_for(case: EvalCase, *, shadow: bool = False) -> policy.InterviewPolicyContext:
    messages = sorted(case.snapshot.dialogue.messages, key=lambda item: (item.turn_index, item.created_at, item.id))
    if case.policy_context is not None:
        frozen = case.policy_context
        brief = frozen.brief
    else:
        if case.snapshot.dialogue.session_goal != "open_narration":
            raise ValueError(f"Active completion evaluation requires frozen policy context: {case.id}")
        latest = next((item for item in reversed(messages) if item.role == "user"), None)
        constraints = policy._safety_constraints(latest.content_text if latest else "")
        brief = interview_policy.open_narration_brief(
            current_story_state=policy._story_state(latest, completion_session=False, safety_constraints=constraints),
            conversation_momentum=policy._conversation_momentum(latest, safety_constraints=constraints),
            safety_constraints=constraints,
            policy_version=policy.SHADOW_POLICY_VERSION if shadow else policy.ACTIVE_POLICY_VERSION,
        )
        frozen = FrozenPolicy(brief=brief)
    return policy.InterviewPolicyContext(
        project_id="offline", brief=brief, messages=tuple(messages[-policy.MAX_POLICY_CONTEXT_MESSAGES:]),
        completion_context=frozen.completion_context,
        trigger_message_id=messages[-1].id if messages else None,
        topic_candidates=frozen.topic_candidates, topic_offer_authorized=frozen.topic_offer_authorized,
    )


async def run_case(case: EvalCase, config: InterviewConfig, *, live: bool, policy_mode: str, client_factory=get_ai_service):
    started = time.monotonic()
    run = InterviewRun()
    calls = []
    snapshot = case.snapshot
    selected = None
    first_text = None
    prepared = None
    context = None
    policy_error = None
    reply_seconds = None

    def record_policy(response: AITextCallResult):
        calls.append({"operation": "policy", **response.diagnostics()})

    def result(status, **extra):
        return {
            "schema_version": 1, "case": case.model_dump(mode="json"),
            "config": asdict(config), "prompt_version": STREAM_PROMPT_VERSION,
            "policy_mode": policy_mode,
            "policy_error": policy_error,
            "policy_context_origin": "frozen" if case.policy_context else "visible_dialogue_only",
            "policy_input": asdict(context) if context else None,
            "policy_decision": {
                "action": selected.selected_action.value, "execution_mode": selected.execution_mode.value,
                "applies_to_reply": selected.applies_to_reply,
            } if selected else None,
            "system_prompt": prepared.system_prompt if prepared else None,
            "prompt": prepared.prompt if prepared else None,
            "max_tokens": prepared.max_tokens if prepared else None,
            "status": status, "run": asdict(run), "policy_calls": calls,
            "reply_estimated_cost": estimate_ai_cost(
                provider=run.ai_provider, model=run.response_ai_model or run.resolved_ai_model, usage=run.usage,
            ).as_dict() if run.usage else None,
            "seconds": round(time.monotonic() - started, 4),
            "reply_seconds": reply_seconds,
            "first_text_seconds": round(first_text, 4) if first_text is not None else None,
            **extra,
        }

    async def select_turn():
        nonlocal policy_error

        async def resolve_topic(current, selection):
            if selection.action != MemoirInterviewAction.OFFER_NEW_TOPIC:
                return current
            frozen = case.policy_context
            if frozen is None or selection.framework_item_id not in frozen.topic_briefs:
                raise ValueError("Missing frozen selected-topic brief")
            return replace(current, brief=replace(
                frozen.topic_briefs[selection.framework_item_id],
                candidate_actions=current.brief.candidate_actions,
                candidate_topic_ids=current.brief.candidate_topic_ids,
            ))

        try:
            return await policy.select_policy_turn(
                context=context, ai_client=client_factory(config.requested_model),
                resolve_context=resolve_topic, on_response=record_policy,
            )
        except Exception as exc:
            policy_error = {"type": type(exc).__name__, "message": str(exc)}
            return None

    try:
        completion_answered = bool(snapshot.completion_context and snapshot.completion_context.answered)
        policy_available = (
            snapshot.dialogue.session_goal == "open_narration" or snapshot.completion_context is not None
        )
        if policy_mode != "off" and policy_available and not completion_answered:
            context = policy_context_for(case, shadow=policy_mode == "shadow")
            if live and policy_mode == "active":
                selected = await select_turn()
                snapshot = replace(snapshot, policy_instruction="\n".join(filter(None, (
                    snapshot.policy_instruction, policy.active_reply_instruction(selected),
                ))))
        prepared = prepare_interview_turn(snapshot, config)
        turn = None
        if live:
            async with aclosing(stream_interview_turn(
                prepared, ai_client=client_factory(config.requested_model), run=run, cancel_event=asyncio.Event(),
            )) as stream:
                async for chunk in stream:
                    if chunk.text_delta and first_text is None:
                        first_text = time.monotonic() - started
            turn = run.completed_turn(snapshot)
            reply_seconds = round(time.monotonic() - started, 4)
            if policy_mode == "shadow" and context is not None:
                selected = await select_turn()
                if selected:
                    selected = replace(selected, execution_mode=MemoirInterviewPolicyExecutionMode.SHADOW)
        return result("completed" if live else "dry_run", turn=asdict(turn) if turn else None)
    except Exception as exc:
        # Each attempted case is retained; failed provider calls with unavailable
        # usage remain unknown instead of being reported as zero cost.
        return result("failed", error={"type": type(exc).__name__, "message": str(exc)})


def git_output(path: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


async def evaluate(args: argparse.Namespace) -> int:
    if current_ai_usage_context() is not None:
        raise ValueError("Offline evaluations must not use a billable user context")
    if args.dataset:
        cases, sources = load_dataset(args.dataset, split=args.split, track=args.track, mode=args.mode)
    else:
        cases = [EvalCase.model_validate(row) for row in read_jsonl(args.cases)]
        cases = [case for case in cases if case.split == args.split]
        sources = [args.cases]
    ids = [case.id for case in cases]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate case IDs")
    if args.case:
        if unknown := set(args.case) - set(ids):
            raise ValueError(f"Unknown case IDs: {sorted(unknown)}")
        cases = [case for case in cases if case.id in args.case]
    cases = cases[:args.max_cases]
    if not cases:
        raise ValueError("No evaluation cases selected")
    config = InterviewConfig(requested_model=args.ai_model, temperature=args.temperature)
    repo = Path(__file__).resolve().parents[2]
    tracked_diff = git_output(repo, "diff", "HEAD", "--", "backend")
    manifest = {
        "schema_version": 1, "code_commit": git_output(repo, "rev-parse", "HEAD"),
        "tracked_backend_diff_sha256": hashlib.sha256(tracked_diff.encode()).hexdigest(),
        "tracked_backend_dirty": bool(tracked_diff),
        "source_files": {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
        "config": asdict(config), "policy_mode": args.policy_mode, "live": args.live,
        "case_ids": [case.id for case in cases], "repeat": args.repeat,
    }
    args.output.mkdir(parents=True, exist_ok=False)
    write_json(args.output / "manifest.json", manifest)
    (args.output / "inputs.jsonl").write_text("".join(case.model_dump_json() + "\n" for case in cases))
    failed = 0
    policy_failed = 0
    with (args.output / "results.jsonl").open("x") as out:
        for case in cases:
            for repeat in range(args.repeat):
                row = await run_case(case, config, live=args.live, policy_mode=args.policy_mode)
                row["repeat"] = repeat
                out.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
                out.flush()
                failed += row["status"] == "failed"
                policy_failed += bool(row["policy_error"])
                print(json.dumps({"id": case.id, "repeat": repeat, "status": row["status"], "seconds": row["seconds"]}), flush=True)
    write_json(args.output / "summary.json", {
        "attempts": len(cases) * args.repeat, "failed": failed, "policy_failed": policy_failed,
    })
    return int(failed > 0 or policy_failed > 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--dataset", type=Path)
    source.add_argument("--cases", type=Path, help="Typed product snapshots or a previous run's inputs.jsonl")
    parser.add_argument("--split", choices=["dev", "eval"], default="dev")
    parser.add_argument("--track", choices=["observed_interview", "narrative_derived", "controlled_stress"], default="observed_interview")
    parser.add_argument("--mode", choices=["autonomous", "guided"], default="autonomous")
    parser.add_argument("--policy-mode", choices=["off", "active", "shadow"], required=True)
    parser.add_argument("--ai-model", required=True)
    parser.add_argument("--temperature", type=float, default=0.72)
    parser.add_argument("--case", action="append")
    parser.add_argument("--max-cases", type=int, default=10)
    parser.add_argument("--repeat", type=int, choices=range(1, 6), default=1)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 0 <= args.temperature <= 1.5:
        parser.error("temperature must be between 0 and 1.5")
    if not 1 <= args.max_cases <= 10000:
        parser.error("max-cases must be between 1 and 10000")
    return asyncio.run(evaluate(args))


if __name__ == "__main__":
    raise SystemExit(main())
