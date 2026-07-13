#!/usr/bin/env python3
"""Export a structured chapter candidate and its deterministic quality artifact."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.database import async_session_maker
from app.services.memoir.narrative.quality_artifact import (
    write_private_json,
    write_private_text,
)
from app.services.memoir.narrative.quality_measurement import (
    measure_narrative_candidate,
)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--chapter-id", required=True)
    parser.add_argument("--baseline-draft-version-id", required=True)
    parser.add_argument("--candidate-draft-version-id", required=True)
    parser.add_argument("--content-output", type=Path, required=True)
    parser.add_argument("--artifact-output", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


async def _measure(args: argparse.Namespace) -> tuple[str, dict[str, object]]:
    async with async_session_maker() as db:
        return await measure_narrative_candidate(
            db,
            project_id=args.project_id,
            chapter_id=args.chapter_id,
            baseline_draft_version_id=args.baseline_draft_version_id,
            candidate_draft_version_id=args.candidate_draft_version_id,
        )


def main() -> int:
    args = _args()
    content_path = args.content_output.expanduser().resolve()
    artifact_path = args.artifact_output.expanduser().resolve()
    if content_path == artifact_path:
        raise ValueError("content and artifact outputs must be different files")
    if not args.overwrite:
        for output in (content_path, artifact_path):
            if output.exists():
                raise FileExistsError(f"refusing to overwrite existing file: {output}")
    content, artifact = asyncio.run(_measure(args))
    write_private_text(content_path, content, overwrite=args.overwrite)
    write_private_json(artifact_path, artifact, overwrite=args.overwrite)
    print(artifact_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
