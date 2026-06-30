"""Inspect memoir semantic backfill state without modifying data.

Usage:
  PYTHONPATH=backend python backend/scripts/inspect_memoir_semantic_state.py
  PYTHONPATH=backend python backend/scripts/inspect_memoir_semantic_state.py --project-id <project_id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import async_session_maker, engine, init_db
from app.domain.memoir.types import MemoirChapterSourceLinkStatus, MemoirSemanticCacheStatus
from app.domain.projects.types import ProjectType
from app.migrations.memoir_semantic import migrate_memoir_semantic_schema
from app.migrations.memoir_voice import migrate_memoir_voice_schema
from app.models.memoir import (
    MemoirBookPlan,
    MemoirBookQualityAuditSnapshot,
    MemoirChapterCard,
    MemoirChapterSourceLink,
    MemoirSourceCard,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.models.projects import Project
from app.services.memoir.book_source_links import source_ref_utterance_id
from app.services.memoir.chapter_cards import CHAPTER_CARD_ENGINE, build_chapter_card_context
from app.services.memoir.source_cards import SOURCE_CARD_ENGINE, source_text_for_utterance, source_text_hash


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", help="Only inspect one memoir project.")
    return parser.parse_args()


async def _memoir_projects(db: AsyncSession, project_id: str | None) -> list[Project]:
    statement = select(Project).where(Project.type == ProjectType.MEMOIR.value).order_by(Project.created_at, Project.id)
    if project_id:
        statement = statement.where(Project.id == project_id)
    return list((await db.execute(statement)).scalars().all())


async def _project_utterances(db: AsyncSession, project_id: str) -> list[MemoirVoiceUtterance]:
    return list(
        (
            await db.execute(
                select(MemoirVoiceUtterance)
                .join(MemoirVoiceSession, MemoirVoiceUtterance.voice_session_id == MemoirVoiceSession.id)
                .where(MemoirVoiceSession.project_id == project_id)
                .order_by(MemoirVoiceSession.created_at, MemoirVoiceUtterance.created_at, MemoirVoiceUtterance.id)
            )
        ).scalars().all()
    )


async def _project_plans(db: AsyncSession, project_id: str) -> list[MemoirBookPlan]:
    return list(
        (
            await db.execute(
                select(MemoirBookPlan)
                .where(MemoirBookPlan.project_id == project_id)
                .options(selectinload(MemoirBookPlan.chapters))
                .order_by(MemoirBookPlan.created_at, MemoirBookPlan.id)
            )
        ).scalars().all()
    )


async def _source_card_status_counts(db: AsyncSession, project_id: str) -> Counter[str]:
    rows = (
        await db.execute(
            select(MemoirSourceCard.status).where(
                MemoirSourceCard.project_id == project_id,
                MemoirSourceCard.engine == SOURCE_CARD_ENGINE,
            )
        )
    ).scalars().all()
    return Counter(rows)


async def _chapter_card_status_counts(db: AsyncSession, project_id: str) -> Counter[str]:
    rows = (
        await db.execute(
            select(MemoirChapterCard.status).where(
                MemoirChapterCard.project_id == project_id,
                MemoirChapterCard.engine == CHAPTER_CARD_ENGINE,
            )
        )
    ).scalars().all()
    return Counter(rows)


async def _audit_snapshot_counts(db: AsyncSession, project_id: str) -> dict[str, int]:
    rows = (
        await db.execute(
            select(MemoirBookQualityAuditSnapshot.engine, MemoirBookQualityAuditSnapshot.overall_status).where(
                MemoirBookQualityAuditSnapshot.project_id == project_id,
            )
        )
    ).all()
    counts: Counter[str] = Counter()
    for engine_name, status in rows:
        counts[f"{engine_name}:{status or 'unknown'}"] += 1
    return dict(sorted(counts.items()))


async def _has_ready_source_card(
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


async def _has_ready_chapter_card(
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


async def _link_counts(db: AsyncSession, project_id: str) -> tuple[Counter[str], set[tuple[str, str]], set[tuple[str, str]]]:
    rows = (
        await db.execute(
            select(MemoirChapterSourceLink.chapter_id, MemoirChapterSourceLink.utterance_id, MemoirChapterSourceLink.status)
            .where(MemoirChapterSourceLink.project_id == project_id)
        )
    ).all()
    counts: Counter[str] = Counter()
    active_keys: set[tuple[str, str]] = set()
    all_keys: set[tuple[str, str]] = set()
    for chapter_id, utterance_id, status in rows:
        counts[status] += 1
        key = (chapter_id, utterance_id)
        all_keys.add(key)
        if status == MemoirChapterSourceLinkStatus.ACTIVE.value:
            active_keys.add(key)
    return counts, active_keys, all_keys


async def _inspect_project(db: AsyncSession, project: Project) -> dict[str, object]:
    failures: list[dict[str, str]] = []
    utterances = await _project_utterances(db, project.id)
    nonempty_utterances = [utterance for utterance in utterances if source_text_for_utterance(utterance)]
    source_cards_missing = 0
    for utterance in nonempty_utterances:
        source_hash = source_text_hash(source_text_for_utterance(utterance))
        if not await _has_ready_source_card(
            db,
            project_id=project.id,
            utterance_id=utterance.id,
            source_hash=source_hash,
        ):
            source_cards_missing += 1

    plans = await _project_plans(db, project.id)
    chapters = [chapter for plan in plans for chapter in plan.chapters]
    legacy_keys: set[tuple[str, str]] = set()
    legacy_ref_count = 0
    for chapter in chapters:
        for ref in chapter.source_refs_json or []:
            utterance_id = source_ref_utterance_id(ref)
            if not utterance_id:
                continue
            legacy_ref_count += 1
            legacy_keys.add((chapter.id, utterance_id))

    link_status_counts, active_link_keys, all_link_keys = await _link_counts(db, project.id)
    source_status_counts = await _source_card_status_counts(db, project.id)
    chapter_status_counts = await _chapter_card_status_counts(db, project.id)
    chapter_cards_missing = 0
    for chapter in chapters:
        try:
            context = await build_chapter_card_context(db, project_id=project.id, chapter_id=chapter.id)
            if not await _has_ready_chapter_card(
                db,
                project_id=project.id,
                chapter_id=chapter.id,
                input_signature=context.input_signature,
            ):
                chapter_cards_missing += 1
        except Exception as exc:
            chapter_cards_missing += 1
            failures.append({"chapter_id": chapter.id, "error": str(exc)})

    return {
        "project_id": project.id,
        "title": project.title,
        "voice_utterance_count": len(utterances),
        "nonempty_utterance_count": len(nonempty_utterances),
        "source_cards": {
            "ready": _status_count(source_status_counts, "ready"),
            "stale": _status_count(source_status_counts, "stale"),
            "failed": _status_count(source_status_counts, "failed"),
            "missing_current_ready": source_cards_missing,
        },
        "chapter_sources": {
            "chapter_count": len(chapters),
            "legacy_source_ref_count": legacy_ref_count,
            "active_link_count": link_status_counts.get(MemoirChapterSourceLinkStatus.ACTIVE.value, 0),
            "removed_link_count": link_status_counts.get(MemoirChapterSourceLinkStatus.REMOVED.value, 0),
            "legacy_refs_without_any_link_count": len(legacy_keys - all_link_keys),
            "active_links_without_legacy_ref_count": len(active_link_keys - legacy_keys),
        },
        "chapter_cards": {
            "ready": _status_count(chapter_status_counts, "ready"),
            "stale": _status_count(chapter_status_counts, "stale"),
            "failed": _status_count(chapter_status_counts, "failed"),
            "missing_current_ready": chapter_cards_missing,
        },
        "audit_snapshots": await _audit_snapshot_counts(db, project.id),
        "failure_count": len(failures),
        "failures": failures,
    }


def _status_count(counts: Counter[str], status: str) -> int:
    return counts.get(status, 0)


async def run_semantic_state_inspection(
    db: AsyncSession,
    *,
    project_id: str | None = None,
) -> dict[str, object]:
    projects = await _memoir_projects(db, project_id)
    project_results = [await _inspect_project(db, project) for project in projects]
    return {
        "project_id": project_id,
        "scanned_project_count": len(projects),
        "projects": project_results,
    }


async def _main() -> None:
    args = _parse_args()
    try:
        await init_db()
        await migrate_memoir_voice_schema(engine)
        await migrate_memoir_semantic_schema(engine)
        async with async_session_maker() as db:
            result = await run_semantic_state_inspection(db, project_id=args.project_id)
            print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
