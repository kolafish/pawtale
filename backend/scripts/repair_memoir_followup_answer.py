"""Repair one explicitly identified context-only memoir follow-up answer.

The command is dry-run by default. Every affected object must be supplied by ID,
and the current text and state must match before any write is allowed.
"""

from __future__ import annotations

import argparse
import asyncio
import json

from app.database import async_session_maker, engine
from app.domain.memoir.narrative_types import MemoirFollowupAnswerKind
from app.services.memoir.followup_answer_repair import (
    ContextOnlyFollowupAnswerRepairSpec,
    apply_context_only_followup_answer_repair,
    inspect_context_only_followup_answer_repair,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--utterance-id", required=True)
    parser.add_argument("--chapter-id", required=True)
    parser.add_argument("--source-link-id", required=True)
    parser.add_argument("--source-card-id", required=True)
    parser.add_argument("--source-assignment-event-id", required=True)
    parser.add_argument("--expected-source-text", required=True)
    parser.add_argument(
        "--answer-kind",
        required=True,
        choices=[
            MemoirFollowupAnswerKind.CLARIFICATION.value,
            MemoirFollowupAnswerKind.FACT_RESOLUTION.value,
        ],
    )
    parser.add_argument("--expected-interpretation-status", required=True)
    parser.add_argument("--expected-task-status", required=True)
    parser.add_argument("--expected-gap-status", required=True)
    parser.add_argument("--derived-story-id", action="append", required=True)
    parser.add_argument("--derived-claim-id", action="append", required=True)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def _spec(args: argparse.Namespace) -> ContextOnlyFollowupAnswerRepairSpec:
    return ContextOnlyFollowupAnswerRepairSpec(
        project_id=args.project_id,
        task_id=args.task_id,
        utterance_id=args.utterance_id,
        chapter_id=args.chapter_id,
        source_link_id=args.source_link_id,
        source_card_id=args.source_card_id,
        source_assignment_event_id=args.source_assignment_event_id,
        expected_source_text=args.expected_source_text,
        answer_kind=MemoirFollowupAnswerKind(args.answer_kind),
        expected_interpretation_status=args.expected_interpretation_status,
        expected_task_status=args.expected_task_status,
        expected_gap_status=args.expected_gap_status,
        derived_story_ids=tuple(args.derived_story_id),
        derived_claim_ids=tuple(args.derived_claim_id),
    )


async def _main() -> None:
    args = _parse_args()
    try:
        async with async_session_maker() as db:
            if args.apply:
                result = await apply_context_only_followup_answer_repair(db, spec=_spec(args))
                await db.commit()
                mode = "applied"
            else:
                result = await inspect_context_only_followup_answer_repair(db, spec=_spec(args))
                await db.rollback()
                mode = "dry_run"
            print(json.dumps({"mode": mode, **result.as_dict()}, ensure_ascii=False, indent=2))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
