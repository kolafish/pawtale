from __future__ import annotations

from datetime import datetime
from enum import Enum
import hashlib
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import MemoirSemanticCacheStatus
from app.models.memoir import (
    MemoirBookQualityAuditSnapshot,
    MemoirChapterCard,
    MemoirChapterEventClusterSnapshot,
    MemoirSourceCard,
)


def _utcnow() -> datetime:
    return datetime.utcnow()


def _status_value(status: MemoirSemanticCacheStatus | str) -> str:
    if isinstance(status, Enum):
        return str(status.value)
    return status


def semantic_text_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def semantic_json_signature(payload: object) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return semantic_text_hash(raw)


async def upsert_source_card(
    db: AsyncSession,
    *,
    project_id: str,
    utterance_id: str,
    source_text_hash: str,
    engine: str,
    transcript_version_id: str | None = None,
    status: MemoirSemanticCacheStatus | str = MemoirSemanticCacheStatus.READY,
    card_json: dict | None = None,
    error_message: str = "",
) -> MemoirSourceCard:
    existing = await db.scalar(
        select(MemoirSourceCard).where(
            MemoirSourceCard.project_id == project_id,
            MemoirSourceCard.utterance_id == utterance_id,
            MemoirSourceCard.engine == engine,
            MemoirSourceCard.source_text_hash == source_text_hash,
        )
    )
    if existing:
        existing.transcript_version_id = transcript_version_id
        existing.status = _status_value(status)
        existing.card_json = card_json or {}
        existing.error_message = error_message
        existing.updated_at = _utcnow()
        db.add(existing)
        await db.flush()
        return existing

    source_card = MemoirSourceCard(
        project_id=project_id,
        utterance_id=utterance_id,
        transcript_version_id=transcript_version_id,
        source_text_hash=source_text_hash,
        engine=engine,
        status=_status_value(status),
        card_json=card_json or {},
        error_message=error_message,
    )
    db.add(source_card)
    await db.flush()
    return source_card


async def upsert_chapter_card(
    db: AsyncSession,
    *,
    project_id: str,
    plan_id: str,
    chapter_id: str,
    input_signature: str,
    engine: str,
    draft_version_id: str | None = None,
    status: MemoirSemanticCacheStatus | str = MemoirSemanticCacheStatus.READY,
    card_json: dict | None = None,
    error_message: str = "",
) -> MemoirChapterCard:
    existing = await db.scalar(
        select(MemoirChapterCard).where(
            MemoirChapterCard.project_id == project_id,
            MemoirChapterCard.chapter_id == chapter_id,
            MemoirChapterCard.engine == engine,
            MemoirChapterCard.input_signature == input_signature,
        )
    )
    if existing:
        existing.plan_id = plan_id
        existing.draft_version_id = draft_version_id
        existing.status = _status_value(status)
        existing.card_json = card_json or {}
        existing.error_message = error_message
        existing.updated_at = _utcnow()
        db.add(existing)
        await db.flush()
        return existing

    chapter_card = MemoirChapterCard(
        project_id=project_id,
        plan_id=plan_id,
        chapter_id=chapter_id,
        draft_version_id=draft_version_id,
        input_signature=input_signature,
        engine=engine,
        status=_status_value(status),
        card_json=card_json or {},
        error_message=error_message,
    )
    db.add(chapter_card)
    await db.flush()
    return chapter_card


async def upsert_chapter_event_cluster_snapshot(
    db: AsyncSession,
    *,
    project_id: str,
    plan_id: str,
    chapter_id: str,
    engine: str,
    input_signature: str,
    status: MemoirSemanticCacheStatus | str = MemoirSemanticCacheStatus.READY,
    clusters_json: list | None = None,
    source_ids_json: list[str] | None = None,
    source_fingerprints_json: dict | None = None,
    error_message: str = "",
) -> MemoirChapterEventClusterSnapshot:
    existing = await db.scalar(
        select(MemoirChapterEventClusterSnapshot).where(
            MemoirChapterEventClusterSnapshot.project_id == project_id,
            MemoirChapterEventClusterSnapshot.chapter_id == chapter_id,
            MemoirChapterEventClusterSnapshot.engine == engine,
            MemoirChapterEventClusterSnapshot.input_signature == input_signature,
        )
    )
    if existing:
        existing.plan_id = plan_id
        existing.status = _status_value(status)
        existing.clusters_json = clusters_json or []
        existing.source_ids_json = source_ids_json or []
        existing.source_fingerprints_json = source_fingerprints_json or {}
        existing.error_message = error_message
        existing.updated_at = _utcnow()
        db.add(existing)
        await db.flush()
        return existing

    snapshot = MemoirChapterEventClusterSnapshot(
        project_id=project_id,
        plan_id=plan_id,
        chapter_id=chapter_id,
        engine=engine,
        input_signature=input_signature,
        status=_status_value(status),
        clusters_json=clusters_json or [],
        source_ids_json=source_ids_json or [],
        source_fingerprints_json=source_fingerprints_json or {},
        error_message=error_message,
    )
    db.add(snapshot)
    await db.flush()
    return snapshot


async def upsert_book_quality_audit_snapshot(
    db: AsyncSession,
    *,
    project_id: str,
    engine: str,
    input_signature: str,
    overall_status: str,
    summary_text: str = "",
    display_json: dict | None = None,
    primary_action_json: dict | None = None,
    chapter_audits_json: list | None = None,
    source_card_ids_json: list[str] | None = None,
    chapter_card_ids_json: list[str] | None = None,
    error_message: str = "",
) -> MemoirBookQualityAuditSnapshot:
    existing = await db.scalar(
        select(MemoirBookQualityAuditSnapshot).where(
            MemoirBookQualityAuditSnapshot.project_id == project_id,
            MemoirBookQualityAuditSnapshot.engine == engine,
            MemoirBookQualityAuditSnapshot.input_signature == input_signature,
        )
    )
    if existing:
        existing.overall_status = overall_status
        existing.summary_text = summary_text
        existing.display_json = display_json or {}
        existing.primary_action_json = primary_action_json or {}
        existing.chapter_audits_json = chapter_audits_json or []
        existing.source_card_ids_json = source_card_ids_json or []
        existing.chapter_card_ids_json = chapter_card_ids_json or []
        existing.error_message = error_message
        existing.updated_at = _utcnow()
        db.add(existing)
        await db.flush()
        return existing

    snapshot = MemoirBookQualityAuditSnapshot(
        project_id=project_id,
        engine=engine,
        input_signature=input_signature,
        overall_status=overall_status,
        summary_text=summary_text,
        display_json=display_json or {},
        primary_action_json=primary_action_json or {},
        chapter_audits_json=chapter_audits_json or [],
        source_card_ids_json=source_card_ids_json or [],
        chapter_card_ids_json=chapter_card_ids_json or [],
        error_message=error_message,
    )
    db.add(snapshot)
    await db.flush()
    return snapshot
