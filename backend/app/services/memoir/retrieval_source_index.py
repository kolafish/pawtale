from __future__ import annotations

from typing import Protocol

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
from app.services.embeddings import (
    EmbeddingBatchResult,
    create_embedding_client,
)
from app.services.memoir import editing_jobs
from app.services.memoir.editing_jobs import EditingJobResult
from app.services.memoir.material_roles import is_countable_material
from app.services.memoir.transcript_projection import (
    current_transcript_version_id,
    current_utterance_text_and_source,
    source_text_hash,
)


SOURCE_INDEX_VERSION = 1
SOURCE_ENTRY_ORDINAL = 0


class EmbeddingClient(Protocol):
    async def embed(self, texts: list[str]) -> EmbeddingBatchResult: ...


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
) -> EditingJobResult:
    await db.execute(
        delete(MemoirRetrievalEntry).where(
            MemoirRetrievalEntry.project_id == project_id,
            MemoirRetrievalEntry.utterance_id == utterance_id,
            MemoirRetrievalEntry.entry_kind
            == MemoirRetrievalEntryKind.SOURCE_TEXT.value,
        )
    )
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
        }
    )
