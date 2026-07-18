from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import MemoirEditingJobType
from app.models.memoir import (
    MemoirChatMessage,
    MemoirEditingJob,
    MemoirSectionMaterial,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.services.memoir import editing_jobs, sections, source_cards
from app.services.memoir.narrative import ingestion_jobs as narrative_ingestion
from app.services.memoir.source_disposition import is_ignored_non_memoir_source


ROUTE_PAYLOAD_TRANSCRIPT_VERSION_IDS_KEY = "transcript_version_ids"
ROUTE_PAYLOAD_SOURCE_TEXT_HASHES_KEY = "source_text_hashes"
ROUTE_PAYLOAD_SOURCE_TEXT_LENGTHS_KEY = "source_text_lengths"
ROUTE_LINEAGE_EDITING_JOB_ID_KEY = "editing_job_id"
ROUTE_LINEAGE_PIPELINE_SOURCE_KEY = "pipeline_source"
ROUTE_LINEAGE_TRANSCRIPT_VERSION_ID_KEY = "transcript_version_id"
ROUTE_LINEAGE_SOURCE_TEXT_HASH_KEY = "source_text_hash"
ROUTE_LINEAGE_SOURCE_TEXT_LENGTH_KEY = "source_text_length"


@dataclass(frozen=True)
class SourceRoutingTranscriptGuard:
    ok: bool
    error_message: str = ""
    stale_utterance_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class TranscriptMutationGuard:
    source_text_hash: str
    transcript_version_id: str | None


@dataclass(frozen=True)
class SourceProcessingJobs:
    source_card_job: MemoirEditingJob | None
    route_job: MemoirEditingJob | None
    narrative_job: MemoirEditingJob | None


def current_utterance_text(utterance: MemoirVoiceUtterance) -> str:
    return (utterance.transcript_edited or "").strip() or (utterance.transcript_raw or "").strip()


def current_transcript_version_id(utterance: MemoirVoiceUtterance) -> str | None:
    return utterance.chat_message.transcript_version_id if utterance.chat_message else None


def source_text_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def transcript_mutation_guard(utterance: MemoirVoiceUtterance) -> TranscriptMutationGuard:
    return TranscriptMutationGuard(
        source_text_hash=source_text_hash(current_utterance_text(utterance)),
        transcript_version_id=current_transcript_version_id(utterance),
    )


async def claim_transcript_mutation_guard(
    db: AsyncSession,
    *,
    utterance: MemoirVoiceUtterance,
    expected: TranscriptMutationGuard,
) -> bool:
    if transcript_mutation_guard(utterance) != expected:
        return False
    message = utterance.chat_message
    if message is None:
        return False
    if source_text_hash(message.content_text) != expected.source_text_hash:
        return False

    version_filter = (
        MemoirChatMessage.transcript_version_id.is_(None)
        if expected.transcript_version_id is None
        else MemoirChatMessage.transcript_version_id == expected.transcript_version_id
    )
    result = await db.execute(
        update(MemoirChatMessage)
        .where(
            MemoirChatMessage.id == message.id,
            MemoirChatMessage.content_text == message.content_text,
            version_filter,
        )
        .values(
            content_text=MemoirChatMessage.content_text,
            transcript_version_id=MemoirChatMessage.transcript_version_id,
            updated_at=MemoirChatMessage.updated_at,
        )
    )
    return result.rowcount == 1


async def schedule_source_routing_for_ready_utterance(
    db: AsyncSession,
    *,
    project_id: str,
    utterance: MemoirVoiceUtterance,
    source: str,
    ai_model: str | None = None,
    replace_completed: bool = True,
) -> MemoirEditingJob | None:
    text = current_utterance_text(utterance)
    if not text:
        return None
    if is_ignored_non_memoir_source(utterance):
        return None
    text_hash = source_text_hash(text)
    transcript_version_id = current_transcript_version_id(utterance)
    payload_json: dict[str, object] = {
        "utterance_ids": [utterance.id],
        "voice_session_id": utterance.voice_session_id,
        "source": source,
        ROUTE_PAYLOAD_TRANSCRIPT_VERSION_IDS_KEY: {
            utterance.id: transcript_version_id,
        },
        ROUTE_PAYLOAD_SOURCE_TEXT_HASHES_KEY: {
            utterance.id: text_hash,
        },
        ROUTE_PAYLOAD_SOURCE_TEXT_LENGTHS_KEY: {
            utterance.id: len(text),
        },
    }
    if ai_model:
        payload_json["ai_model"] = ai_model
    return await editing_jobs.enqueue_editing_job(
        db,
        project_id=project_id,
        job_type=MemoirEditingJobType.ROUTE_SOURCES,
        payload_json=payload_json,
        dedupe_key=f"route_source:{utterance.id}",
        replace_completed=replace_completed,
    )


async def schedule_source_processing_for_ready_utterance(
    db: AsyncSession,
    *,
    project_id: str,
    utterance: MemoirVoiceUtterance,
    source: str,
    ai_model: str | None = None,
    include_routing: bool = True,
) -> SourceProcessingJobs:
    if not current_utterance_text(utterance) or is_ignored_non_memoir_source(utterance):
        return SourceProcessingJobs(source_card_job=None, route_job=None, narrative_job=None)
    already_layered = await db.scalar(
        select(MemoirSectionMaterial.id).where(
            MemoirSectionMaterial.project_id == project_id,
            MemoirSectionMaterial.utterance_id == utterance.id,
        )
    )
    if utterance.completion_issue_id or already_layered is not None:
        transcript_version_id = current_transcript_version_id(utterance)
        if already_layered is not None and transcript_version_id:
            await sections.refresh_material_transcript_version(
                db,
                project_id=project_id,
                utterance_id=utterance.id,
                transcript_version_id=transcript_version_id,
            )
        return SourceProcessingJobs(
            source_card_job=None,
            route_job=None,
            narrative_job=None,
        )
    source_card_job = await source_cards.schedule_source_card_generation_job(
        db,
        project_id=project_id,
        utterance_id=utterance.id,
        ai_model=ai_model,
        reason=source,
    )
    route_job = (
        await schedule_source_routing_for_ready_utterance(
            db,
            project_id=project_id,
            utterance=utterance,
            source=source,
            ai_model=ai_model,
        )
        if include_routing
        else None
    )
    narrative_job = (
        await narrative_ingestion.schedule_narrative_ingestion_job(
            db,
            project_id=project_id,
            utterance=utterance,
            source=source,
            ai_model=ai_model,
        )
        if not include_routing
        else None
    )
    return SourceProcessingJobs(
        source_card_job=source_card_job,
        route_job=route_job,
        narrative_job=narrative_job,
    )


async def validate_source_routing_transcript_guard(
    db: AsyncSession,
    *,
    project_id: str,
    payload_json: dict,
    utterance_ids: list[str],
) -> SourceRoutingTranscriptGuard:
    expected_versions = payload_json.get(ROUTE_PAYLOAD_TRANSCRIPT_VERSION_IDS_KEY)
    expected_hashes = payload_json.get(ROUTE_PAYLOAD_SOURCE_TEXT_HASHES_KEY)
    if not isinstance(expected_versions, dict) or not isinstance(expected_hashes, dict):
        return SourceRoutingTranscriptGuard(
            ok=False,
            error_message="source routing job missing transcript guard",
        )

    result = await db.execute(
        select(MemoirVoiceUtterance)
        .join(MemoirVoiceSession, MemoirVoiceUtterance.voice_session_id == MemoirVoiceSession.id)
        .where(
            MemoirVoiceSession.project_id == project_id,
            MemoirVoiceUtterance.id.in_(utterance_ids),
        )
        .options(selectinload(MemoirVoiceUtterance.chat_message))
    )
    utterances = {utterance.id: utterance for utterance in result.scalars().all()}
    stale_ids: list[str] = []
    for utterance_id in utterance_ids:
        utterance = utterances.get(utterance_id)
        if utterance is None:
            stale_ids.append(utterance_id)
            continue
        expected_version_id = expected_versions.get(utterance_id)
        if current_transcript_version_id(utterance) != expected_version_id:
            stale_ids.append(utterance_id)
            continue
        expected_hash = expected_hashes.get(utterance_id)
        if source_text_hash(current_utterance_text(utterance)) != expected_hash:
            stale_ids.append(utterance_id)
    if stale_ids:
        return SourceRoutingTranscriptGuard(
            ok=False,
            error_message="transcript changed before source routing",
            stale_utterance_ids=tuple(stale_ids),
        )
    return SourceRoutingTranscriptGuard(ok=True)


def source_routing_lineage_by_utterance(
    payload_json: dict,
    *,
    utterance_ids: list[str],
    editing_job_id: str,
) -> dict[str, dict[str, object]]:
    versions = payload_json.get(ROUTE_PAYLOAD_TRANSCRIPT_VERSION_IDS_KEY)
    hashes = payload_json.get(ROUTE_PAYLOAD_SOURCE_TEXT_HASHES_KEY)
    lengths = payload_json.get(ROUTE_PAYLOAD_SOURCE_TEXT_LENGTHS_KEY)
    pipeline_source = payload_json.get("source")
    return {
        utterance_id: {
            ROUTE_LINEAGE_EDITING_JOB_ID_KEY: editing_job_id,
            ROUTE_LINEAGE_PIPELINE_SOURCE_KEY: pipeline_source if isinstance(pipeline_source, str) else "",
            ROUTE_LINEAGE_TRANSCRIPT_VERSION_ID_KEY: versions.get(utterance_id) if isinstance(versions, dict) else None,
            ROUTE_LINEAGE_SOURCE_TEXT_HASH_KEY: hashes.get(utterance_id) if isinstance(hashes, dict) else None,
            ROUTE_LINEAGE_SOURCE_TEXT_LENGTH_KEY: lengths.get(utterance_id) if isinstance(lengths, dict) else None,
        }
        for utterance_id in utterance_ids
    }
