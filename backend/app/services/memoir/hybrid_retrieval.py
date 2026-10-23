from __future__ import annotations

from dataclasses import dataclass
import logging
import time
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memoir_retrieval import MemoirRetrievalEntry
from app.services.embeddings import EmbeddingClient, create_embedding_client


logger = logging.getLogger(__name__)

DEFAULT_VECTOR_LIMIT = 8
DEFAULT_BM25_LIMIT = 8
DEFAULT_CANDIDATE_LIMIT = 6
RRF_RANK_CONSTANT = 60

RetrievalChannel = Literal["vector", "bm25"]


@dataclass(frozen=True)
class RetrievalHit:
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
    query_text: str
    requested_embedding_model: str | None
    resolved_embedding_model: str | None
    embedding_dimensions: int | None
    embedding_elapsed_ms: int
    vector_elapsed_ms: int
    bm25_elapsed_ms: int
    retrieval_elapsed_ms: int
    vector_hits: tuple[RetrievalHit, ...]
    bm25_hits: tuple[RetrievalHit, ...]
    retrieved_entry_count: int
    retrieved_source_count: int
    candidates: tuple[HybridRetrievalCandidate, ...]


@dataclass
class _CandidateAccumulator:
    score: float
    evidence: list[RetrievalHit]


def _filters(project_id: str, excluded_voice_session_id: str | None, excluded_utterance_ids: tuple[str, ...]) -> list:
    filters = [MemoirRetrievalEntry.project_id == project_id]
    if excluded_voice_session_id:
        filters.append(
            MemoirRetrievalEntry.voice_session_id != excluded_voice_session_id
        )
    if excluded_utterance_ids:
        filters.append(MemoirRetrievalEntry.utterance_id.not_in(excluded_utterance_ids))
    return filters


def _hit(
    entry: MemoirRetrievalEntry,
    *,
    channel: RetrievalChannel,
    rank: int,
    score: float,
) -> RetrievalHit:
    return RetrievalHit(
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
    excluded_utterance_ids: tuple[str, ...],
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
            *_filters(project_id, excluded_voice_session_id, excluded_utterance_ids),
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
    excluded_utterance_ids: tuple[str, ...],
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
            *_filters(project_id, excluded_voice_session_id, excluded_utterance_ids),
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
            channel="bm25",
            rank=rank,
            score=float(score),
        )
        for rank, (entry, score) in enumerate(rows, 1)
    )


def _fuse(
    vector_hits: tuple[RetrievalHit, ...],
    bm25_hits: tuple[RetrievalHit, ...],
    candidate_limit: int,
) -> tuple[HybridRetrievalCandidate, ...]:
    accumulators: dict[str, _CandidateAccumulator] = {}
    for hits in (vector_hits, bm25_hits):
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
                    hit.channel,
                    hit.entry_id,
                ),
            ),
            evidence=tuple(
                sorted(
                    accumulator.evidence,
                    key=lambda hit: (hit.channel, hit.rank, hit.entry_id),
                )
            ),
        )
        for rank, (utterance_id, accumulator) in enumerate(ranked, 1)
    )


def _empty_trace(
    *,
    project_id: str,
    excluded_voice_session_id: str | None,
) -> HybridRetrievalTrace:
    return HybridRetrievalTrace(
        project_id=project_id,
        excluded_voice_session_id=excluded_voice_session_id,
        query_text="",
        requested_embedding_model=None,
        resolved_embedding_model=None,
        embedding_dimensions=None,
        embedding_elapsed_ms=0,
        vector_elapsed_ms=0,
        bm25_elapsed_ms=0,
        retrieval_elapsed_ms=0,
        vector_hits=(),
        bm25_hits=(),
        retrieved_entry_count=0,
        retrieved_source_count=0,
        candidates=(),
    )


async def retrieve_hybrid_candidates(
    db: AsyncSession,
    *,
    project_id: str,
    query_text: str,
    excluded_voice_session_id: str | None = None,
    excluded_utterance_ids: tuple[str, ...] = (),
    vector_limit: int = DEFAULT_VECTOR_LIMIT,
    bm25_limit: int = DEFAULT_BM25_LIMIT,
    candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
    embedding_client: EmbeddingClient | None = None,
) -> HybridRetrievalTrace:
    if vector_limit <= 0 or bm25_limit <= 0 or candidate_limit <= 0:
        raise ValueError("hybrid retrieval limits must be greater than zero")
    normalized_query = query_text.strip()
    if not normalized_query:
        return _empty_trace(
            project_id=project_id,
            excluded_voice_session_id=excluded_voice_session_id,
        )

    client: EmbeddingClient = embedding_client or create_embedding_client()
    embedded = await client.embed([normalized_query])
    if len(embedded.embeddings) != 1:
        raise ValueError("embedding service must return one retrieval query embedding")

    started = time.perf_counter()
    vector_started = time.perf_counter()
    vector_hits = await _vector_hits(
        db,
        project_id=project_id,
        excluded_voice_session_id=excluded_voice_session_id,
        excluded_utterance_ids=excluded_utterance_ids,
        embedding=embedded.embeddings[0],
        embedding_model=embedded.resolved_model,
        limit=vector_limit,
    )
    vector_elapsed_ms = round((time.perf_counter() - vector_started) * 1000)

    bm25_started = time.perf_counter()
    bm25_hits = await _bm25_hits(
        db,
        project_id=project_id,
        excluded_voice_session_id=excluded_voice_session_id,
        excluded_utterance_ids=excluded_utterance_ids,
        query_text=normalized_query,
        limit=bm25_limit,
    )
    bm25_elapsed_ms = round((time.perf_counter() - bm25_started) * 1000)

    hits = (*vector_hits, *bm25_hits)
    entry_ids = {hit.entry_id for hit in hits}
    source_ids = {hit.utterance_id for hit in hits}
    candidates = _fuse(vector_hits, bm25_hits, candidate_limit)
    retrieval_elapsed_ms = round((time.perf_counter() - started) * 1000)
    logger.info(
        "memoir hybrid retrieval completed project_id=%s query_chars=%d entries=%d "
        "sources=%d candidates=%d requested_model=%s resolved_model=%s "
        "embedding_elapsed_ms=%d retrieval_elapsed_ms=%d",
        project_id,
        len(normalized_query),
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
        query_text=normalized_query,
        requested_embedding_model=embedded.requested_model,
        resolved_embedding_model=embedded.resolved_model,
        embedding_dimensions=embedded.dimensions,
        embedding_elapsed_ms=embedded.service_elapsed_ms,
        vector_elapsed_ms=vector_elapsed_ms,
        bm25_elapsed_ms=bm25_elapsed_ms,
        retrieval_elapsed_ms=retrieval_elapsed_ms,
        vector_hits=vector_hits,
        bm25_hits=bm25_hits,
        retrieved_entry_count=len(entry_ids),
        retrieved_source_count=len(source_ids),
        candidates=candidates,
    )
