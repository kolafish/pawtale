"""Backfill memoir source cards for existing voice utterances.

Default mode is dry-run and does not call an LLM provider.

Usage:
  PYTHONPATH=backend python backend/scripts/backfill_memoir_source_cards.py
  PYTHONPATH=backend python backend/scripts/backfill_memoir_source_cards.py --execute --limit 20
  PYTHONPATH=backend python backend/scripts/backfill_memoir_source_cards.py --project-id <project_id>
"""

from __future__ import annotations

import argparse
import asyncio
import json

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_maker, engine, init_db
from app.domain.memoir.types import MemoirSemanticCacheStatus
from app.domain.projects.types import ProjectType
from app.migrations.memoir_semantic import migrate_memoir_semantic_schema
from app.migrations.memoir_voice import migrate_memoir_voice_schema
from app.models.memoir import MemoirSourceCard, MemoirVoiceSession, MemoirVoiceUtterance
from app.models.projects import Project
from app.services.ai_types import AITextClient
from app.services.memoir.source_cards import (
    SOURCE_CARD_ENGINE,
    generate_or_reuse_source_card,
    source_text_for_utterance,
    source_text_hash,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", help="Only backfill one memoir project.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true", help="Call the provider and write source cards.")
    mode.add_argument("--dry-run", action="store_true", help="Report candidates only. This is the default.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of candidate utterances to process.")
    parser.add_argument("--ai-model", help="Optional AI model override.")
    parser.add_argument("--force-regenerate", action="store_true", help="Regenerate even when a ready card exists.")
    return parser.parse_args()


async def _source_card_exists(
    db: AsyncSession,
    *,
    project_id: str,
    utterance_id: str,
    source_hash: str,
) -> bool:
    return bool(
        await db.scalar(
            select(MemoirSourceCard.id).where(
                MemoirSourceCard.project_id == project_id,
                MemoirSourceCard.utterance_id == utterance_id,
                MemoirSourceCard.engine == SOURCE_CARD_ENGINE,
                MemoirSourceCard.source_text_hash == source_hash,
                MemoirSourceCard.status == MemoirSemanticCacheStatus.READY.value,
            )
        )
    )


async def _candidate_utterances(
    db: AsyncSession,
    *,
    project_id: str | None,
    force_regenerate: bool,
    limit: int | None,
) -> tuple[int, int, list[tuple[str, str]]]:
    statement = (
        select(MemoirVoiceUtterance, MemoirVoiceSession.project_id)
        .join(MemoirVoiceSession, MemoirVoiceUtterance.voice_session_id == MemoirVoiceSession.id)
        .join(Project, MemoirVoiceSession.project_id == Project.id)
        .where(
            Project.type == ProjectType.MEMOIR.value,
            or_(
                MemoirVoiceUtterance.transcript_raw != "",
                MemoirVoiceUtterance.transcript_edited != "",
            ),
        )
        .order_by(MemoirVoiceSession.created_at, MemoirVoiceUtterance.created_at, MemoirVoiceUtterance.id)
    )
    if project_id:
        statement = statement.where(Project.id == project_id)
    rows = (await db.execute(statement)).all()
    scanned_count = len(rows)
    skipped_existing_count = 0
    candidates: list[tuple[str, str]] = []
    max_candidates = None if limit is None else max(0, limit)
    for utterance, current_project_id in rows:
        if max_candidates is not None and len(candidates) >= max_candidates:
            break
        source_text = source_text_for_utterance(utterance)
        if not source_text:
            continue
        current_hash = source_text_hash(source_text)
        if not force_regenerate and await _source_card_exists(
            db,
            project_id=current_project_id,
            utterance_id=utterance.id,
            source_hash=current_hash,
        ):
            skipped_existing_count += 1
            continue
        candidates.append((current_project_id, utterance.id))
    return scanned_count, skipped_existing_count, candidates


async def run_source_card_backfill(
    db: AsyncSession,
    *,
    project_id: str | None = None,
    dry_run: bool = True,
    limit: int | None = None,
    ai_model: str | None = None,
    force_regenerate: bool = False,
    ai_client: AITextClient | None = None,
) -> dict[str, object]:
    scanned_count, skipped_existing_count, candidates = await _candidate_utterances(
        db,
        project_id=project_id,
        force_regenerate=force_regenerate,
        limit=limit,
    )
    failures: list[dict[str, str]] = []
    ready_count = 0
    failed_count = 0
    if not dry_run:
        for current_project_id, utterance_id in candidates:
            try:
                card = await generate_or_reuse_source_card(
                    db,
                    project_id=current_project_id,
                    utterance_id=utterance_id,
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
                        "utterance_id": utterance_id,
                        "error": card.error_message,
                    })
                await db.commit()
            except Exception as exc:
                await db.rollback()
                failed_count += 1
                failures.append({"project_id": current_project_id, "utterance_id": utterance_id, "error": str(exc)})
    else:
        await db.rollback()

    return {
        "dry_run": dry_run,
        "project_id": project_id,
        "scanned_utterance_count": scanned_count,
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
            result = await run_source_card_backfill(
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
