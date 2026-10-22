"""Build anonymous interview review packets and summarize explicitly attributed ratings."""

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import random
from statistics import mean, median
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scripts.eval_memoir_interview import EvalCase, git_output, load_dataset, read_jsonl
from scripts.memoir_eval_io import write_json


class Scores(BaseModel):
    model_config = ConfigDict(extra="forbid")
    continuity: int = Field(ge=0, le=2, strict=True)
    value: int = Field(ge=0, le=2, strict=True)
    grounding: int = Field(ge=0, le=2, strict=True)
    boundaries: int = Field(ge=0, le=2, strict=True)
    non_repetition: int = Field(ge=0, le=2, strict=True)


class Rating(BaseModel):
    model_config = ConfigDict(extra="forbid")
    packet_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    item_id: str
    reviewer_kind: Literal["pending", "human", "agent", "model"] = "pending"
    reviewer: str = ""
    input_checked: bool = False
    scores: Scores | None = None
    severe_failures: list[Literal[
        "invented_fact", "ignored_correction", "pushed_refusal", "scene_violation", "system_failure",
    ]] = Field(default_factory=list)
    rationale: str = ""

    @model_validator(mode="after")
    def require_attribution(self):
        if (self.scores is not None or self.severe_failures) and (
            self.reviewer_kind == "pending" or not self.reviewer.strip() or not self.rationale.strip()
        ):
            raise ValueError("A scored rating requires reviewer kind/name and rationale")
        return self


def load_run(path: Path):
    rows = read_jsonl(path / "results.jsonl")
    indexed = {(row["case"]["id"], row["repeat"]): row for row in rows}
    if len(rows) != len(indexed):
        raise ValueError("Duplicate case/repeat results")
    manifest = json.loads((path / "manifest.json").read_text())
    expected = {(identifier, n) for identifier in manifest["case_ids"] for n in range(manifest["repeat"])}
    if set(indexed) != expected:
        raise ValueError("Incomplete run; retain all expected attempts before comparison")
    return indexed


def packet_fingerprint(manifest: dict) -> str:
    content = {key: value for key, value in manifest.items() if key != "packet_sha256"}
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def packet(runs: list[Path], output: Path, seed: int):
    if len(runs) not in {1, 2}:
        raise ValueError("Review one baseline or compare two runs")
    indexed = [load_run(path) for path in runs]
    if not indexed[0]:
        raise ValueError("Cannot review an empty run")
    keys = set(indexed[0])
    if any(set(other) != keys for other in indexed):
        raise ValueError("Comparison runs must contain the same cases and repeats")
    output.mkdir(parents=True, exist_ok=False)
    rng = random.Random(seed)
    mapping = {}
    lines = [
        "# 访谈回应匿名评审", "",
        "每项 0–2 分：承接、价值、不预设、尊重边界、不重复。允许适时倾听；机械鼓励不自动得满分。",
        "只根据实际可见上下文评分，不使用隐藏后续推测信息收益。严重失败单列，并写依据。",
        "每个候选分别展示其实际 system_prompt 与 prompt；不要把另一候选的任务或事实当作本候选输入。",
        "在 ratings.jsonl 填分，明确 human/agent/model；未审核保持 pending。input_checked 表示核对过来源与输入。", "",
    ]
    review_cases = []
    for index, key in enumerate(sorted(keys)):
        case = EvalCase.model_validate(indexed[0][key]["case"])
        if any(other[key]["case"] != indexed[0][key]["case"] for other in indexed[1:]):
            raise ValueError(f"Input snapshot mismatch: {key}")
        lines.extend([f"## {index + 1}. {case.id} / repeat {key[1]}", "", f"场景：{case.scenario}；来源：{case.track}", ""])
        context = {
            "input_mode": case.mode,
            "source_url": case.source_url,
        }
        lines.extend(["```json", json.dumps(context, ensure_ascii=False, indent=2), "```", ""])
        order = list(range(len(runs)))
        rng.shuffle(order)
        candidates = []
        for label_index, run_index in enumerate(order):
            label = chr(ord("A") + label_index)
            row = indexed[run_index][key]
            item_id = f"{index + 1}:{label}"
            mapping[item_id] = {
                "run": str(runs[run_index].resolve()), "case_id": case.id, "repeat": key[1],
                "track": case.track, "scenario": case.scenario, "split_group": case.split_group,
                "status": row["status"],
            }
            observed_input = {"system_prompt": row.get("system_prompt"), "prompt": row.get("prompt")}
            answer = row.get("turn")
            speech = answer["speech_text"] if answer else "[本次没有完成回应]"
            action = answer["conversation_action"] if answer else "failed"
            lines.extend([
                f"### {label}", "", "实际模型输入（null 表示该次运行未记录）：", "", "```json",
                json.dumps(observed_input, ensure_ascii=False, indent=2), "```", "",
                f"动作：{action}", "", speech, "",
            ])
            candidates.append({
                "item_id": item_id, "observed_input": observed_input, "speech_text": speech, "action": action,
            })
        review_cases.append({"case_id": case.id, "repeat": key[1], "context": context, "candidates": candidates})
    write_json(output / "mapping.private.json", mapping)
    write_json(output / "review.json", review_cases)
    (output / "review.md").write_text("\n".join(lines) + "\n")
    manifest = {
        "schema_version": 2, "seed": seed,
        "content_sha256": {
            name: hashlib.sha256((output / name).read_bytes()).hexdigest()
            for name in ("mapping.private.json", "review.json", "review.md")
        },
        "source_result_sha256": {
            str(path.resolve()): hashlib.sha256((path / "results.jsonl").read_bytes()).hexdigest() for path in runs
        },
    }
    manifest["packet_sha256"] = packet_fingerprint(manifest)
    write_json(output / "manifest.json", manifest)
    (output / "ratings.jsonl").write_text("".join(
        Rating(item_id=item_id, packet_sha256=manifest["packet_sha256"]).model_dump_json() + "\n"
        for item_id in mapping
    ))


def summarize(packet_dir: Path, ratings_file: Path):
    manifest = json.loads((packet_dir / "manifest.json").read_text())
    if manifest.get("schema_version") != 2:
        raise ValueError("Regenerate the review packet and re-review; legacy ratings have no packet fingerprint")
    if packet_fingerprint(manifest) != manifest["packet_sha256"]:
        raise ValueError("Review packet manifest changed after creation")
    for name, expected in manifest["content_sha256"].items():
        if hashlib.sha256((packet_dir / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Review packet content changed after creation: {name}")
    for path, expected in manifest["source_result_sha256"].items():
        if hashlib.sha256((Path(path) / "results.jsonl").read_bytes()).hexdigest() != expected:
            raise ValueError("Source results changed after packet creation")
    mapping = json.loads((packet_dir / "mapping.private.json").read_text())
    ratings = [Rating.model_validate(row) for row in read_jsonl(ratings_file)]
    seen = set()
    grouped = defaultdict(list)
    severe = []
    human_checked = set()
    for rating in ratings:
        if rating.packet_sha256 != manifest["packet_sha256"]:
            raise ValueError(f"Rating belongs to a different review packet: {rating.item_id}")
        if rating.item_id not in mapping:
            raise ValueError(f"Unknown review item: {rating.item_id}")
        identity = (rating.item_id, rating.reviewer_kind, rating.reviewer)
        if identity in seen:
            raise ValueError(f"Duplicate rating: {identity}")
        seen.add(identity)
        item = mapping[rating.item_id]
        if rating.severe_failures:
            severe.append({**item, **rating.model_dump(exclude={"scores"})})
        if rating.scores is None:
            continue
        grouped[(rating.reviewer_kind, item["run"], item["track"], item["scenario"], item["split_group"])].append(
            sum(rating.scores.model_dump().values())
        )
        if rating.reviewer_kind == "human" and rating.input_checked:
            human_checked.add(rating.item_id)
    group_rows = [
        dict(zip(("reviewer_kind", "run", "track", "scenario", "split_group"), key),
             ratings=len(values), mean_total=round(mean(values), 3)) for key, values in sorted(grouped.items())
    ]
    # Every source/person group contributes once, rather than treating sibling
    # windows or synthetic variants as independent people.
    track_scores = defaultdict(list)
    for row in group_rows:
        track_scores[(row["reviewer_kind"], row["run"], row["track"], row["scenario"])].append(row["mean_total"])
    aggregates = [dict(zip(("reviewer_kind", "run", "track", "scenario"), key),
                       groups=len(values), group_mean_total=round(mean(values), 3))
                  for key, values in sorted(track_scores.items())]
    runtime = []
    for run_path in sorted({item["run"] for item in mapping.values()}):
        rows = list(load_run(Path(run_path)).values())
        first = [row["first_text_seconds"] for row in rows if row["first_text_seconds"] is not None]
        costs = [cost for row in rows for cost in [row.get("reply_estimated_cost"), *[
            call.get("estimated_cost") for call in row["policy_calls"]
        ]]]
        runtime.append({
            "run": run_path, "attempts": len(rows), "failed": sum(row["status"] == "failed" for row in rows),
            "policy_failed": sum(bool(row["policy_error"]) for row in rows),
            "median_first_text_seconds": median(first) if first else None,
            "known_estimated_rmb": round(sum(cost.get("amount_rmb") or 0 for cost in costs if cost), 6),
            "unknown_cost_items": sum(not cost or cost.get("amount_rmb") is None for cost in costs),
        })
        runtime[-1]["unknown_cost_items"] += sum(bool(row["policy_error"]) and not row["policy_calls"] for row in rows)
    return {
        "schema_version": 2, "packet_sha256": manifest["packet_sha256"],
        "review_items": len(mapping), "human_checked_items": len(human_checked),
        "human_review_complete": len(human_checked) == len(mapping),
        "groups": group_rows, "aggregates": aggregates, "severe_failures": severe, "runtime": runtime,
    }


def freeze_inputs(dataset: Path, selection: Path, split: str, product_cases: Path) -> list[EvalCase]:
    selected = json.loads(selection.read_text())
    if hashlib.sha256((dataset / "manifest.json").read_bytes()).hexdigest() != selected["dataset_manifest_sha256"]:
        raise ValueError("Dataset manifest differs from frozen selection")
    if not git_output(dataset, "rev-parse", "HEAD").startswith(selected["dataset_commit"]):
        raise ValueError("Dataset commit differs from frozen selection")
    if git_output(dataset, "diff", "HEAD", "--"):
        raise ValueError("Frozen source dataset has uncommitted changes")
    identifiers = selected[f"{split}_ids"]
    cases = []
    for track in ["observed_interview", "narrative_derived", "controlled_stress"]:
        rows, _ = load_dataset(dataset, split=split, track=track, mode=selected["mode"])
        cases.extend(case for case in rows if case.id in identifiers)
    by_id = {case.id: case for case in cases}
    if set(by_id) != set(identifiers) or len(by_id) != len(identifiers):
        raise ValueError("Frozen selection has missing or duplicate case IDs")
    result = [by_id[identifier] for identifier in identifiers]
    if split == "dev":
        products = {case.id: case for case in [EvalCase.model_validate(row) for row in read_jsonl(product_cases)]}
        result.extend(products[identifier] for identifier in selected["product_case_ids"])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("freeze")
    freeze.add_argument("--dataset", type=Path, required=True)
    freeze.add_argument("--selection", type=Path, default=Path("evals/memoir_interview_baseline.json"))
    freeze.add_argument("--product-cases", type=Path, default=Path("evals/memoir_interview_product_cases.jsonl"))
    freeze.add_argument("--split", choices=["dev", "eval"], required=True)
    freeze.add_argument("--output", type=Path, required=True)
    build = commands.add_parser("packet")
    build.add_argument("--run", action="append", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--seed", type=int, default=41)
    report = commands.add_parser("summarize")
    report.add_argument("--packet", type=Path, required=True)
    report.add_argument("--ratings", type=Path, required=True)
    report.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "freeze":
        cases = freeze_inputs(args.dataset, args.selection, args.split, args.product_cases)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x") as out:
            out.writelines(case.model_dump_json() + "\n" for case in cases)
    elif args.command == "packet":
        packet(args.run, args.output, args.seed)
    else:
        write_json(args.output, summarize(args.packet, args.ratings))


if __name__ == "__main__":
    main()
