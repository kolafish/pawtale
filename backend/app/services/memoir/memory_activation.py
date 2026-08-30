from __future__ import annotations

from dataclasses import asdict, dataclass
import logging
import re
import time
import uuid

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application_errors import ResourceNotFoundError
from app.models.memoir import MemoirChatMessage, MemoirVoiceUtterance
from app.models.memoir_retrieval import (
    MemoirMemoryActivationTrace,
    MemoirRetrievalEntry,
)
from app.services.ai_calls import generate_structured_with_client
from app.services.ai_factory import DEEPSEEK_V4_FLASH_MODEL, get_ai_service
from app.services.ai_timeouts import ai_error_metadata, ai_text_timeout_seconds
from app.services.ai_types import AITextClient
from app.services.ai_workloads import AITextWorkload, ai_text_workload_policy
from app.services.embeddings import (
    EmbeddingClient,
    create_embedding_client,
    embed_texts_in_batches,
)
from app.services.memoir.access import load_memoir_project
from app.services.memoir.hybrid_retrieval import HybridRetrievalCandidate, RetrievalHit
from app.services.memoir.retrieval_query_planning import (
    QUERY_PLANNING_MODEL,
    RetrievalQueryPlanningTrace,
    plan_and_retrieve_memory_candidates,
)
from app.services.memoir.transcript_projection import current_utterance_text_and_source


logger = logging.getLogger(__name__)

MEMORY_ACTIVATION_PROMPT_VERSION = "memoir_memory_activation_v1"
MEMORY_ACTIVATION_MODEL = DEEPSEEK_V4_FLASH_MODEL
MEMORY_ACTIVATION_TEMPERATURE = 0.1
SOURCE_EXCERPT_MAX_CHARS = 520
ACTIVATION_CONTEXT_MAX_CHARS = 4_000
MAX_MATCHED_INDEX_TEXTS = 4
_SENTENCE_END = re.compile(r"(?<=[。！？!?；;])|\n+")


class _SelectedCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    utterance_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class _SelectionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected: list[_SelectedCandidate] = Field(default_factory=list)
    decision_reason: str


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
    representative_query: str
    representative_channel: str
    representative_rank: int


@dataclass(frozen=True)
class MemoryActivationResult:
    trace_id: str | None
    status: str
    context: str
    selected: tuple[MemoryActivationCandidate, ...] = ()
    selected_count: int = 0
    cached: bool = False


def memory_activation_system_prompt() -> str:
    return """你负责为中文回忆录访谈选择本轮真正需要激活的历史讲述。

“老余口述”只是产品名称，“老余”不是讲述者的默认姓名或称呼。

根据 current_narration、retrieval_queries 和 candidates，选择零个或多个确实有助于理解当前讲述、自然承接、避免重复提问或提出更好下一问的候选。没有固定选择数量，不要为了凑数而选择；仅仅共享宽泛主题不够。候选中的 source_excerpt 是可回溯的原讲述片段，matched_index_texts 只是检索入口。不要改写、补充或合并候选内容。

selected 中只返回候选已有的 utterance_id，并为每项简要说明本轮为什么需要它。decision_reason 说明整体取舍。只返回 JSON：
{"selected":[{"utterance_id":"...","reason":"..."}],"decision_reason":"..."}"""


def _selection_user_prompt(
    planning: RetrievalQueryPlanningTrace,
    candidates: tuple[MemoryActivationCandidate, ...],
) -> str:
    candidate_blocks = []
    for candidate in candidates:
        matched = "\n".join(f"  - {text}" for text in candidate.matched_index_texts)
        candidate_blocks.append(
            f"candidate {candidate.rank}\n"
            f"utterance_id: {candidate.utterance_id}\n"
            f"rrf_fused_score: {candidate.fused_score:.8f}\n"
            f"representative_match: {candidate.representative_channel} "
            f"query={candidate.representative_query} rank={candidate.representative_rank}\n"
            f"matched_index_texts:\n{matched or '  - 无'}\n"
            f"source_excerpt:\n{candidate.source_excerpt}"
        )
    queries = "\n".join(f"- {query}" for query in planning.retrieval_queries)
    return (
        f"current_narration:\n{planning.generation.input_text}\n\n"
        f"retrieval_queries:\n{queries or '- 无'}\n\n"
        f"candidates:\n\n" + "\n\n".join(candidate_blocks)
    )


def _split_source_text(text: str) -> tuple[str, ...]:
    normalized = text.strip()
    if len(normalized) <= SOURCE_EXCERPT_MAX_CHARS:
        return (normalized,) if normalized else ()
    pieces = [piece.strip() for piece in _SENTENCE_END.split(normalized) if piece.strip()]
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        while len(piece) > SOURCE_EXCERPT_MAX_CHARS:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(piece[:SOURCE_EXCERPT_MAX_CHARS])
            piece = piece[SOURCE_EXCERPT_MAX_CHARS:]
        if not piece:
            continue
        if current and len(current) + len(piece) > SOURCE_EXCERPT_MAX_CHARS:
            chunks.append(current)
            current = piece
        else:
            current += piece
    if current:
        chunks.append(current)
    return tuple(chunks)


def _dot(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right):
        raise ValueError("embedding dimensions do not match")
    return sum(a * b for a, b in zip(left, right, strict=True))


def _matched_index_texts(candidate: HybridRetrievalCandidate) -> tuple[str, ...]:
    ranked = sorted(
        candidate.evidence,
        key=lambda hit: (
            -hit.rrf_contribution,
            hit.query_index,
            hit.channel,
            hit.rank,
            hit.entry_id,
        ),
    )
    return tuple(dict.fromkeys(hit.search_text for hit in ranked if hit.search_text.strip()))[:MAX_MATCHED_INDEX_TEXTS]


async def _prepare_candidates(
    db: AsyncSession,
    *,
    planning: RetrievalQueryPlanningTrace,
    embedding_client: EmbeddingClient | None,
) -> tuple[tuple[MemoryActivationCandidate, ...], dict[str, object]]:
    hybrid = planning.hybrid_retrieval
    if hybrid is None or not hybrid.candidates:
        return (), {
            "requested_embedding_model": None,
            "resolved_embedding_model": None,
            "embedding_dimensions": None,
            "embedding_elapsed_ms": 0,
        }
    utterance_ids = [candidate.utterance_id for candidate in hybrid.candidates]
    utterances = (
        await db.scalars(select(MemoirVoiceUtterance).where(MemoirVoiceUtterance.id.in_(utterance_ids)))
    ).all()
    utterance_by_id = {utterance.id: utterance for utterance in utterances}

    sources: list[
        tuple[
            HybridRetrievalCandidate,
            MemoirVoiceUtterance,
            str,
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
        sources.append((candidate, utterance, source_text, source, chunks))
        if len(chunks) > 1:
            anchor = f"{planning.generation.input_text}\n{candidate.representative_hit.search_text}"
            long_sources.append((source_index, anchor, chunks))

    selected_excerpt_by_source: dict[int, str] = {
        index: source[4][0] for index, source in enumerate(sources) if len(source[4]) == 1
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
                key=lambda index: (_dot(anchor_embedding, chunk_embeddings[index]), -index),
            )
            selected_excerpt_by_source[source_index] = sources[source_index][4][best_index]
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
            representative_query=candidate.representative_hit.query_text,
            representative_channel=candidate.representative_hit.channel,
            representative_rank=candidate.representative_hit.rank,
        )
        for index, (candidate, utterance, _source_text, source, _chunks) in enumerate(sources)
    )
    return prepared, excerpt_embedding_trace


def _activation_context(
    selected: tuple[tuple[MemoryActivationCandidate, str], ...],
) -> tuple[str, int]:
    if not selected:
        return "", 0
    prefix = (
        "以下是从同一回忆录过往讲述中激活的原文片段。只在确实有帮助时自然承接，"
        "不要说出检索、索引、系统记忆或内部编号，也不要把历史内容当成用户刚刚说的："
    )
    blocks: list[str] = []
    used = len(prefix)
    for index, (candidate, _reason) in enumerate(selected, 1):
        block = f"历史讲述 {index}：{candidate.source_excerpt}"
        if used + len(block) > ACTIVATION_CONTEXT_MAX_CHARS:
            break
        blocks.append(block)
        used += len(block)
    return (prefix + "\n" + "\n".join(blocks), len(blocks)) if blocks else ("", 0)


def _hit_trace(hit: RetrievalHit) -> dict[str, object]:
    return asdict(hit)


def _planning_trace(planning: RetrievalQueryPlanningTrace) -> dict[str, object]:
    hybrid = planning.hybrid_retrieval
    return {
        "generation": asdict(planning.generation),
        "retrieval_queries": list(planning.retrieval_queries),
        "hybrid_retrieval": None
        if hybrid is None
        else {
            "project_id": hybrid.project_id,
            "excluded_voice_session_id": hybrid.excluded_voice_session_id,
            "requested_embedding_model": hybrid.requested_embedding_model,
            "resolved_embedding_model": hybrid.resolved_embedding_model,
            "embedding_dimensions": hybrid.embedding_dimensions,
            "embedding_elapsed_ms": hybrid.embedding_elapsed_ms,
            "retrieval_elapsed_ms": hybrid.retrieval_elapsed_ms,
            "retrieved_entry_union_count": hybrid.retrieved_entry_union_count,
            "retrieved_source_union_count": hybrid.retrieved_source_union_count,
            "queries": [
                {
                    "query_index": query.query_index,
                    "query_text": query.query_text,
                    "vector_elapsed_ms": query.vector_elapsed_ms,
                    "bm25_elapsed_ms": query.bm25_elapsed_ms,
                    "vector_hits": [_hit_trace(hit) for hit in query.vector_hits],
                    "bm25_hits": [_hit_trace(hit) for hit in query.bm25_hits],
                }
                for query in hybrid.queries
            ],
            "candidates": [asdict(candidate) for candidate in hybrid.candidates],
        },
    }


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
            MemoirMemoryActivationTrace.prompt_version == MEMORY_ACTIVATION_PROMPT_VERSION,
            MemoirMemoryActivationTrace.status.in_(("skipped", "succeeded")),
        )
        .order_by(MemoirMemoryActivationTrace.created_at.desc())
        .limit(1)
    )
    if trace is None:
        return None
    activation = trace.trace_json.get("activation")
    context = activation.get("injected_context", "") if isinstance(activation, dict) else ""
    return MemoryActivationResult(
        trace_id=trace.id,
        status=trace.status,
        context=context if isinstance(context, str) else "",
        selected_count=trace.selected_count,
        cached=True,
    )


async def activate_memory_for_turn(
    db: AsyncSession,
    *,
    project_id: str,
    voice_session_id: str,
    current_message: MemoirChatMessage,
    query_ai_client: AITextClient | None = None,
    selection_ai_client: AITextClient | None = None,
    embedding_client: EmbeddingClient | None = None,
) -> MemoryActivationResult:
    cached = await _cached_result(db, current_message.id)
    if cached is not None:
        return cached

    started = time.perf_counter()
    trace_id = str(uuid.uuid4())
    current_message_id = current_message.id
    current_narration = current_message.content_text.strip()
    current_utterance_id = await db.scalar(
        select(MemoirVoiceUtterance.id)
        .where(MemoirVoiceUtterance.chat_message_id == current_message.id)
        .order_by(MemoirVoiceUtterance.created_at.desc())
        .limit(1)
    )
    base_trace: dict[str, object] = {
        "current_narration": current_narration,
        "current_message_id": current_message_id,
        "current_utterance_id": current_utterance_id,
    }
    entry_count = int(
        await db.scalar(
            select(func.count(MemoirRetrievalEntry.id)).where(
                MemoirRetrievalEntry.project_id == project_id,
                MemoirRetrievalEntry.voice_session_id != voice_session_id,
            )
        )
        or 0
    )
    if entry_count == 0:
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        trace = MemoirMemoryActivationTrace(
            id=trace_id,
            project_id=project_id,
            voice_session_id=voice_session_id,
            current_message_id=current_message_id,
            current_utterance_id=current_utterance_id,
            status="skipped",
            should_retrieve=False,
            query_count=0,
            candidate_count=0,
            selected_count=0,
            prompt_version=MEMORY_ACTIVATION_PROMPT_VERSION,
            requested_model=QUERY_PLANNING_MODEL,
            elapsed_ms=elapsed_ms,
            trace_json={**base_trace, "skip_reason": "no_historical_retrieval_entries"},
        )
        await _persist_trace(db, trace)
        return MemoryActivationResult(trace_id=trace_id, status="skipped", context="")

    planning: RetrievalQueryPlanningTrace | None = None
    planning_json: dict[str, object] | None = None
    candidates: tuple[MemoryActivationCandidate, ...] = ()
    excerpt_embedding: dict[str, object] = {}
    try:
        async with db.begin_nested():
            planning = await plan_and_retrieve_memory_candidates(
                db,
                project_id=project_id,
                current_narration=current_narration,
                excluded_voice_session_id=voice_session_id,
                ai_client=query_ai_client,
                embedding_client=embedding_client,
            )
            planning_json = _planning_trace(planning)
            if not planning.should_retrieve:
                elapsed_ms = round((time.perf_counter() - started) * 1000)
                trace = MemoirMemoryActivationTrace(
                    id=trace_id,
                    project_id=project_id,
                    voice_session_id=voice_session_id,
                    current_message_id=current_message_id,
                    current_utterance_id=current_utterance_id,
                    status="skipped",
                    should_retrieve=False,
                    query_count=0,
                    candidate_count=0,
                    selected_count=0,
                    prompt_version=MEMORY_ACTIVATION_PROMPT_VERSION,
                    requested_model=planning.generation.requested_model,
                    resolved_model=planning.generation.resolved_model,
                    provider=planning.generation.provider,
                    elapsed_ms=elapsed_ms,
                    trace_json={
                        **base_trace,
                        "query_planning": planning_json,
                        "skip_reason": "query_planner_declined_retrieval",
                    },
                )
                await _persist_trace(db, trace)
                return MemoryActivationResult(trace_id=trace_id, status="skipped", context="")

            candidates, excerpt_embedding = await _prepare_candidates(
                db,
                planning=planning,
                embedding_client=embedding_client,
            )
            if not candidates:
                elapsed_ms = round((time.perf_counter() - started) * 1000)
                trace = MemoirMemoryActivationTrace(
                    id=trace_id,
                    project_id=project_id,
                    voice_session_id=voice_session_id,
                    current_message_id=current_message_id,
                    current_utterance_id=current_utterance_id,
                    status="skipped",
                    should_retrieve=True,
                    query_count=len(planning.retrieval_queries),
                    candidate_count=0,
                    selected_count=0,
                    prompt_version=MEMORY_ACTIVATION_PROMPT_VERSION,
                    requested_model=planning.generation.requested_model,
                    resolved_model=planning.generation.resolved_model,
                    provider=planning.generation.provider,
                    elapsed_ms=elapsed_ms,
                    trace_json={
                        **base_trace,
                        "query_planning": planning_json,
                        "candidate_preparation": {
                            **excerpt_embedding,
                            "candidates": [],
                        },
                        "skip_reason": "no_retrieval_candidates",
                    },
                )
                await _persist_trace(db, trace)
                return MemoryActivationResult(trace_id=trace_id, status="skipped", context="")

            policy = ai_text_workload_policy(AITextWorkload.REALTIME_STRUCTURED)
            timeout_seconds = ai_text_timeout_seconds(policy.timeout_profile)
            selector = selection_ai_client or get_ai_service(MEMORY_ACTIVATION_MODEL)
            generated = await generate_structured_with_client(
                selector,
                memory_activation_system_prompt(),
                _selection_user_prompt(planning, candidates),
                output_model=_SelectionOutput,
                requested_model=MEMORY_ACTIVATION_MODEL,
                temperature=MEMORY_ACTIVATION_TEMPERATURE,
                max_tokens=policy.max_tokens,
                timeout=timeout_seconds,
                max_attempts=policy.max_attempts,
            )
            candidate_by_id = {candidate.utterance_id: candidate for candidate in candidates}
            selected_pairs: list[tuple[MemoryActivationCandidate, str]] = []
            seen: set[str] = set()
            ignored_ids: list[str] = []
            for item in generated.value.selected:
                candidate = candidate_by_id.get(item.utterance_id)
                if candidate is None:
                    ignored_ids.append(item.utterance_id)
                    continue
                if item.utterance_id in seen:
                    continue
                seen.add(item.utterance_id)
                selected_pairs.append((candidate, item.reason.strip()))
            selected = tuple(selected_pairs)
            context, injected_count = _activation_context(selected)
            injected = selected[:injected_count]
            rejected_ids = [candidate.utterance_id for candidate in candidates if candidate.utterance_id not in seen]
            response = generated.response
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            trace = MemoirMemoryActivationTrace(
                id=trace_id,
                project_id=project_id,
                voice_session_id=voice_session_id,
                current_message_id=current_message_id,
                current_utterance_id=current_utterance_id,
                status="succeeded",
                should_retrieve=True,
                query_count=len(planning.retrieval_queries),
                candidate_count=len(candidates),
                selected_count=len(injected),
                prompt_version=MEMORY_ACTIVATION_PROMPT_VERSION,
                requested_model=response.requested_model,
                resolved_model=response.resolved_model,
                provider=response.provider,
                elapsed_ms=elapsed_ms,
                trace_json={
                    **base_trace,
                    "query_planning": planning_json,
                    "candidate_preparation": {
                        **excerpt_embedding,
                        "candidates": [asdict(candidate) for candidate in candidates],
                    },
                    "selection": {
                        "prompt_version": MEMORY_ACTIVATION_PROMPT_VERSION,
                        "requested_model": response.requested_model,
                        "resolved_model": response.resolved_model,
                        "response_model": response.response_model,
                        "provider": response.provider,
                        "selected": [
                            {"utterance_id": candidate.utterance_id, "reason": reason} for candidate, reason in selected
                        ],
                        "rejected_utterance_ids": rejected_ids,
                        "ignored_unknown_utterance_ids": ignored_ids,
                        "decision_reason": generated.value.decision_reason.strip(),
                        "raw_output": response.content,
                        "usage": response.usage,
                        "elapsed_ms": response.elapsed_ms,
                        "attempt_count": response.attempt_count,
                        "timeout_seconds": timeout_seconds,
                    },
                    "activation": {
                        "packages": [
                            {**asdict(candidate), "selection_reason": reason} for candidate, reason in injected
                        ],
                        "injected_context": context,
                        "not_injected_due_to_budget_utterance_ids": [
                            candidate.utterance_id for candidate, _reason in selected[injected_count:]
                        ],
                    },
                },
            )
        await _persist_trace(db, trace)
        logger.info(
            "memoir memory activation completed project_id=%s session_id=%s "
            "queries=%d candidates=%d selected=%d elapsed_ms=%d",
            project_id,
            voice_session_id,
            trace.query_count,
            trace.candidate_count,
            trace.selected_count,
            trace.elapsed_ms,
        )
        return MemoryActivationResult(
            trace_id=trace_id,
            status="succeeded",
            context=context,
            selected=tuple(candidate for candidate, _reason in injected),
            selected_count=len(injected),
        )
    except Exception as error:
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        provider = getattr(selection_ai_client or query_ai_client, "provider_name", None)
        resolved_model = getattr(selection_ai_client or query_ai_client, "model_name", None)
        failure = ai_error_metadata(
            error,
            requested_model=MEMORY_ACTIVATION_MODEL,
            provider=provider,
            resolved_model=resolved_model,
            timeout_seconds=ai_text_timeout_seconds(
                ai_text_workload_policy(AITextWorkload.REALTIME_STRUCTURED).timeout_profile
            ),
            elapsed_ms=elapsed_ms,
        )
        failure["message"] = str(error)
        trace = MemoirMemoryActivationTrace(
            id=trace_id,
            project_id=project_id,
            voice_session_id=voice_session_id,
            current_message_id=current_message_id,
            current_utterance_id=current_utterance_id,
            status="failed",
            should_retrieve=bool(planning and planning.should_retrieve),
            query_count=len(planning.retrieval_queries) if planning else 0,
            candidate_count=len(candidates),
            selected_count=0,
            prompt_version=MEMORY_ACTIVATION_PROMPT_VERSION,
            requested_model=MEMORY_ACTIVATION_MODEL,
            resolved_model=resolved_model,
            provider=provider,
            elapsed_ms=elapsed_ms,
            trace_json={
                **base_trace,
                **({"query_planning": planning_json} if planning_json is not None else {}),
                **(
                    {
                        "candidate_preparation": {
                            **excerpt_embedding,
                            "candidates": [asdict(candidate) for candidate in candidates],
                        }
                    }
                    if candidates
                    else {}
                ),
                "failure": failure,
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
        filters.append(MemoirMemoryActivationTrace.voice_session_id == voice_session_id)
    total = int(await db.scalar(select(func.count(MemoirMemoryActivationTrace.id)).where(*filters)) or 0)
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
