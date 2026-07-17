"""Safely rebuild one memoir project's AI-derived narrative state.

Examples:
  PYTHONPATH=. python scripts/rebuild_memoir_narrative_state.py preview \
    --project-id <project-id>
  PYTHONPATH=. python scripts/rebuild_memoir_narrative_state.py start \
    --project-id <project-id> --confirm-project-title <exact-title>
  PYTHONPATH=. python scripts/rebuild_memoir_narrative_state.py status \
    --project-id <project-id> --run-id <run-id>
  PYTHONPATH=. python scripts/rebuild_memoir_narrative_state.py finalize \
    --project-id <project-id> --run-id <run-id>
"""

from __future__ import annotations

import argparse
import asyncio
import json

from app.database import async_session_maker, engine
from app.services.memoir.narrative.project_rebuild import (
    NarrativeProjectRebuildError,
    finalize_project_narrative_rebuild,
    get_project_narrative_rebuild_status,
    preview_project_narrative_rebuild,
    start_project_narrative_rebuild,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    preview = commands.add_parser("preview")
    preview.add_argument("--project-id", required=True)

    start = commands.add_parser("start")
    start.add_argument("--project-id", required=True)
    start.add_argument("--confirm-project-title", required=True)
    start.add_argument("--close-active-sessions", action="store_true")

    status = commands.add_parser("status")
    status.add_argument("--project-id", required=True)
    status.add_argument("--run-id", required=True)

    finalize = commands.add_parser("finalize")
    finalize.add_argument("--project-id", required=True)
    finalize.add_argument("--run-id", required=True)
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> dict:
    async with async_session_maker() as db:
        if args.command == "preview":
            result = await preview_project_narrative_rebuild(
                db,
                project_id=args.project_id,
            )
            return result.as_dict()
        if args.command == "start":
            result = await start_project_narrative_rebuild(
                db,
                project_id=args.project_id,
                confirm_project_title=args.confirm_project_title,
                close_active_sessions=args.close_active_sessions,
            )
            await db.commit()
            return result.as_dict()
        if args.command == "status":
            result = await get_project_narrative_rebuild_status(
                db,
                project_id=args.project_id,
                run_id=args.run_id,
            )
            return result.as_dict()
        job_ids = await finalize_project_narrative_rebuild(
            db,
            project_id=args.project_id,
            run_id=args.run_id,
        )
        await db.commit()
        return {
            "project_id": args.project_id,
            "run_id": args.run_id,
            "completion_refresh_job_ids": job_ids,
        }


async def _main() -> None:
    args = _parse_args()
    try:
        result = await _run(args)
    except NarrativeProjectRebuildError as exc:
        print(
            json.dumps(
                {"ok": False, "error": str(exc)},
                ensure_ascii=False,
                indent=2,
            )
        )
        raise SystemExit(2) from exc
    finally:
        await engine.dispose()
    print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(_main())
