"""Opt-in live evaluation using synthetic speech; invokes the selected paid provider."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import time

from scripts.memoir_eval_io import select_cases, write_json

from app.domain.memoir.types import MemoirTranscriptPolishLevel
from app.services.memoir.voice_transcripts import (
    MemoirTranscriptCleanupContext,
    generate_cleaned_transcript_text,
)


async def evaluate(args: argparse.Namespace) -> int:
    fixture = Path(__file__).resolve().parents[1] / "evals/memoir_self_correction.json"
    cases = json.loads(fixture.read_text())["cases"]
    cases = select_cases(cases, args.case)
    results = []
    for case in cases:
        started = time.monotonic()
        context = MemoirTranscriptCleanupContext(
            recent_user_utterances=tuple(
                case.get("context", {}).get("recent_user_utterances", [])
            ),
        )
        result = await generate_cleaned_transcript_text(
            case["source"], ai_model=args.ai_model, context=context, strict=True,
            transcript_polish_level=MemoirTranscriptPolishLevel(args.polish_level),
        )
        failures = [
            f"missing one of: {group}"
            for group in case["required"]
            if not any(fragment in result.content for fragment in group)
        ] + [
            f"unexpected: {fragment}"
            for fragment in case["forbidden"]
            if fragment in result.content
        ]
        row = {
            "id": case["id"],
            "source": case["source"],
            "polish_level": args.polish_level,
            "output": result.content,
            "requested_model": result.requested_model,
            "resolved_model": result.resolved_model,
            "response_model": result.response_model,
            "provider": result.provider,
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "failures": failures,
        }
        results.append(row)
        write_json(args.output, results)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    failed = sum(bool(row["failures"]) for row in results)
    print(f"Fragment checks: {len(results) - failed}/{len(results)} passed; manually review all outputs.")
    return int(failed > 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ai-model", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", action="append")
    parser.add_argument("--polish-level", choices=["low", "medium", "high"], default="low")
    return asyncio.run(evaluate(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
