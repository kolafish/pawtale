from __future__ import annotations

from dataclasses import dataclass
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import MemoirSemanticCacheStatus
from app.models.memoir import MemoirBookPlanChapter, MemoirChapterEventClusterSnapshot
from app.services.ai_types import AITextClient
from app.services.json_utils import extract_json_payload
from app.services.memoir.book_draft_ai import ChapterEventCluster, ChapterSourceText, generate_event_clusters
from app.services.memoir.book_plan_sources import text_or_empty
from app.services.memoir.semantic_state import (
    semantic_json_signature,
    semantic_text_hash,
    upsert_chapter_event_cluster_snapshot,
)


CHAPTER_EVENT_CLUSTER_ENGINE = "llm_event_cluster_v1"
INCREMENTAL_CLUSTER_SYSTEM_PROMPT = "你是一位谨慎的中文回忆录结构编辑，只判断新增来源应该加入哪个已有事件聚合。"
INCREMENTAL_CLUSTER_MAX_ADDED_SOURCES = 5
INCREMENTAL_CLUSTER_MAX_ADDED_RATIO = 0.35
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IncrementalClusterAssignment:
    source_id: str
    target_cluster_index: int | None
    new_cluster_title: str = ""
    summary: str = ""


def _source_card_signature(source: ChapterSourceText) -> dict[str, object]:
    card = source.source_card
    if not card:
        return {"source_text_hash": semantic_text_hash(source.text)}
    return {
        "source_card_id": card.source_card_id,
        "summary": card.summary,
        "people": card.people or [],
        "places": card.places or [],
        "time_hints": card.time_hints or [],
        "event_tags": card.event_tags or [],
        "key_facts": card.key_facts or [],
    }


def _source_cluster_fingerprint_payload(source: ChapterSourceText) -> dict[str, object]:
    return {
        "utterance_id": source.ref.utterance_id,
        "quote": source.ref.quote or "",
        "change_type": source.change_type,
        "source_text_hash": semantic_text_hash(source.text),
        "card": _source_card_signature(source),
    }


def source_fingerprints_for_clusters(sources: list[ChapterSourceText]) -> dict[str, str]:
    fingerprints: dict[str, str] = {}
    for source in sources:
        fingerprints[source.ref.utterance_id] = semantic_json_signature(_source_cluster_fingerprint_payload(source))
    return fingerprints


def chapter_event_cluster_input_signature(
    *,
    chapter: MemoirBookPlanChapter,
    sources: list[ChapterSourceText],
    engine: str = CHAPTER_EVENT_CLUSTER_ENGINE,
) -> str:
    source_ids = [source.ref.utterance_id for source in sources]
    source_fingerprints = source_fingerprints_for_clusters(sources)
    payload = {
        "engine": engine,
        "chapter_id": chapter.id,
        "source_ids": source_ids,
        "source_fingerprints": source_fingerprints,
    }
    return semantic_json_signature(payload)


def serialize_event_clusters(clusters: list[ChapterEventCluster]) -> list[dict[str, object]]:
    return [
        {
            "title": cluster.title,
            "source_ids": list(cluster.source_ids),
            "summary": cluster.summary,
        }
        for cluster in clusters
    ]


def _clusters_from_json(
    raw_clusters: object,
    *,
    valid_source_ids: set[str],
    require_all_sources: bool,
) -> list[ChapterEventCluster]:
    used_source_ids: set[str] = set()
    clusters: list[ChapterEventCluster] = []
    cluster_items = raw_clusters if isinstance(raw_clusters, list) else []
    for item in cluster_items:
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
    if require_all_sources and used_source_ids != valid_source_ids:
        return []
    return clusters


def _snapshot_event_clusters(
    snapshot: MemoirChapterEventClusterSnapshot,
    *,
    sources: list[ChapterSourceText],
) -> list[ChapterEventCluster]:
    return _clusters_from_json(
        snapshot.clusters_json,
        valid_source_ids={source.ref.utterance_id for source in sources},
        require_all_sources=True,
    )


def _snapshot_source_ids(snapshot: MemoirChapterEventClusterSnapshot) -> list[str]:
    source_ids = snapshot.source_ids_json if isinstance(snapshot.source_ids_json, list) else []
    return [source_id for source_id in source_ids if isinstance(source_id, str) and source_id]


def _snapshot_source_fingerprints(snapshot: MemoirChapterEventClusterSnapshot) -> dict[str, str]:
    fingerprints = (
        snapshot.source_fingerprints_json if isinstance(snapshot.source_fingerprints_json, dict) else {}
    )
    return {
        str(source_id): str(fingerprint)
        for source_id, fingerprint in fingerprints.items()
        if isinstance(source_id, str) and isinstance(fingerprint, str) and source_id and fingerprint
    }


def _prune_clusters_to_sources(
    clusters: list[ChapterEventCluster],
    *,
    current_source_ids: set[str],
) -> list[ChapterEventCluster]:
    pruned: list[ChapterEventCluster] = []
    for cluster in clusters:
        source_ids = [source_id for source_id in cluster.source_ids if source_id in current_source_ids]
        if source_ids:
            pruned.append(ChapterEventCluster(title=cluster.title, source_ids=source_ids, summary=cluster.summary))
    return pruned


def _covered_source_ids(clusters: list[ChapterEventCluster]) -> set[str]:
    return {source_id for cluster in clusters for source_id in cluster.source_ids}


def _can_incrementally_cluster(*, added_count: int, current_count: int) -> bool:
    if added_count <= 0:
        return True
    if added_count > INCREMENTAL_CLUSTER_MAX_ADDED_SOURCES:
        return False
    return added_count / max(current_count, 1) <= INCREMENTAL_CLUSTER_MAX_ADDED_RATIO


def _source_card_prompt_text(source: ChapterSourceText) -> str:
    card = source.source_card
    if not card:
        return "source_card: 无"
    return "\n".join(
        [
            f"source_card_id: {card.source_card_id}",
            f"source_card_summary: {card.summary or '无'}",
            f"source_card_people: {card.people or []}",
            f"source_card_places: {card.places or []}",
            f"source_card_time_hints: {card.time_hints or []}",
            f"source_card_event_tags: {card.event_tags or []}",
            f"source_card_key_facts: {card.key_facts or []}",
        ]
    )


def _compact_text(value: str, *, limit: int = 160) -> str:
    return " ".join((value or "").split()).strip()[:limit].rstrip()


def _incremental_source_block(sources: list[ChapterSourceText]) -> str:
    return "\n\n".join(
        (
            f"新增来源 {index}\n"
            f"utterance_id: {source.ref.utterance_id}\n"
            f"{_source_card_prompt_text(source)}\n"
            f"quote: {source.ref.quote or '无'}"
            + (
                ""
                if source.source_card
                else f"\ntranscript_preview: {_compact_text(source.text, limit=160)}"
            )
        )
        for index, source in enumerate(sources, start=1)
    )


def _existing_cluster_block(clusters: list[ChapterEventCluster]) -> str:
    return "\n\n".join(
        (
            f"cluster_index: {index}\n"
            f"title: {cluster.title}\n"
            f"source_ids: {cluster.source_ids}\n"
            f"summary: {cluster.summary or '无'}"
        )
        for index, cluster in enumerate(clusters, start=1)
    )


def _build_incremental_cluster_prompt(
    *,
    chapter: MemoirBookPlanChapter,
    base_clusters: list[ChapterEventCluster],
    added_sources: list[ChapterSourceText],
) -> str:
    return f"""请判断新增来源应该归入哪个已有事件聚合，或成为新的事件聚合。

章节标题参考：{chapter.title}

已有事件聚合：
{_existing_cluster_block(base_clusters)}

新增来源：
{_incremental_source_block(added_sources)}

请输出严格 JSON，不要 Markdown，不要解释。格式：
{{
  "assignments": [
    {{
      "source_id": "新增来源 utterance_id",
      "target_cluster_index": 1,
      "new_cluster_title": "",
      "summary": "归并后的事件摘要"
    }}
  ]
}}

要求：
- source_id 只能来自新增来源。
- target_cluster_index 使用上方已有事件聚合的 cluster_index；如果新增来源不属于任何已有聚合，填 null，并给出 new_cluster_title。
- 只根据新增来源和已有事件聚合判断，不新增事实。
- 每条新增来源都必须出现在 assignments 中。
"""


def _assignment_cluster_index(value: object, cluster_count: int) -> int | None:
    if value is None:
        return None
    try:
        index = int(value)
    except (TypeError, ValueError):
        return None
    return index if 1 <= index <= cluster_count else None


def _assignments_from_content(
    content: str,
    *,
    added_sources: list[ChapterSourceText],
    cluster_count: int,
) -> dict[str, IncrementalClusterAssignment]:
    payload = extract_json_payload(content)
    if not isinstance(payload, dict):
        raise ValueError("AI did not return valid incremental cluster JSON")
    raw_assignments = payload.get("assignments")
    if not isinstance(raw_assignments, list):
        raise ValueError("AI did not return incremental cluster assignments")

    valid_source_ids = {source.ref.utterance_id for source in added_sources}
    assignments: dict[str, IncrementalClusterAssignment] = {}
    for item in raw_assignments:
        if not isinstance(item, dict):
            continue
        source_id = item.get("source_id")
        if not isinstance(source_id, str) or source_id not in valid_source_ids or source_id in assignments:
            continue
        target_cluster_index = _assignment_cluster_index(item.get("target_cluster_index"), cluster_count)
        assignments[source_id] = IncrementalClusterAssignment(
            source_id=source_id,
            target_cluster_index=target_cluster_index,
            new_cluster_title=text_or_empty(
                item.get("new_cluster_title") if isinstance(item.get("new_cluster_title"), str) else ""
            ),
            summary=text_or_empty(item.get("summary") if isinstance(item.get("summary"), str) else ""),
        )
    return assignments


def _singleton_cluster(source: ChapterSourceText) -> ChapterEventCluster:
    title = text_or_empty(source.ref.quote) or (
        source.source_card.summary if source.source_card and source.source_card.summary else ""
    )
    summary = (
        source.source_card.summary
        if source.source_card and source.source_card.summary
        else (source.ref.quote or source.text[:120])
    )
    return ChapterEventCluster(
        title=(title or "单条讲述").strip()[:40],
        source_ids=[source.ref.utterance_id],
        summary=summary.strip()[:160],
    )


def _apply_incremental_assignments(
    *,
    base_clusters: list[ChapterEventCluster],
    added_sources: list[ChapterSourceText],
    assignments: dict[str, IncrementalClusterAssignment],
) -> list[ChapterEventCluster]:
    clusters = [
        ChapterEventCluster(title=cluster.title, source_ids=list(cluster.source_ids), summary=cluster.summary)
        for cluster in base_clusters
    ]
    for source in added_sources:
        source_id = source.ref.utterance_id
        assignment = assignments.get(source_id)
        if assignment and assignment.target_cluster_index is not None:
            cluster_index = assignment.target_cluster_index - 1
            target = clusters[cluster_index]
            source_ids = list(target.source_ids)
            if source_id not in source_ids:
                source_ids.append(source_id)
            clusters[cluster_index] = ChapterEventCluster(
                title=target.title,
                source_ids=source_ids,
                summary=assignment.summary or target.summary,
            )
            continue

        fallback_cluster = _singleton_cluster(source)
        title = (
            assignment.new_cluster_title
            if assignment and assignment.new_cluster_title
            else fallback_cluster.title
        )
        summary = assignment.summary if assignment and assignment.summary else fallback_cluster.summary
        clusters.append(
            ChapterEventCluster(
                title=title,
                source_ids=[source_id],
                summary=summary,
            )
        )
    return clusters


async def _incrementally_cluster_added_sources(
    client: AITextClient,
    *,
    chapter: MemoirBookPlanChapter,
    base_clusters: list[ChapterEventCluster],
    added_sources: list[ChapterSourceText],
) -> list[ChapterEventCluster]:
    prompt = _build_incremental_cluster_prompt(
        chapter=chapter,
        base_clusters=base_clusters,
        added_sources=added_sources,
    )
    try:
        response = await client._call_llm(
            INCREMENTAL_CLUSTER_SYSTEM_PROMPT,
            prompt,
            temperature=0.1,
            max_tokens=max(700, min(1600, 400 + len(added_sources) * 180)),
            timeout=60.0,
        )
        assignments = _assignments_from_content(
            str(response),
            added_sources=added_sources,
            cluster_count=len(base_clusters),
        )
        return _apply_incremental_assignments(
            base_clusters=base_clusters,
            added_sources=added_sources,
            assignments=assignments,
        )
    except Exception:
        logger.warning(
            "AI memoir chapter incremental event clustering failed; fallback to singleton additions "
            "provider=%s chapter_id=%s added_count=%s",
            getattr(client, "provider_name", "unknown"),
            chapter.id,
            len(added_sources),
            exc_info=True,
        )
    return base_clusters + [_singleton_cluster(source) for source in added_sources]


def _snapshot_can_increment(
    snapshot: MemoirChapterEventClusterSnapshot,
    *,
    current_source_ids: list[str],
    current_fingerprints: dict[str, str],
) -> bool:
    previous_source_ids = _snapshot_source_ids(snapshot)
    previous_fingerprints = _snapshot_source_fingerprints(snapshot)
    if not previous_source_ids or not previous_fingerprints:
        return False
    previous_source_id_set = set(previous_source_ids)
    current_source_id_set = set(current_source_ids)
    stable_source_ids = previous_source_id_set & current_source_id_set
    return all(
        previous_fingerprints.get(source_id) == current_fingerprints.get(source_id)
        for source_id in stable_source_ids
    )


async def _try_incremental_event_clusters(
    db: AsyncSession,
    *,
    client: AITextClient,
    chapter: MemoirBookPlanChapter,
    sources: list[ChapterSourceText],
    current_fingerprints: dict[str, str],
    engine: str,
) -> list[ChapterEventCluster] | None:
    current_source_ids = [source.ref.utterance_id for source in sources]
    current_source_id_set = set(current_source_ids)
    snapshot = await db.scalar(
        select(MemoirChapterEventClusterSnapshot)
        .where(
            MemoirChapterEventClusterSnapshot.project_id == chapter.project_id,
            MemoirChapterEventClusterSnapshot.chapter_id == chapter.id,
            MemoirChapterEventClusterSnapshot.engine == engine,
            MemoirChapterEventClusterSnapshot.status == MemoirSemanticCacheStatus.READY.value,
        )
        .order_by(MemoirChapterEventClusterSnapshot.updated_at.desc(), MemoirChapterEventClusterSnapshot.id.desc())
    )
    if not snapshot or not _snapshot_can_increment(
        snapshot,
        current_source_ids=current_source_ids,
        current_fingerprints=current_fingerprints,
    ):
        return None

    previous_source_ids = _snapshot_source_ids(snapshot)
    previous_source_id_set = set(previous_source_ids)
    previous_clusters = _clusters_from_json(
        snapshot.clusters_json,
        valid_source_ids=previous_source_id_set,
        require_all_sources=True,
    )
    if not previous_clusters:
        return None

    added_source_ids = current_source_id_set - previous_source_id_set
    if not _can_incrementally_cluster(added_count=len(added_source_ids), current_count=len(current_source_ids)):
        return None

    base_clusters = _prune_clusters_to_sources(previous_clusters, current_source_ids=current_source_id_set)
    stable_source_ids = current_source_id_set & previous_source_id_set
    if _covered_source_ids(base_clusters) != stable_source_ids:
        return None
    if not added_source_ids:
        return base_clusters if _covered_source_ids(base_clusters) == current_source_id_set else None

    source_by_id = {source.ref.utterance_id: source for source in sources}
    added_sources = [source_by_id[source_id] for source_id in current_source_ids if source_id in added_source_ids]
    return await _incrementally_cluster_added_sources(
        client,
        chapter=chapter,
        base_clusters=base_clusters,
        added_sources=added_sources,
    )


async def get_or_build_chapter_event_clusters(
    db: AsyncSession,
    *,
    client: AITextClient,
    chapter: MemoirBookPlanChapter,
    sources: list[ChapterSourceText],
    engine: str = CHAPTER_EVENT_CLUSTER_ENGINE,
) -> list[ChapterEventCluster]:
    source_ids = [source.ref.utterance_id for source in sources]
    source_fingerprints = source_fingerprints_for_clusters(sources)
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

    clusters = await _try_incremental_event_clusters(
        db,
        client=client,
        chapter=chapter,
        sources=sources,
        current_fingerprints=source_fingerprints,
        engine=engine,
    )
    if clusters:
        await upsert_chapter_event_cluster_snapshot(
            db,
            project_id=chapter.project_id,
            plan_id=chapter.plan_id,
            chapter_id=chapter.id,
            engine=engine,
            input_signature=input_signature,
            clusters_json=serialize_event_clusters(clusters),
            source_ids_json=source_ids,
            source_fingerprints_json=source_fingerprints,
        )
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
        source_ids_json=source_ids,
        source_fingerprints_json=source_fingerprints,
    )
    return clusters
