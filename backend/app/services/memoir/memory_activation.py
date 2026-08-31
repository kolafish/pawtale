from __future__ import annotations

from dataclasses import asdict, dataclass
import logging
import re
import time
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application_errors import ResourceNotFoundError
from app.models.memoir import MemoirChatMessage, MemoirVoiceUtterance
from app.models.memoir_retrieval import (
    MemoirMemoryActivationTrace,
    MemoirRetrievalEntry,
)
from app.services.embeddings import (
    EmbeddingClient,
    create_embedding_client,
    embed_texts_in_batches,
)
from app.services.memoir.access import load_memoir_project
from app.services.memoir.hybrid_retrieval import (
    HybridRetrievalCandidate,
    HybridRetrievalTrace,
    retrieve_hybrid_candidates,
)
from app.services.memoir.transcript_projection import current_utterance_text_and_source


logger = logging.getLogger(__name__)

MEMORY_ACTIVATION_PROMPT_VERSION = "memoir_memory_activation_v2"
SOURCE_EXCERPT_MAX_CHARS = 520
ACTIVATION_CONTEXT_MAX_CHARS = 4_000
MAX_MATCHED_INDEX_TEXTS = 4
_SENTENCE_PART = re.compile(r".*?(?:[。！？!?；;]+|\n+|$)", re.DOTALL)


@dataclass(frozen=True)
class MemoryActivationCandidate:
    rank: int
    utterance_id: str
    voice_session_id: str
    transcript_version_id: str | None
    audio_file_id: str | None
    start_ms: int | None
    end_ms: int | None
    source_text_source: str
    source_excerpt: str
    matched_index_texts: tuple[str, ...]
    fused_score: float
    representative_channel: str
    representative_rank: int


@dataclass(frozen=True)
class MemoryActivationResult:
    trace_id: str | None
    status: str
    context: str
    injected_count: int = 0
    cached: bool = False


def _split_source_text(text: str) -> tuple[str, ...]:
    normalized = text.strip()
    if len(normalized) <= SOURCE_EXCERPT_MAX_CHARS:
        return (normalized,) if normalized else ()
    pieces = [
        match.group()
        for match in _SENTENCE_PART.finditer(normalized)
        if match.group()
    ]
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        while len(piece) > SOURCE_EXCERPT_MAX_CHARS:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(piece[:SOURCE_EXCERPT_MAX_CHARS].strip())
            piece = piece[SOURCE_EXCERPT_MAX_CHARS:]
        if not piece:
            continue
        if current and len(current) + len(piece) > SOURCE_EXCERPT_MAX_CHARS:
            chunks.append(current.strip())
            current = piece
        else:
            current += piece
    if current:
        chunks.append(current.strip())
    return tuple(chunk for chunk in chunks if chunk)


def _dot(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right):
        raise ValueError("embedding dimensions do not match")
    return sum(a * b for a, b in zip(left, right, strict=True))


def _matched_index_texts(candidate: HybridRetrievalCandidate) -> tuple[str, ...]:
    ranked = sorted(
        candidate.evidence,
        key=lambda hit: (
            -hit.rrf_contribution,
            hit.channel,
            hit.rank,
            hit.entry_id,
        ),
    )
    return tuple(
        dict.fromkeys(hit.search_text for hit in ranked if hit.search_text.strip())
    )[:MAX_MATCHED_INDEX_TEXTS]


async def _prepare_candidates(
    db: AsyncSession,
    *,
    current_narration: str,
    hybrid: HybridRetrievalTrace,
    embedding_client: EmbeddingClient | None,
) -> tuple[tuple[MemoryActivationCandidate, ...], dict[str, object]]:
    if not hybrid.candidates:
        return (), {
            "requested_embedding_model": None,
            "resolved_embedding_model": None,
            "embedding_dimensions": None,
            "embedding_elapsed_ms": 0,
        }
    utterance_ids = [candidate.utterance_id for candidate in hybrid.candidates]
    utterances = (
        await db.scalars(
            select(MemoirVoiceUtterance).where(
                MemoirVoiceUtterance.id.in_(utterance_ids)
            )
        )
    ).all()
    utterance_by_id = {utterance.id: utterance for utterance in utterances}

    sources: list[
        tuple[
            HybridRetrievalCandidate,
            MemoirVoiceUtterance,
            str,
            tuple[str, ...],
        ]
    ] = []
    long_sources: list[tuple[int, str, tuple[str, ...]]] = []
    for candidate in hybrid.candidates:
        utterance = utterance_by_id.get(candidate.utterance_id)
        if utterance is None:
            continue
        source_text, source = current_utterance_text_and_source(utterance)
        chunks = _split_source_text(source_text)
        if not chunks:
            continue
        source_index = len(sources)
        sources.append((candidate, utterance, source, chunks))
        if len(chunks) > 1:
            anchor = (
                f"{current_narration}\n{candidate.representative_hit.search_text}"
            )
            long_sources.append((source_index, anchor, chunks))

    selected_excerpt_by_source: dict[int, str] = {
        index: item[3][0]
        for index, item in enumerate(sources)
        if len(item[3]) == 1
    }
    excerpt_embedding_trace: dict[str, object] = {
        "requested_embedding_model": None,
        "resolved_embedding_model": None,
        "embedding_dimensions": None,
        "embedding_elapsed_ms": 0,
    }
    if long_sources:
        texts: list[str] = []
        spans: list[tuple[int, int, int]] = []
        for source_index, anchor, chunks in long_sources:
            start = len(texts)
            texts.extend((anchor, *chunks))
            spans.append((source_index, start, len(chunks)))
        client = embedding_client or create_embedding_client()
        embedded = await embed_texts_in_batches(client, texts)
        for source_index, start, chunk_count in spans:
            anchor_embedding = embedded.embeddings[start]
            chunk_embeddings = embedded.embeddings[start + 1 : start + 1 + chunk_count]
            best_index = max(
                range(chunk_count),
                key=lambda index: (
                    _dot(anchor_embedding, chunk_embeddings[index]),
                    -index,
                ),
            )
            selected_excerpt_by_source[source_index] = sources[source_index][3][
                best_index
            ]
        excerpt_embedding_trace = {
            "requested_embedding_model": embedded.requested_model,
            "resolved_embedding_model": embedded.resolved_model,
            "embedding_dimensions": embedded.dimensions,
            "embedding_elapsed_ms": embedded.service_elapsed_ms,
        }

    prepared = tuple(
        MemoryActivationCandidate(
            rank=candidate.rank,
            utterance_id=utterance.id,
            voice_session_id=utterance.voice_session_id,
            transcript_version_id=candidate.representative_hit.transcript_version_id,
            audio_file_id=utterance.audio_file_id,
            start_ms=utterance.start_ms,
            end_ms=utterance.end_ms,
            source_text_source=source,
            source_excerpt=selected_excerpt_by_source[index],
            matched_index_texts=_matched_index_texts(candidate),
            fused_score=candidate.fused_score,
            representative_channel=candidate.representative_hit.channel,
            representative_rank=candidate.representative_hit.rank,
        )
        for index, (candidate, utterance, source, _chunks) in enumerate(sources)
    )
    return prepared, excerpt_embedding_trace


def _activation_context(
    candidates: tuple[MemoryActivationCandidate, ...],
) -> tuple[str, int]:
    if not candidates:
        return "", 0
    prefix = (
        "以下是从同一回忆录过往讲述中检索到的候选原文，不保证与本轮相关。"
        "先自行判断，只有确实有助于理解最新讲述、避免重复提问或提出更好下一问时才使用；"
        "不相关时全部忽略。不要说出检索、索引、系统记忆或内部编号，"
        "也不要把历史内容当成用户刚刚说的："
    )
    blocks: list[str] = []
    used = len(prefix)
    for index, candidate in enumerate(candidates, 1):
        block = f"历史候选 {index}：{candidate.source_excerpt}"
        if used + len(block) > ACTIVATION_CONTEXT_MAX_CHARS:
            break
        blocks.append(block)
        used += len(block)
    return (prefix + "\n" + "\n".join(blocks), len(blocks)) if blocks else ("", 0)


async def _persist_trace(
    db: AsyncSession,
    trace: MemoirMemoryActivationTrace,
) -> None:
    if db.bind is None:
        raise RuntimeError("memory activation trace persistence requires a bound session")
    session_maker = async_sessionmaker(db.bind, expire_on_commit=False)
    async with session_maker() as trace_db:
        trace_db.add(trace)
        await trace_db.commit()


async def _cached_result(
    db: AsyncSession,
    current_message_id: str,
) -> MemoryActivationResult | None:
    trace = await db.scalar(
        select(MemoirMemoryActivationTrace)
        .where(
            MemoirMemoryActivationTrace.current_message_id == current_message_id,
            MemoirMemoryActivationTrace.prompt_version
            == MEMORY_ACTIVATION_PROMPT_VERSION,
            MemoirMemoryActivationTrace.status.in_(("skipped", "succeeded")),
        )
        .order_by(MemoirMemoryActivationTrace.created_at.desc())
        .limit(1)
    )
    if trace is None:
        return None
    activation = trace.trace_json.get("activation")
    context = (
        activation.get("injected_context", "")
        if isinstance(activation, dict)
        else ""
    )
    return MemoryActivationResult(
        trace_id=trace.id,
        status=trace.status,
        context=context if isinstance(context, str) else "",
        injected_count=trace.injected_count,
        cached=True,
    )


async def activate_memory_for_turn(
    db: AsyncSession,
    *,
    project_id: str,
    voice_session_id: str,
    current_message: MemoirChatMessage,
    embedding_client: EmbeddingClient | None = None,
) -> MemoryActivationResult:
    cached = await _cached_result(db, current_message.id)
    if cached is not None:
        return cached

    started = time.perf_counter()
    trace_id = str(uuid.uuid4())
    current_narration = current_message.content_text.strip()
    current_utterance_id = await db.scalar(
        select(MemoirVoiceUtterance.id)
        .where(MemoirVoiceUtterance.chat_message_id == current_message.id)
        .order_by(MemoirVoiceUtterance.created_at.desc())
        .limit(1)
    )
    base_trace: dict[str, object] = {
        "current_narration": current_narration,
        "current_message_id": current_message.id,
        "current_utterance_id": current_utterance_id,
    }
    historical_entry_count = int(
        await db.scalar(
            select(func.count(MemoirRetrievalEntry.id)).where(
                MemoirRetrievalEntry.project_id == project_id,
                MemoirRetrievalEntry.voice_session_id != voice_session_id,
            )
        )
        or 0
    )
    if historical_entry_count == 0:
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        trace = MemoirMemoryActivationTrace(
            id=trace_id,
            project_id=project_id,
            voice_session_id=voice_session_id,
            current_message_id=current_message.id,
            current_utterance_id=current_utterance_id,
            status="skipped",
            candidate_count=0,
            injected_count=0,
            prompt_version=MEMORY_ACTIVATION_PROMPT_VERSION,
            elapsed_ms=elapsed_ms,
            trace_json={
                **base_trace,
                "skip_reason": "no_historical_retrieval_entries",
            },
        )
        await _persist_trace(db, trace)
        return MemoryActivationResult(trace_id=trace_id, status="skipped", context="")

    hybrid: HybridRetrievalTrace | None = None
    candidates: tuple[MemoryActivationCandidate, ...] = ()
    excerpt_embedding: dict[str, object] = {}
    failure_stage = "hybrid_retrieval"
    try:
        async with db.begin_nested():
            hybrid = await retrieve_hybrid_candidates(
                db,
                project_id=project_id,
                query_text=current_narration,
                excluded_voice_session_id=voice_session_id,
                embedding_client=embedding_client,
            )
            if not hybrid.candidates:
                elapsed_ms = round((time.perf_counter() - started) * 1000)
                trace = MemoirMemoryActivationTrace(
                    id=trace_id,
                    project_id=project_id,
                    voice_session_id=voice_session_id,
                    current_message_id=current_message.id,
                    current_utterance_id=current_utterance_id,
                    status="skipped",
                    candidate_count=0,
                    injected_count=0,
                    prompt_version=MEMORY_ACTIVATION_PROMPT_VERSION,
                    requested_model=hybrid.requested_embedding_model,
                    resolved_model=hybrid.resolved_embedding_model,
                    provider="local_embedding",
                    elapsed_ms=elapsed_ms,
                    trace_json={
                        **base_trace,
                        "retrieval": asdict(hybrid),
                        "skip_reason": "no_retrieval_candidates",
                    },
                )
                await _persist_trace(db, trace)
                return MemoryActivationResult(
                    trace_id=trace_id,
                    status="skipped",
                    context="",
                )

            failure_stage = "candidate_excerpt_selection"
            candidates, excerpt_embedding = await _prepare_candidates(
                db,
                current_narration=current_narration,
                hybrid=hybrid,
                embedding_client=embedding_client,
            )
            context, injected_count = _activation_context(candidates)
            injected = candidates[:injected_count]
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            trace = MemoirMemoryActivationTrace(
                id=trace_id,
                project_id=project_id,
                voice_session_id=voice_session_id,
                current_message_id=current_message.id,
                current_utterance_id=current_utterance_id,
                status="succeeded",
                candidate_count=len(candidates),
                injected_count=len(injected),
                prompt_version=MEMORY_ACTIVATION_PROMPT_VERSION,
                requested_model=hybrid.requested_embedding_model,
                resolved_model=hybrid.resolved_embedding_model,
                provider="local_embedding",
                elapsed_ms=elapsed_ms,
                trace_json={
                    **base_trace,
                    "retrieval": asdict(hybrid),
                    "candidate_preparation": {
                        **excerpt_embedding,
                        "candidates": [asdict(candidate) for candidate in candidates],
                    },
                    "activation": {
                        "packages": [asdict(candidate) for candidate in injected],
                        "injected_context": context,
                        "not_injected_due_to_budget_utterance_ids": [
                            candidate.utterance_id
                            for candidate in candidates[injected_count:]
                        ],
                    },
                },
            )
        await _persist_trace(db, trace)
        logger.info(
            "memoir memory activation completed project_id=%s session_id=%s "
            "candidates=%d injected=%d elapsed_ms=%d",
            project_id,
            voice_session_id,
            trace.candidate_count,
            trace.injected_count,
            trace.elapsed_ms,
        )
        return MemoryActivationResult(
            trace_id=trace_id,
            status="succeeded",
            context=context,
            injected_count=len(injected),
        )
    except Exception as error:
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        requested_model = (
            hybrid.requested_embedding_model
            if hybrid is not None
            else getattr(embedding_client, "model", None)
        )
        resolved_model = (
            hybrid.resolved_embedding_model if hybrid is not None else None
        )
        trace = MemoirMemoryActivationTrace(
            id=trace_id,
            project_id=project_id,
            voice_session_id=voice_session_id,
            current_message_id=current_message.id,
            current_utterance_id=current_utterance_id,
            status="failed",
            candidate_count=len(candidates),
            injected_count=0,
            prompt_version=MEMORY_ACTIVATION_PROMPT_VERSION,
            requested_model=requested_model,
            resolved_model=resolved_model,
            provider="local_embedding",
            elapsed_ms=elapsed_ms,
            trace_json={
                **base_trace,
                **({"retrieval": asdict(hybrid)} if hybrid is not None else {}),
                **(
                    {
                        "candidate_preparation": {
                            **excerpt_embedding,
                            "candidates": [
                                asdict(candidate) for candidate in candidates
                            ],
                        }
                    }
                    if candidates
                    else {}
                ),
                "failure": {
                    "stage": failure_stage,
                    "message": str(error),
                    "error_type": type(error).__name__,
                    "elapsed_ms": elapsed_ms,
                },
            },
        )
        try:
            await _persist_trace(db, trace)
        except Exception:
            logger.exception(
                "memoir memory activation trace persistence failed project_id=%s session_id=%s",
                project_id,
                voice_session_id,
            )
        logger.exception(
            "memoir memory activation failed; continuing without historical context "
            "project_id=%s session_id=%s trace_id=%s",
            project_id,
            voice_session_id,
            trace_id,
        )
        return MemoryActivationResult(trace_id=trace_id, status="failed", context="")


async def list_memory_activation_traces(
    db: AsyncSession,
    *,
    project_id: str,
    user_id: str,
    allow_admin: bool = False,
    voice_session_id: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, object]:
    await load_memoir_project(
        db,
        project_id,
        user_id,
        allow_admin=allow_admin,
    )
    filters = [MemoirMemoryActivationTrace.project_id == project_id]
    if voice_session_id:
        filters.append(
            MemoirMemoryActivationTrace.voice_session_id == voice_session_id
        )
    total = int(
        await db.scalar(
            select(func.count(MemoirMemoryActivationTrace.id)).where(*filters)
        )
        or 0
    )
    items = (
        await db.scalars(
            select(MemoirMemoryActivationTrace)
            .where(*filters)
            .order_by(
                MemoirMemoryActivationTrace.created_at.desc(),
                MemoirMemoryActivationTrace.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return {"items": list(items), "total": total, "limit": limit, "offset": offset}


async def get_memory_activation_trace(
    db: AsyncSession,
    *,
    project_id: str,
    trace_id: str,
    user_id: str,
    allow_admin: bool = False,
) -> MemoirMemoryActivationTrace:
    await load_memoir_project(
        db,
        project_id,
        user_id,
        allow_admin=allow_admin,
    )
    trace = await db.scalar(
        select(MemoirMemoryActivationTrace).where(
            MemoirMemoryActivationTrace.id == trace_id,
            MemoirMemoryActivationTrace.project_id == project_id,
        )
    )
    if trace is None:
        raise ResourceNotFoundError("记忆激活日志不存在")
    return trace
