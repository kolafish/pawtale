from __future__ import annotations

from collections import Counter
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import (
    MemoirAutomationEventType,
    MemoirBookPlanChapterStatus,
    MemoirChapterSourceLinkStatus,
    MemoirSemanticCacheStatus,
)
from app.domain.projects.types import ProjectType
from app.models.memoir import (
    MemoirAutomationEvent,
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirBookQualityAuditSnapshot,
    MemoirChapterCard,
    MemoirChapterSourceLink,
    MemoirSourceCard,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.models.projects import Project
from app.services.memoir.access import load_memoir_project
from app.services.memoir.book_quality_audit_semantic import SEMANTIC_AUDIT_ENGINE
from app.services.memoir.chapter_cards import CHAPTER_CARD_ENGINE, build_chapter_card_context
from app.services.memoir.source_cards import SOURCE_CARD_ENGINE, source_text_for_utterance, source_text_hash


def _utcnow() -> datetime:
    return datetime.utcnow()


def _status_count(counts: Counter[str], status: str) -> int:
    return counts.get(status, 0)


def _string_items(value: object, *, limit: int = 8) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in items:
            items.append(text[:160])
        if len(items) >= limit:
            break
    return items


def _gap_messages(value: object, *, limit: int = 4) -> list[str]:
    if not isinstance(value, list):
        return []
    messages: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        message = str(item.get("message") or "").strip()
        severity = str(item.get("severity") or "").strip()
        if message:
            messages.append(f"{severity or 'medium'}: {message[:160]}")
        if len(messages) >= limit:
            break
    return messages


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


async def _audit_snapshot_state(db: AsyncSession, project_id: str) -> dict[str, object]:
    rows = list(
        (
            await db.execute(
                select(MemoirBookQualityAuditSnapshot)
                .where(MemoirBookQualityAuditSnapshot.project_id == project_id)
                .order_by(
                    MemoirBookQualityAuditSnapshot.engine,
                    MemoirBookQualityAuditSnapshot.updated_at.desc(),
                    MemoirBookQualityAuditSnapshot.id.desc(),
                )
            )
        ).scalars().all()
    )
    by_engine_status: Counter[str] = Counter()
    ready = 0
    failed = 0
    latest_by_engine: dict[str, MemoirBookQualityAuditSnapshot] = {}
    for snapshot in rows:
        by_engine_status[f"{snapshot.engine}:{snapshot.overall_status or 'unknown'}"] += 1
        if snapshot.error_message:
            failed += 1
        else:
            ready += 1
        latest_by_engine.setdefault(snapshot.engine, snapshot)
    latest = [
        {
            "snapshot_id": snapshot.id,
            "engine": snapshot.engine,
            "overall_status": snapshot.overall_status or "",
            "summary_text": snapshot.summary_text or "",
            "error_message": snapshot.error_message or "",
            "updated_at": snapshot.updated_at,
        }
        for snapshot in latest_by_engine.values()
    ]
    return {
        "ready": ready,
        "stale": 0,
        "failed": failed,
        "by_engine_status": dict(sorted(by_engine_status.items())),
        "latest": latest,
    }


async def _latest_audit_snapshots(db: AsyncSession, project_id: str) -> list[MemoirBookQualityAuditSnapshot]:
    rows = list(
        (
            await db.execute(
                select(MemoirBookQualityAuditSnapshot)
                .where(MemoirBookQualityAuditSnapshot.project_id == project_id)
                .order_by(
                    MemoirBookQualityAuditSnapshot.engine,
                    MemoirBookQualityAuditSnapshot.updated_at.desc(),
                    MemoirBookQualityAuditSnapshot.id.desc(),
                )
            )
        ).scalars().all()
    )
    latest_by_engine: dict[str, MemoirBookQualityAuditSnapshot] = {}
    for snapshot in rows:
        if snapshot.error_message:
            continue
        latest_by_engine.setdefault(snapshot.engine, snapshot)
    return list(latest_by_engine.values())


async def _latest_audit_event_ids(db: AsyncSession, project_id: str) -> list[str]:
    rows = (
        await db.execute(
            select(MemoirAutomationEvent.id)
            .where(
                MemoirAutomationEvent.project_id == project_id,
                MemoirAutomationEvent.event_type == MemoirAutomationEventType.AUDIT_COMPLETED.value,
            )
            .order_by(MemoirAutomationEvent.updated_at.desc(), MemoirAutomationEvent.id.desc())
            .limit(5)
        )
    ).scalars().all()
    return list(rows)


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


async def _ready_chapter_card_for_signature(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    input_signature: str,
) -> MemoirChapterCard | None:
    return await db.scalar(
        select(MemoirChapterCard).where(
            MemoirChapterCard.project_id == project_id,
            MemoirChapterCard.chapter_id == chapter_id,
            MemoirChapterCard.engine == CHAPTER_CARD_ENGINE,
            MemoirChapterCard.input_signature == input_signature,
            MemoirChapterCard.status == MemoirSemanticCacheStatus.READY.value,
        )
    )


async def _link_counts(db: AsyncSession, project_id: str) -> tuple[Counter[str], set[tuple[str, str]], set[tuple[str, str]]]:
    rows = (
        await db.execute(
            select(
                MemoirChapterSourceLink.chapter_id,
                MemoirChapterSourceLink.utterance_id,
                MemoirChapterSourceLink.status,
            ).where(MemoirChapterSourceLink.project_id == project_id)
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


def _chapter_title_by_id(chapters: list[MemoirBookPlanChapter]) -> dict[str, str]:
    return {chapter.id: chapter.title for chapter in chapters}


def _append_suggestion(
    suggestions: list[dict[str, object]],
    seen_questions: set[str],
    *,
    source: str,
    question: str,
    reason: str,
    chapter_id: str | None = None,
    chapter_title: str = "",
    priority: str = "medium",
    card_id: str | None = None,
    snapshot_id: str | None = None,
) -> None:
    normalized_question = question.strip()
    if not normalized_question or normalized_question in seen_questions:
        return
    seen_questions.add(normalized_question)
    suggestions.append(
        {
            "source": source,
            "chapter_id": chapter_id,
            "chapter_title": chapter_title,
            "question": normalized_question,
            "reason": reason.strip()[:240],
            "priority": priority,
            "card_id": card_id,
            "snapshot_id": snapshot_id,
        }
    )


def _chapter_card_suggestions(
    cards: list[MemoirChapterCard],
    *,
    chapter_titles: dict[str, str],
) -> list[dict[str, object]]:
    suggestions: list[dict[str, object]] = []
    seen_questions: set[str] = set()
    for card in sorted(cards, key=lambda item: (item.updated_at, item.id), reverse=True):
        card_json = card.card_json if isinstance(card.card_json, dict) else {}
        gaps = _gap_messages(card_json.get("gaps"), limit=2)
        reason = gaps[0] if gaps else str(card_json.get("summary") or "").strip()
        for question in _string_items(card_json.get("next_questions"), limit=4):
            _append_suggestion(
                suggestions,
                seen_questions,
                source="chapter_card",
                chapter_id=card.chapter_id,
                chapter_title=chapter_titles.get(card.chapter_id, ""),
                question=question,
                reason=reason,
                priority="medium",
                card_id=card.id,
            )
            if len(suggestions) >= 8:
                return suggestions
    return suggestions


def _audit_suggestions(
    snapshots: list[MemoirBookQualityAuditSnapshot],
    *,
    chapter_titles: dict[str, str],
    seen_questions: set[str],
) -> list[dict[str, object]]:
    suggestions: list[dict[str, object]] = []
    for snapshot in sorted(
        snapshots,
        key=lambda item: (item.engine == SEMANTIC_AUDIT_ENGINE, item.updated_at, item.id),
        reverse=True,
    ):
        audits = snapshot.chapter_audits_json if isinstance(snapshot.chapter_audits_json, list) else []
        for audit in audits:
            if not isinstance(audit, dict):
                continue
            chapter_id = str(audit.get("chapter_id") or "").strip() or None
            gaps = _gap_messages(audit.get("gaps"), limit=2)
            reason = gaps[0] if gaps else str(audit.get("summary") or snapshot.summary_text or "").strip()
            for question in _string_items(audit.get("next_questions"), limit=4):
                _append_suggestion(
                    suggestions,
                    seen_questions,
                    source="audit_snapshot",
                    chapter_id=chapter_id,
                    chapter_title=chapter_titles.get(chapter_id or "", ""),
                    question=question,
                    reason=reason,
                    priority="high" if snapshot.overall_status in {"needs_updates", "needs_sources"} else "medium",
                    snapshot_id=snapshot.id,
                )
                if len(suggestions) >= 8:
                    return suggestions
    return suggestions


async def inspect_project_semantic_state(db: AsyncSession, project: Project) -> dict[str, object]:
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
    chapters = [
        chapter
        for plan in plans
        for chapter in plan.chapters
        if chapter.status == MemoirBookPlanChapterStatus.ACTIVE.value
    ]
    link_status_counts, _, _ = await _link_counts(db, project.id)
    source_status_counts = await _source_card_status_counts(db, project.id)
    chapter_status_counts = await _chapter_card_status_counts(db, project.id)
    ready_current_chapter_cards: list[MemoirChapterCard] = []
    chapter_cards_missing = 0
    for chapter in chapters:
        try:
            context = await build_chapter_card_context(db, project_id=project.id, chapter_id=chapter.id)
            card = await _ready_chapter_card_for_signature(
                db,
                project_id=project.id,
                chapter_id=chapter.id,
                input_signature=context.input_signature,
            )
            if card:
                ready_current_chapter_cards.append(card)
            else:
                chapter_cards_missing += 1
        except Exception as exc:
            chapter_cards_missing += 1
            failures.append({"chapter_id": chapter.id, "error": str(exc)})

    latest_snapshots = await _latest_audit_snapshots(db, project.id)
    chapter_titles = _chapter_title_by_id(chapters)
    card_suggestions = _chapter_card_suggestions(ready_current_chapter_cards, chapter_titles=chapter_titles)
    seen_questions = {str(item.get("question") or "") for item in card_suggestions}
    audit_suggestions = _audit_suggestions(latest_snapshots, chapter_titles=chapter_titles, seen_questions=seen_questions)
    follow_up_suggestions = (card_suggestions + audit_suggestions)[:8]

    return {
        "project_id": project.id,
        "title": project.title,
        "inspected_at": _utcnow(),
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
            "active_link_count": link_status_counts.get(MemoirChapterSourceLinkStatus.ACTIVE.value, 0),
            "removed_link_count": link_status_counts.get(MemoirChapterSourceLinkStatus.REMOVED.value, 0),
        },
        "chapter_cards": {
            "ready": _status_count(chapter_status_counts, "ready"),
            "stale": _status_count(chapter_status_counts, "stale"),
            "failed": _status_count(chapter_status_counts, "failed"),
            "missing_current_ready": chapter_cards_missing,
        },
        "audit_snapshots": await _audit_snapshot_state(db, project.id),
        "latest_audit_event_ids": await _latest_audit_event_ids(db, project.id),
        "follow_up_suggestions": follow_up_suggestions,
        "failure_count": len(failures),
        "failures": failures,
    }


async def get_project_semantic_state(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
) -> dict[str, object]:
    project = await load_memoir_project(db, project_id, user_id)
    return await inspect_project_semantic_state(db, project)


async def run_semantic_state_inspection(
    db: AsyncSession,
    *,
    project_id: str | None = None,
) -> dict[str, object]:
    projects = await _memoir_projects(db, project_id)
    project_results = [await inspect_project_semantic_state(db, project) for project in projects]
    return {
        "project_id": project_id,
        "scanned_project_count": len(projects),
        "projects": project_results,
    }
