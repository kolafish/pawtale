"""Opt-in paid model evaluation of synthetic interview-to-chapter role boundaries."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import time

from scripts.memoir_eval_io import select_cases, write_json

from app.domain.memoir.types import MemoirTranscriptPolishLevel
from app.services.ai_factory import get_ai_service
from app.services.memoir.chapter_generation import (
    ChapterGenerationContext, ChapterMaterialInput, ChapterSectionInput,
    generate_chapter_section,
)


async def evaluate(args: argparse.Namespace) -> int:
    fixture = Path(__file__).resolve().parents[1] / "evals/memoir_interview_source_boundary.json"
    cases = json.loads(fixture.read_text())["cases"]
    cases = select_cases(cases, args.case)
    client = get_ai_service(args.ai_model)
    results = []
    levels = args.polish_level or ["low", "medium", "high"]
    for case in cases:
        material = ChapterMaterialInput(
            material_id="synthetic-material", section_id="synthetic-section",
            section_title="往事", section_type="event", section_version=1,
            section_order=0, material_order=0, transcript_version_id="synthetic-version",
            text=case["source"], interviewer_line=case["interviewer"],
        )
        section = ChapterSectionInput(
            section_id=material.section_id, title=material.section_title,
            section_type=material.section_type, section_version=1, sort_order=0,
            materials=(material,),
        )
        for level in levels:
            context = ChapterGenerationContext(
                project_id="synthetic-project", chapter_id="synthetic-chapter",
                chapter_title="我的往事", sections=(section,), materials=(material,),
                input_signature=case["id"], polish_level=MemoirTranscriptPolishLevel(level),
            )
            for repeat in range(args.repeat):
                started = time.monotonic()
                generated = await generate_chapter_section(
                    context, section=section, editorial_guidance=[],
                    chapter_sections=context.sections, ai_model=args.ai_model, ai_client=client,
                )
                failures = [
                    f"missing one of: {group}" for group in case["required"]
                    if not any(fragment in generated.content for fragment in group)
                ] + [
                    f"unexpected: {fragment}" for fragment in case["forbidden"]
                    if fragment in generated.content
                ]
                row = {
                    "case": case["id"], "polish_level": level, "repeat": repeat,
                    "source": case["source"], "interviewer": case["interviewer"],
                    "output": generated.content, "model": generated.model,
                    "seconds": round(time.monotonic() - started, 3), "failures": failures,
                }
                results.append(row)
                write_json(args.output, results)
                print(json.dumps(row, ensure_ascii=False), flush=True)
    failed = sum(bool(row["failures"]) for row in results)
    print(f"Fragment checks: {len(results) - failed}/{len(results)}; manually review every output.")
    return int(failed > 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ai-model", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", action="append")
    parser.add_argument("--repeat", type=int, choices=range(1, 6), default=1)
    parser.add_argument("--polish-level", action="append", choices=["low", "medium", "high"])
    return asyncio.run(evaluate(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
