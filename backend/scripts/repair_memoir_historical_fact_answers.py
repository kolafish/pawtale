"""Audit or repair historical fact answers that closed without semantic decisions.

The command audits all projects by default. A repair is always dry-run unless
``--apply`` is supplied, and every target plus the expected answer hash must be
provided explicitly. The command never calls an AI provider.
"""

from __future__ import annotations

import argparse
import asyncio
import json

from app.database import async_session_maker, engine
from app.schemas.memoir_narrative import MemoirFactResolutionPlan
from app.services.memoir.fact_answer_repair import (
    HistoricalFactAnswerRepairSpec,
    apply_historical_fact_answer_repair,
    audit_historical_fact_answers,
    inspect_historical_fact_answer_repair,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id")
    parser.add_argument("--relation-id")
    parser.add_argument("--task-id")
    parser.add_argument("--utterance-id")
    parser.add_argument("--expected-chapter-id")
    parser.add_argument("--expected-gap-id")
    parser.add_argument("--expected-fact-text-sha256")
    parser.add_argument("--expected-interpretation-status")
    parser.add_argument("--expected-task-status")
    parser.add_argument("--expected-gap-status")
    parser.add_argument("--operations-json")
    parser.add_argument("--resolution-reason")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.relation_id:
        required = {
            "--project-id": args.project_id,
            "--task-id": args.task_id,
            "--utterance-id": args.utterance_id,
            "--expected-chapter-id": args.expected_chapter_id,
            "--expected-gap-id": args.expected_gap_id,
            "--expected-fact-text-sha256": args.expected_fact_text_sha256,
            "--expected-interpretation-status": args.expected_interpretation_status,
            "--expected-task-status": args.expected_task_status,
            "--expected-gap-status": args.expected_gap_status,
            "--operations-json": args.operations_json,
            "--resolution-reason": args.resolution_reason,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            parser.error("repair mode requires " + ", ".join(missing))
    elif args.apply:
        parser.error("--apply requires an explicit --relation-id repair specification")
    return args


def _spec(args: argparse.Namespace) -> HistoricalFactAnswerRepairSpec:
    operations = json.loads(args.operations_json)
    if not isinstance(operations, list):
        raise ValueError("--operations-json must contain a JSON array")
    plan = MemoirFactResolutionPlan.model_validate(
        {
            "status": "resolved",
            "operations": operations,
            "confirmation_question": "",
            "reason": args.resolution_reason,
        }
    )
    return HistoricalFactAnswerRepairSpec(
        project_id=args.project_id,
        relation_id=args.relation_id,
        task_id=args.task_id,
        utterance_id=args.utterance_id,
        expected_chapter_id=args.expected_chapter_id,
        expected_gap_id=args.expected_gap_id,
        expected_fact_text_sha256=args.expected_fact_text_sha256,
        expected_interpretation_status=args.expected_interpretation_status,
        expected_task_status=args.expected_task_status,
        expected_gap_status=args.expected_gap_status,
        resolution_plan=plan,
    )


async def _main() -> None:
    args = _parse_args()
    try:
        async with async_session_maker() as db:
            if not args.relation_id:
                candidates = await audit_historical_fact_answers(
                    db,
                    project_id=args.project_id,
                )
                print(
                    json.dumps(
                        {
                            "mode": "audit",
                            "candidate_count": len(candidates),
                            "candidates": [candidate.as_dict() for candidate in candidates],
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return
            spec = _spec(args)
            if args.apply:
                result = await apply_historical_fact_answer_repair(db, spec=spec)
                await db.commit()
                mode = "applied"
            else:
                result = await inspect_historical_fact_answer_repair(db, spec=spec)
                await db.rollback()
                mode = "dry_run"
            print(json.dumps({"mode": mode, **result.as_dict()}, ensure_ascii=False, indent=2))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
