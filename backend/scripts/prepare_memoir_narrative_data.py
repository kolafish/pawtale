#!/usr/bin/env python3
"""Prepare additive memoir narrative data without switching the book pipeline."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

from sqlalchemy import func, select

from app.config import get_settings
from app.database import async_session_maker, engine
from app.domain.memoir.types import MemoirEditingJobStatus, MemoirEditingJobType
from app.migrations.runtime import assert_runtime_schema
from app.models.memoir import MemoirEditingJob
from app.services.memoir.editing_job_handlers import register_memoir_editing_job_handlers
from app.services.memoir.editing_jobs import run_due_editing_jobs
from app.services.memoir.narrative.backfill import run_narrative_ingestion_backfill
from app.services.memoir.narrative.chapter_structure_backfill import (
    run_chapter_structure_backfill,
)
from app.services.memoir.narrative.cutover_checks import (
    assert_ingestion_complete,
    assert_story_graph_complete,
    collect_migration_report,
)
from scripts.backup_runtime_data import sqlite_database_path, validate_backup_manifest


EXECUTABLE_STAGES = {"ingest", "process-ingestion", "structures"}


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "stage",
        choices=(
            "inventory",
            "ingest",
            "process-ingestion",
            "structures",
        ),
        nargs="?",
        default="inventory",
    )
    parser.add_argument("--project-id")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--ai-model")
    parser.add_argument(
        "--backup-manifest",
        type=Path,
        help="manifest.json produced by backup_runtime_data.py; required for writes",
    )
    parser.add_argument(
        "--application-writers-stopped",
        action="store_true",
        help="Required with --execute after the API and background workers are stopped.",
    )
    return parser.parse_args()


def _validate_run_args(args: argparse.Namespace) -> None:
    if args.execute and args.stage not in EXECUTABLE_STAGES:
        raise RuntimeError(
            "execute mode is only valid for ingest, process-ingestion, or structures"
        )
    if args.execute and not args.application_writers_stopped:
        raise RuntimeError(
            "execute mode requires explicit confirmation that application writers are stopped"
        )
def _runtime_sqlite_database() -> Path:
    path = sqlite_database_path(get_settings().database_url)
    if path is None:
        raise RuntimeError(
            "memoir narrative preparation requires a file-backed SQLite database"
        )
    return path


def _validate_backup_manifest(
    path: Path | None,
    *,
    runtime_database: Path,
) -> dict[str, object]:
    if path is None:
        raise RuntimeError("--backup-manifest is required for execute mode")
    return validate_backup_manifest(
        path,
        expected_source_database=runtime_database,
    )


async def _ingestion_status_counts(*, project_id: str | None) -> dict[str, int]:
    async with async_session_maker() as db:
        statement = select(
            MemoirEditingJob.status,
            func.count(MemoirEditingJob.id),
        ).where(
            MemoirEditingJob.job_type
            == MemoirEditingJobType.INGEST_NARRATIVE_STATE.value
        )
        if project_id:
            statement = statement.where(MemoirEditingJob.project_id == project_id)
        rows = (
            await db.execute(
                statement.group_by(MemoirEditingJob.status)
            )
        ).all()
    counts = {status.value: 0 for status in MemoirEditingJobStatus}
    counts.update({str(status): int(count) for status, count in rows})
    return counts


async def _process_ingestion_jobs(
    *,
    project_id: str | None,
    execute: bool,
    limit: int | None,
) -> dict[str, object]:
    initial = await _ingestion_status_counts(project_id=project_id)
    if not execute:
        return {
            "dry_run": True,
            "initial_status_counts": initial,
            "processed_count": 0,
            "succeeded_count": 0,
            "final_status_counts": initial,
        }
    register_memoir_editing_job_handlers()
    processed_count = 0
    succeeded_count = 0
    batch_size = max(1, get_settings().memoir_editing_batch_size)
    while limit is None or processed_count < max(0, limit):
        before = await _ingestion_status_counts(project_id=project_id)
        pending_before = before[MemoirEditingJobStatus.PENDING.value]
        if pending_before == 0:
            break
        current_limit = batch_size
        if limit is not None:
            current_limit = min(current_limit, limit - processed_count)
        succeeded_count += await run_due_editing_jobs(
            limit=current_limit,
            job_type=MemoirEditingJobType.INGEST_NARRATIVE_STATE,
            project_id=project_id,
        )
        after = await _ingestion_status_counts(project_id=project_id)
        processed = pending_before - after[MemoirEditingJobStatus.PENDING.value]
        if processed <= 0:
            break
        processed_count += processed
        if processed_count % 25 == 0:
            print(
                f"processed narrative ingestion jobs: {processed_count}",
                file=sys.stderr,
                flush=True,
            )
    return {
        "dry_run": False,
        "initial_status_counts": initial,
        "processed_count": processed_count,
        "succeeded_count": succeeded_count,
        "final_status_counts": await _ingestion_status_counts(project_id=project_id),
    }


async def _run(args: argparse.Namespace) -> int:
    _validate_run_args(args)
    backup = (
        _validate_backup_manifest(
            args.backup_manifest,
            runtime_database=_runtime_sqlite_database(),
        )
        if args.execute
        else None
    )
    await assert_runtime_schema(engine)
    async with async_session_maker() as db:
        if args.stage == "ingest":
            action = await run_narrative_ingestion_backfill(
                db,
                project_id=args.project_id,
                dry_run=not args.execute,
                limit=args.limit,
                ai_model=args.ai_model,
            )
        elif args.stage == "process-ingestion":
            action = await _process_ingestion_jobs(
                project_id=args.project_id,
                execute=args.execute,
                limit=args.limit,
            )
        elif args.stage == "structures":
            await assert_ingestion_complete(db, project_id=args.project_id)
            await assert_story_graph_complete(db, project_id=args.project_id)
            action = await run_chapter_structure_backfill(
                db,
                project_id=args.project_id,
                dry_run=not args.execute,
                limit=args.limit,
            )
        else:
            action = {"dry_run": True, "stage": args.stage}
        report = await collect_migration_report(db, project_id=args.project_id)
    result = {
        "stage": args.stage,
        "executed": bool(args.execute),
        "backup_manifest": str(args.backup_manifest) if backup else None,
        "action": action,
        "migration": report,
        "data_preparation_ready": bool(report["cutover_data_ready"]),
        "rollback": (
            "Backfill is additive. Roll back application code first; use the validated "
            "backup database only if schema/data restoration is required."
        ),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


async def _main() -> int:
    args = _args()
    try:
        return await _run(args)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
