from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import (
    MemoirChatContentStatus,
    MemoirChatMessageRole,
    MemoirCreatedBy,
    MemoirTranscriptPolishLevel,
    MemoirTranscriptVersionType,
)
from app.domain.projects.types import ProjectType
from app.models.memoir import MemoirChatMessage, MemoirTranscriptVersion, MemoirVoiceSession, MemoirVoiceUtterance
from app.models.projects import Project
from app.services.ai_types import AITextClient
from app.services.memoir.multi_asr_verification import (
    MultiASRVerificationRequest,
    verify_multi_asr_transcripts,
)
from app.services.memoir.dialect_profile import resolve_memoir_dialect_profile
from app.services.memoir.material_offline_asr_state import MATERIAL_OFFLINE_ASR_METADATA_KEY
from app.services.memoir.offline_asr import (
    ASRRunner,
    DEFAULT_OFFLINE_ASR_PROVIDERS,
    STEPFUN_OFFLINE_ASR_PROVIDER,
    VOLC_OFFLINE_ASR_PROVIDER,
    OfflineASRRequest,
    OfflineASRResult,
    memoir_offline_asr_glossary,
    memoir_offline_asr_hotwords,
    memoir_narrator_origin,
    normalize_offline_asr_providers,
    offline_asr_provider_name,
    run_offline_asr,
)
from app.services.memoir.material_roles import (
    is_narrator_material,
    narrator_material_clause,
)
from app.services.memoir.project_ai_preferences import (
    normalize_transcript_polish_level,
    project_transcript_polish_level,
    select_project_ai_model,
)
from app.services.memoir.transcript_projection import current_utterance_text
from app.services.memoir.utterance_editing_pipeline import (
    schedule_material_organization_for_ready_utterance,
)
from app.services.memoir.voice_transcripts import (
    MemoirTranscriptCleanupContext,
    _sync_included_transcript_edit,
    generate_cleaned_transcript_text,
    prune_transcript_versions_for_utterance,
)


BEIJING_UTC_OFFSET = timedelta(hours=8)
OFFLINE_ASR_REPROCESS_SOURCE = "offline_multi_asr_reprocess"


def parse_beijing_datetime(value: str) -> datetime:
    normalized = value.strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue
    raise ValueError("cutoff time must use YYYY-MM-DD HH:MM[:SS]")


def beijing_to_utc_naive(value: datetime) -> datetime:
    return value - BEIJING_UTC_OFFSET


def utc_naive_to_beijing_text(value: datetime | None) -> str:
    if value is None:
        return ""
    return (value + BEIJING_UTC_OFFSET).strftime("%Y-%m-%d %H:%M:%S")


async def candidate_utterances(
    db: AsyncSession,
    *,
    project_id: str,
    cutoff_created_at_utc: datetime,
    providers: Sequence[str] = DEFAULT_OFFLINE_ASR_PROVIDERS,
    force_reprocess: bool = False,
    limit: int | None = None,
) -> list[MemoirVoiceUtterance]:
    user_edited_exists = (
        select(MemoirTranscriptVersion.id)
        .where(
            MemoirTranscriptVersion.source_artifact_id == MemoirVoiceUtterance.id,
            MemoirTranscriptVersion.version_type == MemoirTranscriptVersionType.USER_EDITED.value,
        )
        .exists()
    )
    statement = (
        select(MemoirVoiceUtterance)
        .join(MemoirVoiceSession, MemoirVoiceUtterance.voice_session_id == MemoirVoiceSession.id)
        .join(Project, MemoirVoiceSession.project_id == Project.id)
        .where(
            Project.id == project_id,
            Project.type == ProjectType.MEMOIR.value,
            MemoirVoiceUtterance.created_at <= cutoff_created_at_utc,
            MemoirVoiceUtterance.audio_file_id.is_not(None),
            narrator_material_clause(),
            ~user_edited_exists,
        )
        .options(
            selectinload(MemoirVoiceUtterance.audio_file),
            selectinload(MemoirVoiceUtterance.chat_message),
            selectinload(MemoirVoiceUtterance.voice_session).selectinload(MemoirVoiceSession.messages),
            selectinload(MemoirVoiceUtterance.voice_session).selectinload(MemoirVoiceSession.project),
        )
        .order_by(MemoirVoiceUtterance.created_at.desc(), MemoirVoiceUtterance.id.desc())
    )
    rows = [
        utterance
        for utterance in (await db.execute(statement)).scalars().all()
        if is_narrator_material(utterance)
    ]
    if force_reprocess:
        return rows[: max(0, limit)] if limit is not None else rows

    requested_reprocess_providers = tuple(offline_asr_provider_name(provider) for provider in providers)
    candidates: list[MemoirVoiceUtterance] = []
    max_candidates = None if limit is None else max(0, limit)
    for utterance in rows:
        if max_candidates is not None and len(candidates) >= max_candidates:
            break
        if await _has_requested_reprocess_versions(
            db,
            utterance_id=utterance.id,
            providers=requested_reprocess_providers,
        ):
            continue
        candidates.append(utterance)
    return candidates


async def load_reprocess_utterance(db: AsyncSession, utterance_id: str) -> MemoirVoiceUtterance | None:
    return await db.scalar(
        select(MemoirVoiceUtterance)
        .where(MemoirVoiceUtterance.id == utterance_id)
        .options(
            selectinload(MemoirVoiceUtterance.audio_file),
            selectinload(MemoirVoiceUtterance.chat_message),
            selectinload(MemoirVoiceUtterance.voice_session).selectinload(MemoirVoiceSession.messages),
            selectinload(MemoirVoiceUtterance.voice_session).selectinload(MemoirVoiceSession.project),
        )
    )


async def _has_requested_reprocess_versions(
    db: AsyncSession,
    *,
    utterance_id: str,
    providers: Sequence[str],
) -> bool:
    if not providers:
        return False
    existing = set(
        (
            await db.execute(
                select(MemoirTranscriptVersion.provider).where(
                    MemoirTranscriptVersion.source_artifact_id == utterance_id,
                    MemoirTranscriptVersion.version_type == MemoirTranscriptVersionType.ASR_RAW.value,
                    MemoirTranscriptVersion.provider.in_(providers),
                )
            )
        ).scalars().all()
    )
    return all(provider in existing for provider in providers)


def _candidate_payload(utterance: MemoirVoiceUtterance) -> dict[str, object]:
    audio_file = utterance.audio_file
    return {
        "utterance_id": utterance.id,
        "voice_session_id": utterance.voice_session_id,
        "created_at_bj": utc_naive_to_beijing_text(utterance.created_at),
        "audio_file_id": utterance.audio_file_id,
        "audio_mime_type": audio_file.mime_type if audio_file else None,
        "audio_size_bytes": audio_file.size_bytes if audio_file else None,
        "estimated_seconds": round((audio_file.size_bytes if audio_file else 0) / 32000, 1),
        "current_text_length": len(current_utterance_text(utterance)),
        "is_currently_blank": not bool(current_utterance_text(utterance)),
    }


async def _latest_question_context(db: AsyncSession, utterance: MemoirVoiceUtterance) -> str:
    message = utterance.chat_message
    if message is not None and message.turn_index is not None:
        text = await db.scalar(
            select(MemoirChatMessage.content_text)
            .where(
                MemoirChatMessage.voice_session_id == utterance.voice_session_id,
                MemoirChatMessage.turn_index < message.turn_index,
                MemoirChatMessage.role.in_([
                    MemoirChatMessageRole.ASSISTANT.value,
                    MemoirChatMessageRole.INTERVIEWER.value,
                ]),
                func.trim(MemoirChatMessage.content_text) != "",
            )
            .order_by(MemoirChatMessage.turn_index.desc(), MemoirChatMessage.created_at.desc())
            .limit(1)
        )
        if text:
            return text.strip()

    return ""


def _primary_asr_result(results: Sequence[OfflineASRResult]) -> OfflineASRResult:
    for provider in (VOLC_OFFLINE_ASR_PROVIDER, STEPFUN_OFFLINE_ASR_PROVIDER):
        for result in results:
            if result.provider == provider and result.text.strip():
                return result
    for result in results:
        if result.text.strip():
            return result
    raise ValueError("no non-empty ASR result")


async def _replace_reprocess_asr_versions(
    db: AsyncSession,
    utterance: MemoirVoiceUtterance,
    results: Sequence[OfflineASRResult],
) -> dict[str, str]:
    providers = [result.provider for result in results]
    if providers:
        await db.execute(
            delete(MemoirTranscriptVersion).where(
                MemoirTranscriptVersion.source_artifact_id == utterance.id,
                MemoirTranscriptVersion.version_type == MemoirTranscriptVersionType.ASR_RAW.value,
                MemoirTranscriptVersion.provider.in_(providers),
            )
        )
    version_ids: dict[str, str] = {}
    for result in results:
        version = MemoirTranscriptVersion(
            voice_session_id=utterance.voice_session_id,
            source_artifact_id=utterance.id,
            version_type=MemoirTranscriptVersionType.ASR_RAW.value,
            content=result.text.strip(),
            provider=result.provider,
            created_by=MemoirCreatedBy.ASR.value,
        )
        db.add(version)
        await db.flush()
        version_ids[result.provider] = version.id
    return version_ids


async def _next_turn_index(db: AsyncSession, voice_session_id: str) -> int:
    value = await db.scalar(
        select(func.max(MemoirChatMessage.turn_index)).where(MemoirChatMessage.voice_session_id == voice_session_id)
    )
    return int(value or 0) + 1


async def _ensure_user_chat_message(
    db: AsyncSession,
    utterance: MemoirVoiceUtterance,
    *,
    raw_text: str,
    raw_transcript_version_id: str | None,
) -> None:
    if utterance.chat_message is not None:
        return
    voice_session = utterance.voice_session
    message = MemoirChatMessage(
        project_id=voice_session.project_id,
        voice_session_id=voice_session.id,
        role=MemoirChatMessageRole.USER.value,
        content_text=raw_text,
        content_status=MemoirChatContentStatus.FINAL.value,
        audio_file_id=utterance.audio_file_id,
        transcript_version_id=raw_transcript_version_id,
        turn_index=await _next_turn_index(db, voice_session.id),
        metadata_json={
            "source": OFFLINE_ASR_REPROCESS_SOURCE,
            "speaker_role": "narrator",
            "auto_corrected": False,
        },
    )
    db.add(message)
    await db.flush()
    utterance.chat_message_id = message.id
    utterance.chat_message = message


async def _apply_corrected_reprocess_text(
    db: AsyncSession,
    utterance: MemoirVoiceUtterance,
    *,
    content: str,
    provider: str,
    ai_model: str | None,
) -> None:
    cleaned_content = content.strip()
    if not cleaned_content:
        raise ValueError("dialect-corrected transcript is empty")

    version = MemoirTranscriptVersion(
        voice_session_id=utterance.voice_session_id,
        source_artifact_id=utterance.id,
        version_type=MemoirTranscriptVersionType.DIALECT_CORRECTED.value,
        content=cleaned_content,
        provider=provider,
        created_by=MemoirCreatedBy.AI.value,
    )
    db.add(version)
    await db.flush()

    utterance.transcript_edited = cleaned_content
    if utterance.chat_message:
        utterance.chat_message.content_text = cleaned_content
        utterance.chat_message.content_status = MemoirChatContentStatus.CORRECTED.value
        utterance.chat_message.transcript_version_id = version.id

    await prune_transcript_versions_for_utterance(
        db,
        utterance_id=utterance.id,
        version_type=MemoirTranscriptVersionType.DIALECT_CORRECTED,
    )
    await _sync_included_transcript_edit(
        db,
        utterance=utterance,
        content=cleaned_content,
        transcript_version_id=version.id,
    )
    project_id = utterance.voice_session.project_id
    await schedule_material_organization_for_ready_utterance(
        db,
        project_id=project_id,
        utterance=utterance,
        source=f"transcript_{MemoirTranscriptVersionType.DIALECT_CORRECTED.value}",
        ai_model=ai_model,
    )


def _compact_result_payload(results: Sequence[OfflineASRResult]) -> list[dict[str, object]]:
    payload: list[dict[str, object]] = []
    for result in results:
        raw = result.raw or {}
        payload.append(
            {
                "provider": result.provider,
                "text_length": len(result.text),
                "raw_provider": raw.get("provider"),
                "segment_count": raw.get("segment_count"),
            }
        )
    return payload


async def _apply_reprocessed_transcript(
    db: AsyncSession,
    utterance: MemoirVoiceUtterance,
    *,
    asr_results: Sequence[OfflineASRResult],
    corrected_text: str,
    corrected_provider: str,
    ai_model: str | None,
    recognition_metadata: dict[str, object],
) -> None:
    primary = _primary_asr_result(asr_results)
    version_ids = await _replace_reprocess_asr_versions(db, utterance, asr_results)
    primary_version_id = version_ids.get(primary.provider)
    previous_text_blank = not bool(current_utterance_text(utterance))

    utterance.transcript_raw = primary.text.strip()
    utterance.provider = primary.provider
    provider_payload = (
        dict(utterance.provider_payload_json)
        if isinstance(utterance.provider_payload_json, dict)
        else {}
    )
    if previous_text_blank:
        provider_payload.update(
            {
                "source": OFFLINE_ASR_REPROCESS_SOURCE,
                "primary_provider": primary.provider,
                "providers": _compact_result_payload(asr_results),
            }
        )
    provider_payload[MATERIAL_OFFLINE_ASR_METADATA_KEY] = recognition_metadata
    utterance.provider_payload_json = provider_payload
    await _ensure_user_chat_message(
        db,
        utterance,
        raw_text=primary.text.strip(),
        raw_transcript_version_id=primary_version_id,
    )
    await _apply_corrected_reprocess_text(
        db,
        utterance,
        content=corrected_text,
        provider=corrected_provider,
        ai_model=ai_model,
    )


async def _has_user_edited_version(db: AsyncSession, utterance_id: str) -> bool:
    return bool(
        await db.scalar(
            select(MemoirTranscriptVersion.id).where(
                MemoirTranscriptVersion.source_artifact_id == utterance_id,
                MemoirTranscriptVersion.version_type == MemoirTranscriptVersionType.USER_EDITED.value,
            )
        )
    )


async def reprocess_offline_asr_utterance(
    db: AsyncSession,
    *,
    utterance_id: str,
    ai_model: str | None = None,
    transcript_polish_level: str | None = None,
    providers: Sequence[str] | str = DEFAULT_OFFLINE_ASR_PROVIDERS,
    asr_runner: ASRRunner | None = None,
    ai_client: AITextClient | None = None,
) -> dict[str, object]:
    normalized_providers = normalize_offline_asr_providers(providers)
    utterance = await load_reprocess_utterance(db, utterance_id)
    if utterance is None:
        raise ValueError("material utterance no longer exists")
    if not is_narrator_material(utterance):
        raise ValueError("interviewer prompt is not a reprocessable material")
    if utterance.audio_file_id is None or utterance.audio_file is None:
        raise ValueError("material audio file is missing")
    if await _has_user_edited_version(db, utterance.id):
        raise ValueError("user-edited transcript cannot be overwritten")

    project = utterance.voice_session.project
    selected_ai_model = select_project_ai_model(project, ai_model)
    polish_level = (
        normalize_transcript_polish_level(transcript_polish_level)
        if transcript_polish_level is not None
        else project_transcript_polish_level(project)
    )
    baseline_raw = utterance.transcript_raw or ""
    baseline_edited = utterance.transcript_edited or ""
    question_context = await _latest_question_context(db, utterance)
    hotwords = memoir_offline_asr_hotwords(project)
    dialect_glossary = memoir_offline_asr_glossary(project)
    narrator_origin = memoir_narrator_origin(project)
    dialect_profile = resolve_memoir_dialect_profile(narrator_origin)
    await db.commit()

    runner = asr_runner or run_offline_asr
    asr_results = await runner(
        OfflineASRRequest(
            utterance=utterance,
            providers=normalized_providers,
            hotwords=hotwords,
        )
    )
    if not asr_results:
        raise ValueError("all ASR providers returned empty text")

    verified = await verify_multi_asr_transcripts(
        MultiASRVerificationRequest(
            asr_results=tuple(asr_results),
            question_context=question_context,
            original_raw=baseline_raw,
            original_edited=baseline_edited,
            narrator_origin=narrator_origin,
            dialect_glossary=dialect_glossary,
        ),
        ai_model=selected_ai_model,
        ai_client=ai_client,
    )
    corrected_text = verified.text
    corrected_provider = verified.provider
    polish_provider: str | None = None
    if polish_level != MemoirTranscriptPolishLevel.LOW:
        polished = await generate_cleaned_transcript_text(
            verified.text,
            ai_model=selected_ai_model,
            ai_client=ai_client,
            context=MemoirTranscriptCleanupContext(
                scene="offline_multi_asr",
                dialect_glossary=tuple(
                    f"{entry.source} => {entry.target}" for entry in dialect_glossary
                ),
                recent_assistant_messages=(question_context,) if question_context else (),
            ),
            transcript_polish_level=polish_level,
        )
        corrected_text = polished.content
        corrected_provider = polished.provider or verified.provider
        polish_provider = polished.provider
    db.expunge_all()
    utterance = await load_reprocess_utterance(db, utterance_id)
    if utterance is None:
        raise ValueError("material utterance no longer exists")
    if await _has_user_edited_version(db, utterance.id):
        raise ValueError("user-edited transcript cannot be overwritten")
    if (utterance.transcript_raw or "") != baseline_raw or (utterance.transcript_edited or "") != baseline_edited:
        raise ValueError("material transcript changed while offline ASR was running")

    applied_at = datetime.now(UTC).isoformat()
    recognition_metadata: dict[str, object] = {
        **dialect_profile.metadata(),
        "providers": list(normalized_providers),
        "asr_providers": [result.provider for result in asr_results],
        "verification_provider": verified.provider,
        "verification_requested_model": verified.requested_model,
        "verification_resolved_model": verified.resolved_model,
        "verification_response_model": verified.response_model,
        "verification_model_mismatch": bool(
            verified.resolved_model
            and verified.response_model
            and verified.resolved_model != verified.response_model
        ),
        "transcript_polish_level": polish_level.value,
        "polish_provider": polish_provider,
        "applied_at": applied_at,
    }
    await _apply_reprocessed_transcript(
        db,
        utterance,
        asr_results=asr_results,
        corrected_text=corrected_text,
        corrected_provider=corrected_provider,
        ai_model=selected_ai_model,
        recognition_metadata=recognition_metadata,
    )
    await db.flush()
    return {
        "utterance_id": utterance.id,
        **recognition_metadata,
        "corrected_text_length": len(corrected_text),
    }


async def reprocess_offline_asr(
    db: AsyncSession,
    *,
    project_id: str,
    cutoff_created_at_utc: datetime,
    dry_run: bool = True,
    limit: int | None = None,
    ai_model: str | None = None,
    providers: Sequence[str] | str = DEFAULT_OFFLINE_ASR_PROVIDERS,
    force_reprocess: bool = False,
    asr_runner: ASRRunner | None = None,
    ai_client: AITextClient | None = None,
) -> dict[str, object]:
    normalized_providers = normalize_offline_asr_providers(providers)
    candidates = await candidate_utterances(
        db,
        project_id=project_id,
        cutoff_created_at_utc=cutoff_created_at_utc,
        providers=normalized_providers,
        force_reprocess=force_reprocess,
        limit=limit,
    )
    candidate_ids = [utterance.id for utterance in candidates]
    candidate_payloads = [_candidate_payload(utterance) for utterance in candidates]
    result: dict[str, object] = {
        "dry_run": dry_run,
        "project_id": project_id,
        "cutoff_created_at_utc": cutoff_created_at_utc.isoformat(sep=" "),
        "cutoff_created_at_bj": utc_naive_to_beijing_text(cutoff_created_at_utc),
        "providers": list(normalized_providers),
        "force_reprocess": force_reprocess,
        "candidate_count": len(candidates),
        "processed_count": 0,
        "failed_count": 0,
        "failures": [],
        "candidates": candidate_payloads,
    }
    if dry_run:
        return result

    failures: list[dict[str, str]] = []
    processed_count = 0
    for utterance_id in candidate_ids:
        try:
            await reprocess_offline_asr_utterance(
                db,
                utterance_id=utterance_id,
                ai_model=ai_model,
                providers=normalized_providers,
                asr_runner=asr_runner,
                ai_client=ai_client,
            )
            await db.commit()
            processed_count += 1
        except Exception as exc:
            await db.rollback()
            db.expunge_all()
            failures.append({"utterance_id": utterance_id, "error": str(exc)})

    result["processed_count"] = processed_count
    result["failed_count"] = len(failures)
    result["failures"] = failures
    return result
