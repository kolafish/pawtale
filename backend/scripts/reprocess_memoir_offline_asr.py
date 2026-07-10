"""Reprocess memoir utterances with offline multi-provider ASR.

The command defaults to a read-only candidate report. Pass ``--execute`` to
call providers and persist verified transcripts.
"""

from __future__ import annotations

import argparse
import asyncio
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_maker, engine, init_db
from app.domain.projects.types import ProjectType
from app.migrations.memoir_semantic import migrate_memoir_semantic_schema
from app.migrations.memoir_voice import migrate_memoir_voice_schema
from app.models.projects import Project
from app.services.memoir.offline_asr import DEFAULT_OFFLINE_ASR_PROVIDERS
from app.services.memoir.offline_asr_reprocessing import (
    beijing_to_utc_naive,
    parse_beijing_datetime,
    reprocess_offline_asr,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    identity = parser.add_mutually_exclusive_group(required=True)
    identity.add_argument("--project-id", help="Memoir project id.")
    identity.add_argument("--project-title", help="Exact memoir project title.")
    parser.add_argument(
        "--cutoff-created-at-bj",
        required=True,
        help="Only process utterances created at or before this Beijing time, e.g. 2026-07-08 16:02:59.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Call providers and persist transcript updates. The default is a dry run.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of candidate utterances.")
    parser.add_argument("--ai-model", help="Optional AI model override for transcript verification.")
    parser.add_argument(
        "--providers",
        default=",".join(DEFAULT_OFFLINE_ASR_PROVIDERS),
        help="Comma-separated ASR providers to use: volc,stepfun.",
    )
    parser.add_argument(
        "--force-reprocess",
        action="store_true",
        help="Reprocess utterances even when requested provider versions already exist.",
    )
    return parser.parse_args()


async def _resolve_project_id(
    db: AsyncSession,
    *,
    project_id: str | None,
    project_title: str | None,
) -> str:
    if project_id:
        project = await db.scalar(
            select(Project).where(Project.id == project_id, Project.type == ProjectType.MEMOIR.value)
        )
        if project is None:
            raise ValueError(f"memoir project not found: {project_id}")
        return project.id

    title = (project_title or "").strip()
    projects = (
        await db.execute(
            select(Project).where(Project.title == title, Project.type == ProjectType.MEMOIR.value)
        )
    ).scalars().all()
    if not projects:
        raise ValueError(f"memoir project title not found: {title}")
    if len(projects) > 1:
        ids = ", ".join(project.id for project in projects)
        raise ValueError(f"memoir project title is ambiguous: {title}; ids: {ids}")
    return projects[0].id


async def _main() -> None:
    args = _parse_args()
    cutoff_utc = beijing_to_utc_naive(parse_beijing_datetime(args.cutoff_created_at_bj))
    try:
        await init_db()
        await migrate_memoir_voice_schema(engine)
        await migrate_memoir_semantic_schema(engine)
        async with async_session_maker() as db:
            project_id = await _resolve_project_id(
                db,
                project_id=args.project_id,
                project_title=args.project_title,
            )
            result = await reprocess_offline_asr(
                db,
                project_id=project_id,
                cutoff_created_at_utc=cutoff_utc,
                dry_run=not args.execute,
                limit=args.limit,
                ai_model=args.ai_model,
                providers=args.providers,
                force_reprocess=args.force_reprocess,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
