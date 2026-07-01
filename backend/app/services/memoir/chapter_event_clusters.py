from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import MemoirSemanticCacheStatus
from app.models.memoir import MemoirBookPlanChapter, MemoirChapterEventClusterSnapshot
from app.services.ai_types import AITextClient
from app.services.memoir.book_draft_ai import ChapterEventCluster, ChapterSourceText, generate_event_clusters
from app.services.memoir.semantic_state import upsert_chapter_event_cluster_snapshot


CHAPTER_EVENT_CLUSTER_ENGINE = "llm_event_cluster_v1"


def _source_text_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _source_card_signature(source: ChapterSourceText) -> dict[str, object]:
    card = source.source_card
    if not card:
        return {"source_text_hash": _source_text_hash(source.text)}
    return {
        "source_card_id": card.source_card_id,
        "summary": card.summary,
        "people": card.people or [],
        "places": card.places or [],
        "time_hints": card.time_hints or [],
        "event_tags": card.event_tags or [],
        "key_facts": card.key_facts or [],
    }


def chapter_event_cluster_input_signature(
    *,
    chapter: MemoirBookPlanChapter,
    sources: list[ChapterSourceText],
    engine: str = CHAPTER_EVENT_CLUSTER_ENGINE,
) -> str:
    payload = {
        "engine": engine,
        "chapter_id": chapter.id,
        "source_ids": [source.ref.utterance_id for source in sources],
        "sources": [
            {
                "utterance_id": source.ref.utterance_id,
                "quote": source.ref.quote or "",
                "change_type": source.change_type,
                "card": _source_card_signature(source),
            }
            for source in sources
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def serialize_event_clusters(clusters: list[ChapterEventCluster]) -> list[dict[str, object]]:
    return [
        {
            "title": cluster.title,
            "source_ids": list(cluster.source_ids),
            "summary": cluster.summary,
        }
        for cluster in clusters
    ]


def _snapshot_event_clusters(
    snapshot: MemoirChapterEventClusterSnapshot,
    *,
    sources: list[ChapterSourceText],
) -> list[ChapterEventCluster]:
    valid_source_ids = {source.ref.utterance_id for source in sources}
    used_source_ids: set[str] = set()
    clusters: list[ChapterEventCluster] = []
    raw_clusters = snapshot.clusters_json if isinstance(snapshot.clusters_json, list) else []
    for item in raw_clusters:
        if not isinstance(item, dict):
            continue
        source_ids = [
            source_id
            for source_id in item.get("source_ids", [])
            if isinstance(source_id, str) and source_id in valid_source_ids and source_id not in used_source_ids
        ]
        if not source_ids:
            continue
        used_source_ids.update(source_ids)
        title = str(item.get("title") or "").strip() or "相关回忆"
        summary = str(item.get("summary") or "").strip()
        clusters.append(ChapterEventCluster(title=title, source_ids=source_ids, summary=summary))
    return clusters if used_source_ids == valid_source_ids else []


async def get_or_build_chapter_event_clusters(
    db: AsyncSession,
    *,
    client: AITextClient,
    chapter: MemoirBookPlanChapter,
    sources: list[ChapterSourceText],
    engine: str = CHAPTER_EVENT_CLUSTER_ENGINE,
) -> list[ChapterEventCluster]:
    input_signature = chapter_event_cluster_input_signature(chapter=chapter, sources=sources, engine=engine)
    snapshot = await db.scalar(
        select(MemoirChapterEventClusterSnapshot)
        .where(
            MemoirChapterEventClusterSnapshot.project_id == chapter.project_id,
            MemoirChapterEventClusterSnapshot.chapter_id == chapter.id,
            MemoirChapterEventClusterSnapshot.engine == engine,
            MemoirChapterEventClusterSnapshot.input_signature == input_signature,
            MemoirChapterEventClusterSnapshot.status == MemoirSemanticCacheStatus.READY.value,
        )
        .order_by(MemoirChapterEventClusterSnapshot.updated_at.desc(), MemoirChapterEventClusterSnapshot.id.desc())
    )
    if snapshot:
        clusters = _snapshot_event_clusters(snapshot, sources=sources)
        if clusters:
            return clusters

    clusters = await generate_event_clusters(client, chapter=chapter, sources=sources)
    await upsert_chapter_event_cluster_snapshot(
        db,
        project_id=chapter.project_id,
        plan_id=chapter.plan_id,
        chapter_id=chapter.id,
        engine=engine,
        input_signature=input_signature,
        clusters_json=serialize_event_clusters(clusters),
        source_ids_json=[source.ref.utterance_id for source in sources],
    )
    return clusters
