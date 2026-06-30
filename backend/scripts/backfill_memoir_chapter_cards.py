"""Backfill memoir chapter cards for existing book plan chapters.

Default mode is dry-run and does not call an LLM provider.

Usage:
  PYTHONPATH=backend python backend/scripts/backfill_memoir_chapter_cards.py
  PYTHONPATH=backend python backend/scripts/backfill_memoir_chapter_cards.py --execute --limit 10
  PYTHONPATH=backend python backend/scripts/backfill_memoir_chapter_cards.py --project-id <project_id>
"""

from __future__ import annotations

import argparse
import asyncio
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import async_session_maker, engine, init_db
from app.domain.memoir.types import MemoirSemanticCacheStatus
from app.domain.projects.types import ProjectType
from app.migrations.memoir_semantic import migrate_memoir_semantic_schema
from app.migrations.memoir_voice import migrate_memoir_voice_schema
from app.models.memoir import MemoirBookPlan, MemoirBookPlanChapter, MemoirChapterCard
from app.models.projects import Project
from app.services.ai_types import AITextClient
from app.services.memoir.chapter_cards import (
    CHAPTER_CARD_ENGINE,
    build_chapter_card_context,
    generate_or_reuse_chapter_card,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", help="Only backfill one memoir project.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true", help="Call the provider and write chapter cards.")
    mode.add_argument("--dry-run", action="store_true", help="Report candidates only. This is the default.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of candidate chapters to process.")
    parser.add_argument("--ai-model", help="Optional AI model override.")
    parser.add_argument("--force-regenerate", action="store_true", help="Regenerate even when a ready card exists.")
    return parser.parse_args()


async def _ready_chapter_card_exists(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    input_signature: str,
) -> bool:
    return bool(
        await db.scalar(
            select(MemoirChapterCard.id).where(
                MemoirChapterCard.project_id == project_id,
                MemoirChapterCard.chapter_id == chapter_id,
                MemoirChapterCard.engine == CHAPTER_CARD_ENGINE,
                MemoirChapterCard.input_signature == input_signature,
                MemoirChapterCard.status == MemoirSemanticCacheStatus.READY.value,
            )
        )
    )


async def _candidate_chapters(
    db: AsyncSession,
    *,
    project_id: str | None,
    force_regenerate: bool,
    limit: int | None,
) -> tuple[int, int, list[tuple[str, str, str]]]:
    statement = (
        select(MemoirBookPlanChapter)
        .join(MemoirBookPlan, MemoirBookPlanChapter.plan_id == MemoirBookPlan.id)
        .join(Project, MemoirBookPlanChapter.project_id == Project.id)
        .where(Project.type == ProjectType.MEMOIR.value)
        .options(selectinload(MemoirBookPlanChapter.plan))
        .order_by(Project.created_at, MemoirBookPlanChapter.sort_order, MemoirBookPlanChapter.id)
    )
    if project_id:
        statement = statement.where(Project.id == project_id)
    chapters = list((await db.execute(statement)).scalars().all())
    scanned_count = len(chapters)
    skipped_existing_count = 0
    candidates: list[tuple[str, str, str]] = []
    max_candidates = None if limit is None else max(0, limit)
    for chapter in chapters:
        if max_candidates is not None and len(candidates) >= max_candidates:
            break
        context = await build_chapter_card_context(db, project_id=chapter.project_id, chapter_id=chapter.id)
        if not force_regenerate and await _ready_chapter_card_exists(
            db,
            project_id=chapter.project_id,
            chapter_id=chapter.id,
            input_signature=context.input_signature,
        ):
            skipped_existing_count += 1
            continue
        candidates.append((chapter.project_id, chapter.id, context.input_signature))
    return scanned_count, skipped_existing_count, candidates


async def run_chapter_card_backfill(
    db: AsyncSession,
    *,
    project_id: str | None = None,
    dry_run: bool = True,
    limit: int | None = None,
    ai_model: str | None = None,
    force_regenerate: bool = False,
    ai_client: AITextClient | None = None,
) -> dict[str, object]:
    scanned_count, skipped_existing_count, candidates = await _candidate_chapters(
        db,
        project_id=project_id,
        force_regenerate=force_regenerate,
        limit=limit,
    )
    failures: list[dict[str, str]] = []
    ready_count = 0
    failed_count = 0
    if not dry_run:
        for current_project_id, chapter_id, _input_signature in candidates:
            try:
                card = await generate_or_reuse_chapter_card(
                    db,
                    project_id=current_project_id,
                    chapter_id=chapter_id,
                    ai_model=ai_model,
                    ai_client=ai_client,
                    force_regenerate=force_regenerate,
                )
                if card.status == MemoirSemanticCacheStatus.READY.value:
                    ready_count += 1
                else:
                    failed_count += 1
                    failures.append({
                        "project_id": current_project_id,
                        "chapter_id": chapter_id,
                        "error": card.error_message,
                    })
                await db.commit()
            except Exception as exc:
                await db.rollback()
                failed_count += 1
                failures.append({"project_id": current_project_id, "chapter_id": chapter_id, "error": str(exc)})
    else:
        await db.rollback()

    return {
        "dry_run": dry_run,
        "project_id": project_id,
        "scanned_chapter_count": scanned_count,
        "candidate_count": len(candidates),
        "skipped_existing_count": skipped_existing_count,
        "ready_count": ready_count,
        "failed_count": failed_count,
        "failure_count": len(failures),
        "failures": failures,
    }


async def _main() -> None:
    args = _parse_args()
    try:
        await init_db()
        await migrate_memoir_voice_schema(engine)
        await migrate_memoir_semantic_schema(engine)
        async with async_session_maker() as db:
            result = await run_chapter_card_backfill(
                db,
                project_id=args.project_id,
                dry_run=not args.execute,
                limit=args.limit,
                ai_model=args.ai_model,
                force_regenerate=args.force_regenerate,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
