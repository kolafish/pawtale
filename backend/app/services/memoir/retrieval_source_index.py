from __future__ import annotations

from dataclasses import asdict
import logging
import time

from pydantic import TypeAdapter
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.clock import utcnow_naive
from app.domain.memoir.types import (
    MemoirEditingJobStatus,
    MemoirEditingJobType,
    MemoirRetrievalEntryKind,
)
from app.models.memoir import MemoirEditingJob, MemoirVoiceUtterance
from app.models.memoir_retrieval import MemoirRetrievalEntry
from app.services.ai_factory import DEEPSEEK_V4_PRO_MODEL, get_ai_service
from app.services.ai_timeouts import (
    ai_error_metadata,
    ai_text_timeout_seconds,
    ai_user_error_message,
)
from app.services.ai_types import AITextClient
from app.services.embeddings import (
    EmbeddingClient,
    create_embedding_client,
    embed_texts_in_batches,
)
from app.services.memoir import editing_jobs
from app.services.memoir.editing_jobs import EditingJobResult
from app.services.memoir.material_roles import is_countable_material
from app.services.memoir.retrieval_semantic_units import (
    SemanticUnitGenerationTrace,
    generate_semantic_units,
    semantic_unit_workload_policy,
)
from app.services.memoir.transcript_projection import (
    current_transcript_version_id,
    current_utterance_text_and_source,
    source_text_hash,
)


SOURCE_INDEX_VERSION = 1
SOURCE_ENTRY_ORDINAL = 0
SEMANTIC_INDEX_VERSION = 1
SEMANTIC_INDEX_MODEL = DEEPSEEK_V4_PRO_MODEL
logger = logging.getLogger(__name__)


async def schedule_source_indexing_job(
    db: AsyncSession,
    *,
    project_id: str,
    utterance: MemoirVoiceUtterance,
    source: str,
) -> MemoirEditingJob | None:
    text, text_source = current_utterance_text_and_source(utterance)
    if not is_countable_material(utterance, text):
        return None
    text_hash = source_text_hash(text)
    transcript_version_id = current_transcript_version_id(utterance)
    return await editing_jobs.enqueue_editing_job(
        db,
        project_id=project_id,
        job_type=MemoirEditingJobType.INDEX_RETRIEVAL_SOURCE,
        payload_json={
            "utterance_id": utterance.id,
            "source_text_hash": text_hash,
            "transcript_version_id": transcript_version_id,
            "text_source": text_source,
            "index_version": SOURCE_INDEX_VERSION,
            "source": source,
        },
        dedupe_key=(
            f"index_retrieval_source:v{SOURCE_INDEX_VERSION}:"
            f"{utterance.id}:{transcript_version_id or 'none'}:{text_hash}"
        ),
        retry_failed=True,
    )


async def schedule_semantic_indexing_job(
    db: AsyncSession,
    *,
    project_id: str,
    utterance: MemoirVoiceUtterance,
    source: str,
) -> MemoirEditingJob | None:
    text, text_source = current_utterance_text_and_source(utterance)
    if not is_countable_material(utterance, text):
        return None
    text_hash = source_text_hash(text)
    transcript_version_id = current_transcript_version_id(utterance)
    return await editing_jobs.enqueue_editing_job(
        db,
        project_id=project_id,
        job_type=MemoirEditingJobType.INDEX_RETRIEVAL_SEMANTICS,
        payload_json={
            "utterance_id": utterance.id,
            "source_text_hash": text_hash,
            "transcript_version_id": transcript_version_id,
            "text_source": text_source,
            "index_version": SEMANTIC_INDEX_VERSION,
            "source": source,
        },
        dedupe_key=(
            f"index_retrieval_semantics:v{SEMANTIC_INDEX_VERSION}:"
            f"{utterance.id}:{transcript_version_id or 'none'}:{text_hash}"
        ),
        retry_failed=source == "retry",
        preserve_progress=True,
    )


async def _load_utterance(
    db: AsyncSession,
    utterance_id: str,
    *,
    for_update: bool = False,
) -> MemoirVoiceUtterance | None:
    statement = (
        select(MemoirVoiceUtterance)
        .where(MemoirVoiceUtterance.id == utterance_id)
        .options(
            selectinload(MemoirVoiceUtterance.voice_session),
            selectinload(MemoirVoiceUtterance.chat_message),
        )
        .execution_options(populate_existing=True)
    )
    if for_update:
        statement = statement.with_for_update()
    return await db.scalar(statement)


async def _skip_unavailable_source(
    db: AsyncSession,
    *,
    project_id: str,
    utterance_id: str,
    reason: str,
    entry_kind: MemoirRetrievalEntryKind | None = None,
) -> EditingJobResult:
    statement = delete(MemoirRetrievalEntry).where(
        MemoirRetrievalEntry.project_id == project_id,
        MemoirRetrievalEntry.utterance_id == utterance_id,
    )
    if entry_kind is not None:
        statement = statement.where(
            MemoirRetrievalEntry.entry_kind == entry_kind.value
        )
    await db.execute(statement)
    return EditingJobResult(
        status=MemoirEditingJobStatus.SKIPPED,
        result_json={"utterance_id": utterance_id},
        error_message=reason,
    )


async def _skip_stale_source(
    db: AsyncSession,
    *,
    project_id: str,
    utterance: MemoirVoiceUtterance,
) -> EditingJobResult:
    replacement = await schedule_source_indexing_job(
        db,
        project_id=project_id,
        utterance=utterance,
        source="stale_source_replaced",
    )
    return EditingJobResult(
        status=MemoirEditingJobStatus.SKIPPED,
        result_json={
            "utterance_id": utterance.id,
            "replacement_job_id": replacement.id if replacement else None,
        },
        error_message="source text changed",
    )


async def _skip_stale_semantics(
    db: AsyncSession,
    *,
    project_id: str,
    utterance: MemoirVoiceUtterance,
) -> EditingJobResult:
    replacement = await schedule_semantic_indexing_job(
        db,
        project_id=project_id,
        utterance=utterance,
        source="stale_semantics_replaced",
    )
    return EditingJobResult(
        status=MemoirEditingJobStatus.SKIPPED,
        result_json={
            "utterance_id": utterance.id,
            "replacement_job_id": replacement.id if replacement else None,
        },
        error_message="source text changed",
    )


async def run_source_indexing_job(
    db: AsyncSession,
    job: MemoirEditingJob,
    embedding_client: EmbeddingClient | None = None,
) -> EditingJobResult:
    payload = job.payload_json or {}
    utterance_id = str(payload.get("utterance_id") or "").strip()
    expected_hash = str(payload.get("source_text_hash") or "").strip()
    if not utterance_id or len(expected_hash) != 64:
        return EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            error_message="retrieval source unavailable",
        )

    utterance = await _load_utterance(db, utterance_id)
    if utterance is None or utterance.voice_session.project_id != job.project_id:
        return await _skip_unavailable_source(
            db,
            project_id=job.project_id,
            utterance_id=utterance_id,
            reason="utterance unavailable",
        )
    text, _text_source = current_utterance_text_and_source(utterance)
    if not is_countable_material(utterance, text):
        return await _skip_unavailable_source(
            db,
            project_id=job.project_id,
            utterance_id=utterance_id,
            reason="narrator transcript unavailable",
        )
    if source_text_hash(text) != expected_hash:
        return await _skip_stale_source(
            db,
            project_id=job.project_id,
            utterance=utterance,
        )

    client: EmbeddingClient = embedding_client or create_embedding_client()
    embedded = await client.embed([text])

    locked = await _load_utterance(db, utterance_id, for_update=True)
    if locked is None or locked.voice_session.project_id != job.project_id:
        return await _skip_unavailable_source(
            db,
            project_id=job.project_id,
            utterance_id=utterance_id,
            reason="utterance unavailable",
        )
    current_text, text_source = current_utterance_text_and_source(locked)
    if not is_countable_material(locked, current_text):
        return await _skip_unavailable_source(
            db,
            project_id=job.project_id,
            utterance_id=utterance_id,
            reason="narrator transcript unavailable",
        )
    if source_text_hash(current_text) != expected_hash:
        return await _skip_stale_source(
            db,
            project_id=job.project_id,
            utterance=locked,
        )

    now = utcnow_naive()
    values = {
        "project_id": job.project_id,
        "voice_session_id": locked.voice_session_id,
        "utterance_id": locked.id,
        "transcript_version_id": current_transcript_version_id(locked),
        "audio_file_id": locked.audio_file_id,
        "entry_kind": MemoirRetrievalEntryKind.SOURCE_TEXT.value,
        "ordinal": SOURCE_ENTRY_ORDINAL,
        "search_text": current_text,
        "text_source": text_source,
        "source_text_hash": expected_hash,
        "embedding": list(embedded.embeddings[0]),
        "embedding_model": embedded.resolved_model,
        "index_version": SOURCE_INDEX_VERSION,
        "created_at": now,
        "updated_at": now,
    }
    statement = insert(MemoirRetrievalEntry).values(**values)
    statement = statement.on_conflict_do_update(
        constraint="uq_memoir_retrieval_entries_source_slot",
        set_={
            key: value
            for key, value in values.items()
            if key not in {"created_at", "utterance_id", "entry_kind", "ordinal"}
        },
    ).returning(MemoirRetrievalEntry.id)
    entry_id = await db.scalar(statement)
    await db.execute(
        delete(MemoirRetrievalEntry).where(
            MemoirRetrievalEntry.project_id == job.project_id,
            MemoirRetrievalEntry.utterance_id == locked.id,
            MemoirRetrievalEntry.entry_kind
            == MemoirRetrievalEntryKind.SEMANTIC_UNIT.value,
            MemoirRetrievalEntry.source_text_hash != expected_hash,
        )
    )
    semantic_job = await schedule_semantic_indexing_job(
        db,
        project_id=job.project_id,
        utterance=locked,
        source="source_indexed",
    )
    return EditingJobResult(
        result_json={
            "entry_id": entry_id,
            "utterance_id": locked.id,
            "transcript_version_id": current_transcript_version_id(locked),
            "source_text_hash": expected_hash,
            "text_source": text_source,
            "requested_model": embedded.requested_model,
            "resolved_model": embedded.resolved_model,
            "dimensions": embedded.dimensions,
            "index_version": SOURCE_INDEX_VERSION,
            "semantic_job_id": semantic_job.id if semantic_job else None,
        }
    )


async def run_semantic_indexing_job(
    db: AsyncSession,
    job: MemoirEditingJob,
    embedding_client: EmbeddingClient | None = None,
    ai_client: AITextClient | None = None,
) -> EditingJobResult:
    payload = job.payload_json or {}
    utterance_id = str(payload.get("utterance_id") or "").strip()
    expected_hash = str(payload.get("source_text_hash") or "").strip()
    if not utterance_id or len(expected_hash) != 64:
        return EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            error_message="retrieval source unavailable",
        )

    utterance = await _load_utterance(db, utterance_id)
    if utterance is None or utterance.voice_session.project_id != job.project_id:
        return await _skip_unavailable_source(
            db,
            project_id=job.project_id,
            utterance_id=utterance_id,
            reason="utterance unavailable",
            entry_kind=MemoirRetrievalEntryKind.SEMANTIC_UNIT,
        )
    text, _text_source = current_utterance_text_and_source(utterance)
    if not is_countable_material(utterance, text):
        return await _skip_unavailable_source(
            db,
            project_id=job.project_id,
            utterance_id=utterance_id,
            reason="narrator transcript unavailable",
            entry_kind=MemoirRetrievalEntryKind.SEMANTIC_UNIT,
        )
    if source_text_hash(text) != expected_hash:
        return await _skip_stale_semantics(
            db,
            project_id=job.project_id,
            utterance=utterance,
        )

    generation_client = ai_client or get_ai_service(SEMANTIC_INDEX_MODEL)
    generation_started = time.perf_counter()
    try:
        saved = (job.result_json or {}).get("semantic_generation")
        if saved and (job.result_json or {}).get("source_text_hash") == expected_hash:
            generation = TypeAdapter(SemanticUnitGenerationTrace).validate_python(saved)
        else:
            generation = await generate_semantic_units(
                source_text=text, requested_model=SEMANTIC_INDEX_MODEL, ai_client=generation_client,
            )
    except Exception as error:
        policy = semantic_unit_workload_policy()
        metadata = ai_error_metadata(
            error,
            requested_model=SEMANTIC_INDEX_MODEL,
            provider=getattr(generation_client, "provider_name", None),
            resolved_model=getattr(generation_client, "model_name", None),
            timeout_seconds=ai_text_timeout_seconds(policy.timeout_profile),
            elapsed_ms=round((time.perf_counter() - generation_started) * 1000),
        )
        logger.warning(
            "memoir semantic retrieval generation failed utterance_id=%s "
            "requested_model=%s resolved_model=%s provider=%s "
            "elapsed_ms=%s error_type=%s",
            utterance_id,
            metadata["requested_model"],
            metadata["model"],
            metadata["provider"],
            metadata["elapsed_ms"],
            metadata["error_type"],
        )
        return EditingJobResult(
            status=MemoirEditingJobStatus.FAILED,
            result_json={
                "stage": "generate_semantic_units",
                "utterance_id": utterance_id,
                "source_text_hash": expected_hash,
                **metadata,
            },
            error_message=ai_user_error_message(error, "语义索引提取失败"),
        )

    current = await _load_utterance(db, utterance_id)
    if current is None or current.voice_session.project_id != job.project_id:
        return await _skip_unavailable_source(
            db,
            project_id=job.project_id,
            utterance_id=utterance_id,
            reason="utterance unavailable",
            entry_kind=MemoirRetrievalEntryKind.SEMANTIC_UNIT,
        )
    current_text, _current_text_source = current_utterance_text_and_source(current)
    if source_text_hash(current_text) != expected_hash:
        return await _skip_stale_semantics(
            db,
            project_id=job.project_id,
            utterance=current,
        )

    checkpoint = {"stage": "embed_semantic_units", "source_text_hash": expected_hash,
                  "semantic_generation": asdict(generation)}
    if not await editing_jobs.update_editing_job_progress(db, job, checkpoint):
        raise editing_jobs.EditingJobProgressLost
    embedded = None
    if generation.units:
        client: EmbeddingClient = embedding_client or create_embedding_client()
        embedding_started = time.perf_counter()
        try:
            embedded = await embed_texts_in_batches(
                client,
                list(generation.units),
            )
        except Exception as error:
            embedding_model = getattr(client, "model", None)
            elapsed_ms = round((time.perf_counter() - embedding_started) * 1000)
            logger.warning(
                "memoir semantic retrieval embedding failed utterance_id=%s "
                "model=%s elapsed_ms=%d error_type=%s",
                utterance_id,
                embedding_model,
                elapsed_ms,
                type(error).__name__,
            )
            return EditingJobResult(
                status=MemoirEditingJobStatus.FAILED,
                result_json={
                    **checkpoint,
                    "stage": "embed_semantic_units",
                    "utterance_id": utterance_id,
                    "source_text_hash": expected_hash,
                    "requested_model": embedding_model,
                    "resolved_model": None,
                    "provider": "local_embedding",
                    "timeout_seconds": getattr(client, "timeout_seconds", None),
                    "elapsed_ms": elapsed_ms,
                    "error_type": type(error).__name__,
                    "generation_requested_model": generation.requested_model,
                    "generation_resolved_model": generation.resolved_model,
                    "generation_provider": generation.provider,
                    "generation_elapsed_ms": generation.elapsed_ms,
                },
                error_message=ai_user_error_message(error, "语义索引向量化失败"),
            )
        if len(embedded.embeddings) != len(generation.units):
            raise ValueError("embedding count does not match semantic unit count")

    locked = await _load_utterance(db, utterance_id, for_update=True)
    if locked is None or locked.voice_session.project_id != job.project_id:
        return await _skip_unavailable_source(
            db,
            project_id=job.project_id,
            utterance_id=utterance_id,
            reason="utterance unavailable",
            entry_kind=MemoirRetrievalEntryKind.SEMANTIC_UNIT,
        )
    locked_text, text_source = current_utterance_text_and_source(locked)
    if not is_countable_material(locked, locked_text):
        return await _skip_unavailable_source(
            db,
            project_id=job.project_id,
            utterance_id=utterance_id,
            reason="narrator transcript unavailable",
            entry_kind=MemoirRetrievalEntryKind.SEMANTIC_UNIT,
        )
    if source_text_hash(locked_text) != expected_hash:
        return await _skip_stale_semantics(
            db,
            project_id=job.project_id,
            utterance=locked,
        )

    await db.execute(
        delete(MemoirRetrievalEntry).where(
            MemoirRetrievalEntry.project_id == job.project_id,
            MemoirRetrievalEntry.utterance_id == locked.id,
            MemoirRetrievalEntry.entry_kind
            == MemoirRetrievalEntryKind.SEMANTIC_UNIT.value,
        )
    )
    entry_ids: list[int] = []
    if generation.units and embedded is not None:
        now = utcnow_naive()
        rows = [
            {
                "project_id": job.project_id,
                "voice_session_id": locked.voice_session_id,
                "utterance_id": locked.id,
                "transcript_version_id": current_transcript_version_id(locked),
                "audio_file_id": locked.audio_file_id,
                "entry_kind": MemoirRetrievalEntryKind.SEMANTIC_UNIT.value,
                "ordinal": ordinal,
                "search_text": unit,
                "text_source": text_source,
                "source_text_hash": expected_hash,
                "embedding": list(embedding),
                "embedding_model": embedded.resolved_model,
                "index_version": SEMANTIC_INDEX_VERSION,
                "created_at": now,
                "updated_at": now,
            }
            for ordinal, (unit, embedding) in enumerate(
                zip(generation.units, embedded.embeddings, strict=True)
            )
        ]
        entry_ids = list(
            (
                await db.scalars(
                    insert(MemoirRetrievalEntry)
                    .values(rows)
                    .returning(MemoirRetrievalEntry.id)
                )
            ).all()
        )

    return EditingJobResult(
        result_json={
            "entry_ids": entry_ids,
            "utterance_id": locked.id,
            "transcript_version_id": current_transcript_version_id(locked),
            "source_text_hash": expected_hash,
            "text_source": text_source,
            "units": list(generation.units),
            "unit_count": len(generation.units),
            "decision_reason": generation.decision_reason,
            "prompt_version": generation.prompt_version,
            "requested_model": generation.requested_model,
            "resolved_model": generation.resolved_model,
            "provider": generation.provider,
            "generation_elapsed_ms": generation.elapsed_ms,
            "generation_attempt_count": generation.attempt_count,
            "generation_usage": generation.usage,
            "embedding_model": embedded.resolved_model if embedded else None,
            "embedding_dimensions": embedded.dimensions if embedded else None,
            "embedding_elapsed_ms": (
                embedded.service_elapsed_ms if embedded else 0
            ),
            "index_version": SEMANTIC_INDEX_VERSION,
        }
    )
