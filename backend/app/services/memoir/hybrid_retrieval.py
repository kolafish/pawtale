from __future__ import annotations

from dataclasses import dataclass
import logging
import time
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memoir_retrieval import MemoirRetrievalEntry
from app.services.embeddings import (
    EmbeddingClient,
    create_embedding_client,
    embed_texts_in_batches,
)


logger = logging.getLogger(__name__)

DEFAULT_VECTOR_LIMIT = 8
DEFAULT_BM25_LIMIT = 8
DEFAULT_CANDIDATE_LIMIT = 12
RRF_RANK_CONSTANT = 60

RetrievalChannel = Literal["vector", "bm25"]


@dataclass(frozen=True)
class RetrievalHit:
    query_index: int
    query_text: str
    channel: RetrievalChannel
    rank: int
    score: float
    rrf_contribution: float
    entry_id: int
    utterance_id: str
    voice_session_id: str
    transcript_version_id: str | None
    audio_file_id: str | None
    entry_kind: str
    ordinal: int
    search_text: str
    text_source: str
    embedding_model: str
    index_version: int


@dataclass(frozen=True)
class QueryRetrievalTrace:
    query_index: int
    query_text: str
    vector_hits: tuple[RetrievalHit, ...]
    bm25_hits: tuple[RetrievalHit, ...]
    vector_elapsed_ms: int
    bm25_elapsed_ms: int


@dataclass(frozen=True)
class HybridRetrievalCandidate:
    rank: int
    utterance_id: str
    fused_score: float
    representative_hit: RetrievalHit
    evidence: tuple[RetrievalHit, ...]


@dataclass(frozen=True)
class HybridRetrievalTrace:
    project_id: str
    excluded_voice_session_id: str | None
    requested_embedding_model: str | None
    resolved_embedding_model: str | None
    embedding_dimensions: int | None
    embedding_elapsed_ms: int
    retrieval_elapsed_ms: int
    queries: tuple[QueryRetrievalTrace, ...]
    retrieved_entry_union_count: int
    retrieved_source_union_count: int
    candidates: tuple[HybridRetrievalCandidate, ...]


@dataclass
class _CandidateAccumulator:
    score: float
    evidence: list[RetrievalHit]


def _normalized_queries(queries: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in queries:
        query = value.strip()
        if query and query not in seen:
            seen.add(query)
            normalized.append(query)
    return normalized


def _filters(project_id: str, excluded_voice_session_id: str | None) -> list:
    filters = [MemoirRetrievalEntry.project_id == project_id]
    if excluded_voice_session_id:
        filters.append(
            MemoirRetrievalEntry.voice_session_id != excluded_voice_session_id
        )
    return filters


def _hit(
    entry: MemoirRetrievalEntry,
    *,
    query_index: int,
    query_text: str,
    channel: RetrievalChannel,
    rank: int,
    score: float,
) -> RetrievalHit:
    return RetrievalHit(
        query_index=query_index,
        query_text=query_text,
        channel=channel,
        rank=rank,
        score=score,
        rrf_contribution=1.0 / (RRF_RANK_CONSTANT + rank),
        entry_id=entry.id,
        utterance_id=entry.utterance_id,
        voice_session_id=entry.voice_session_id,
        transcript_version_id=entry.transcript_version_id,
        audio_file_id=entry.audio_file_id,
        entry_kind=entry.entry_kind,
        ordinal=entry.ordinal,
        search_text=entry.search_text,
        text_source=entry.text_source,
        embedding_model=entry.embedding_model,
        index_version=entry.index_version,
    )


async def _vector_hits(
    db: AsyncSession,
    *,
    project_id: str,
    excluded_voice_session_id: str | None,
    query_index: int,
    query_text: str,
    embedding: tuple[float, ...],
    embedding_model: str,
    limit: int,
) -> tuple[RetrievalHit, ...]:
    distance = MemoirRetrievalEntry.embedding.cosine_distance(list(embedding))
    best_entry_per_source = (
        select(
            MemoirRetrievalEntry.id.label("entry_id"),
            distance.label("distance"),
        )
        .where(
            *_filters(project_id, excluded_voice_session_id),
            MemoirRetrievalEntry.embedding_model == embedding_model,
        )
        .distinct(MemoirRetrievalEntry.utterance_id)
        .order_by(
            MemoirRetrievalEntry.utterance_id,
            distance,
            MemoirRetrievalEntry.id,
        )
        .subquery()
    )
    rows = (
        await db.execute(
            select(
                MemoirRetrievalEntry,
                (1 - best_entry_per_source.c.distance).label("score"),
            )
            .join(
                best_entry_per_source,
                best_entry_per_source.c.entry_id == MemoirRetrievalEntry.id,
            )
            .order_by(
                best_entry_per_source.c.distance,
                MemoirRetrievalEntry.id,
            )
            .limit(limit)
        )
    ).all()
    return tuple(
        _hit(
            entry,
            query_index=query_index,
            query_text=query_text,
            channel="vector",
            rank=rank,
            score=float(score),
        )
        for rank, (entry, score) in enumerate(rows, 1)
    )


async def _bm25_hits(
    db: AsyncSession,
    *,
    project_id: str,
    excluded_voice_session_id: str | None,
    query_index: int,
    query_text: str,
    limit: int,
) -> tuple[RetrievalHit, ...]:
    score = func.pdb.score(MemoirRetrievalEntry.id)
    best_entry_per_source = (
        select(
            MemoirRetrievalEntry.id.label("entry_id"),
            score.label("score"),
        )
        .where(
            MemoirRetrievalEntry.search_text.op("|||")(query_text),
            *_filters(project_id, excluded_voice_session_id),
        )
        .distinct(MemoirRetrievalEntry.utterance_id)
        .order_by(
            MemoirRetrievalEntry.utterance_id,
            score.desc(),
            MemoirRetrievalEntry.id,
        )
        .subquery()
    )
    rows = (
        await db.execute(
            select(
                MemoirRetrievalEntry,
                best_entry_per_source.c.score,
            )
            .join(
                best_entry_per_source,
                best_entry_per_source.c.entry_id == MemoirRetrievalEntry.id,
            )
            .order_by(
                best_entry_per_source.c.score.desc(),
                MemoirRetrievalEntry.id,
            )
            .limit(limit)
        )
    ).all()
    return tuple(
        _hit(
            entry,
            query_index=query_index,
            query_text=query_text,
            channel="bm25",
            rank=rank,
            score=float(score),
        )
        for rank, (entry, score) in enumerate(rows, 1)
    )


def _fuse(
    traces: list[QueryRetrievalTrace],
    candidate_limit: int,
) -> tuple[HybridRetrievalCandidate, ...]:
    accumulators: dict[str, _CandidateAccumulator] = {}
    for trace in traces:
        for hits in (trace.vector_hits, trace.bm25_hits):
            seen_sources: set[str] = set()
            for hit in hits:
                if hit.utterance_id in seen_sources:
                    continue
                seen_sources.add(hit.utterance_id)
                accumulator = accumulators.setdefault(
                    hit.utterance_id,
                    _CandidateAccumulator(score=0.0, evidence=[]),
                )
                accumulator.score += hit.rrf_contribution
                accumulator.evidence.append(hit)

    ranked = sorted(
        accumulators.items(),
        key=lambda item: (-item[1].score, item[0]),
    )[:candidate_limit]
    return tuple(
        HybridRetrievalCandidate(
            rank=rank,
            utterance_id=utterance_id,
            fused_score=accumulator.score,
            representative_hit=min(
                accumulator.evidence,
                key=lambda hit: (
                    -hit.rrf_contribution,
                    hit.query_index,
                    hit.channel,
                    hit.entry_id,
                ),
            ),
            evidence=tuple(
                sorted(
                    accumulator.evidence,
                    key=lambda hit: (
                        hit.query_index,
                        hit.channel,
                        hit.rank,
                        hit.entry_id,
                    ),
                )
            ),
        )
        for rank, (utterance_id, accumulator) in enumerate(ranked, 1)
    )


async def retrieve_hybrid_candidates(
    db: AsyncSession,
    *,
    project_id: str,
    queries: list[str],
    excluded_voice_session_id: str | None = None,
    vector_limit: int = DEFAULT_VECTOR_LIMIT,
    bm25_limit: int = DEFAULT_BM25_LIMIT,
    candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
    embedding_client: EmbeddingClient | None = None,
) -> HybridRetrievalTrace:
    if vector_limit <= 0 or bm25_limit <= 0 or candidate_limit <= 0:
        raise ValueError("hybrid retrieval limits must be greater than zero")
    normalized_queries = _normalized_queries(queries)
    if not normalized_queries:
        return HybridRetrievalTrace(
            project_id=project_id,
            excluded_voice_session_id=excluded_voice_session_id,
            requested_embedding_model=None,
            resolved_embedding_model=None,
            embedding_dimensions=None,
            embedding_elapsed_ms=0,
            retrieval_elapsed_ms=0,
            queries=(),
            retrieved_entry_union_count=0,
            retrieved_source_union_count=0,
            candidates=(),
        )

    client: EmbeddingClient = embedding_client or create_embedding_client()
    embedded = await embed_texts_in_batches(client, normalized_queries)
    if len(embedded.embeddings) != len(normalized_queries):
        raise ValueError("embedding count does not match retrieval query count")

    started = time.perf_counter()
    traces: list[QueryRetrievalTrace] = []
    entry_ids: set[int] = set()
    source_ids: set[str] = set()
    for query_index, (query_text, embedding) in enumerate(
        zip(normalized_queries, embedded.embeddings, strict=True)
    ):
        vector_started = time.perf_counter()
        vector_hits = await _vector_hits(
            db,
            project_id=project_id,
            excluded_voice_session_id=excluded_voice_session_id,
            query_index=query_index,
            query_text=query_text,
            embedding=embedding,
            embedding_model=embedded.resolved_model,
            limit=vector_limit,
        )
        vector_elapsed_ms = round((time.perf_counter() - vector_started) * 1000)

        bm25_started = time.perf_counter()
        bm25_hits = await _bm25_hits(
            db,
            project_id=project_id,
            excluded_voice_session_id=excluded_voice_session_id,
            query_index=query_index,
            query_text=query_text,
            limit=bm25_limit,
        )
        bm25_elapsed_ms = round((time.perf_counter() - bm25_started) * 1000)

        for hit in (*vector_hits, *bm25_hits):
            entry_ids.add(hit.entry_id)
            source_ids.add(hit.utterance_id)
        traces.append(
            QueryRetrievalTrace(
                query_index=query_index,
                query_text=query_text,
                vector_hits=vector_hits,
                bm25_hits=bm25_hits,
                vector_elapsed_ms=vector_elapsed_ms,
                bm25_elapsed_ms=bm25_elapsed_ms,
            )
        )

    candidates = _fuse(traces, candidate_limit)
    retrieval_elapsed_ms = round((time.perf_counter() - started) * 1000)
    logger.info(
        "memoir hybrid retrieval completed project_id=%s queries=%d entries=%d "
        "sources=%d candidates=%d requested_model=%s resolved_model=%s "
        "embedding_elapsed_ms=%d retrieval_elapsed_ms=%d",
        project_id,
        len(traces),
        len(entry_ids),
        len(source_ids),
        len(candidates),
        embedded.requested_model,
        embedded.resolved_model,
        embedded.service_elapsed_ms,
        retrieval_elapsed_ms,
    )
    return HybridRetrievalTrace(
        project_id=project_id,
        excluded_voice_session_id=excluded_voice_session_id,
        requested_embedding_model=embedded.requested_model,
        resolved_embedding_model=embedded.resolved_model,
        embedding_dimensions=embedded.dimensions,
        embedding_elapsed_ms=embedded.service_elapsed_ms,
        retrieval_elapsed_ms=retrieval_elapsed_ms,
        queries=tuple(traces),
        retrieved_entry_union_count=len(entry_ids),
        retrieved_source_union_count=len(source_ids),
        candidates=candidates,
    )
