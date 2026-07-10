from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import (
    MemoirEditingJobStatus,
    MemoirEditingJobType,
    MemoirTranscriptVersionType,
)
from app.models.memoir import MemoirEditingJob, MemoirTranscriptVersion, MemoirVoiceUtterance
from app.schemas.memoir_voice import MemoirMaterialOfflineASRStateResponse
from app.services.memoir.dialect_profile import MemoirDialectProfile, resolve_memoir_dialect_profile
from app.services.memoir.source_disposition import INTERVIEWER_SOURCE_MATERIAL_TYPE


MATERIAL_OFFLINE_ASR_METADATA_KEY = "material_offline_asr"
MATERIAL_OFFLINE_ASR_JOB_DEDUPE_PREFIX = "material-offline-asr"
MATERIAL_OFFLINE_ASR_PROVIDERS = ("volc", "stepfun")
_ACTIVE_JOB_STATUSES = {
    MemoirEditingJobStatus.PENDING.value,
    MemoirEditingJobStatus.RUNNING.value,
}


def material_offline_asr_job_dedupe_key(utterance_id: str) -> str:
    return f"{MATERIAL_OFFLINE_ASR_JOB_DEDUPE_PREFIX}:{utterance_id}"


def material_offline_asr_metadata(utterance: MemoirVoiceUtterance) -> dict[str, Any]:
    payload = utterance.provider_payload_json if isinstance(utterance.provider_payload_json, dict) else {}
    metadata = payload.get(MATERIAL_OFFLINE_ASR_METADATA_KEY)
    return dict(metadata) if isinstance(metadata, dict) else {}


def _narrator_origin(utterance: MemoirVoiceUtterance) -> str:
    project = utterance.voice_session.project
    metadata = project.metadata_json if isinstance(project.metadata_json, dict) else {}
    profile = metadata.get("memoir_profile")
    if not isinstance(profile, dict):
        return ""
    return str(profile.get("narrator_origin") or "").strip()


async def user_edited_utterance_ids(
    db: AsyncSession,
    utterance_ids: Sequence[str],
) -> set[str]:
    clean_ids = list(dict.fromkeys(item for item in utterance_ids if item))
    if not clean_ids:
        return set()
    ids = (
        await db.scalars(
            select(MemoirTranscriptVersion.source_artifact_id).where(
                MemoirTranscriptVersion.source_artifact_id.in_(clean_ids),
                MemoirTranscriptVersion.version_type == MemoirTranscriptVersionType.USER_EDITED.value,
            )
        )
    ).all()
    return {item for item in ids if item}


def _job_status(job: MemoirEditingJob | None) -> str:
    if job is None:
        return "idle"
    if job.status in {
        MemoirEditingJobStatus.PENDING.value,
        MemoirEditingJobStatus.RUNNING.value,
        MemoirEditingJobStatus.SUCCEEDED.value,
        MemoirEditingJobStatus.FAILED.value,
    }:
        return job.status
    return "failed"


def _profile_from_metadata(metadata: dict[str, Any]) -> MemoirDialectProfile | None:
    origin = str(metadata.get("narrator_origin") or "").strip()
    code = str(metadata.get("dialect_code") or "").strip()
    label = str(metadata.get("dialect_label") or "").strip()
    strategy = str(metadata.get("dialect_strategy") or "none").strip() or "none"
    enabled = metadata.get("dialect_enhancement_enabled") is True
    if not any((origin, code, label, enabled)):
        return None
    return MemoirDialectProfile(
        narrator_origin=origin,
        dialect_code=code,
        dialect_label=label,
        strategy=strategy,
        enhancement_enabled=enabled,
    )


def _state_profile(
    utterance: MemoirVoiceUtterance,
    job: MemoirEditingJob | None,
    applied: dict[str, Any],
) -> MemoirDialectProfile:
    if job and job.status in _ACTIVE_JOB_STATUSES | {MemoirEditingJobStatus.FAILED.value}:
        payload = job.payload_json if isinstance(job.payload_json, dict) else {}
        profile = _profile_from_metadata(payload)
        if profile:
            return profile
    profile = _profile_from_metadata(applied)
    if profile:
        return profile
    return resolve_memoir_dialect_profile(_narrator_origin(utterance))


def _providers(job: MemoirEditingJob | None, applied: dict[str, Any]) -> list[str]:
    candidates: object = None
    if job:
        source = job.result_json if job.status == MemoirEditingJobStatus.SUCCEEDED.value else job.payload_json
        if isinstance(source, dict):
            candidates = source.get("providers")
    if candidates is None:
        candidates = applied.get("providers")
    if isinstance(candidates, list):
        values = [item.strip() for item in candidates if isinstance(item, str) and item.strip()]
        if values:
            return list(dict.fromkeys(values))
    return list(MATERIAL_OFFLINE_ASR_PROVIDERS)


def _last_applied_at(applied: dict[str, Any]) -> datetime | None:
    value = applied.get("applied_at")
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _public_error_message(job: MemoirEditingJob | None) -> str:
    if not job or job.status != MemoirEditingJobStatus.FAILED.value:
        return ""
    message = str(job.error_message or "")
    if "API key" in message or "credentials are not configured" in message:
        return "离线识别服务尚未配置完整"
    if "audio file" in message.lower() or "音频" in message:
        return "素材音频不可用，无法重新识别"
    if "user-edited" in message:
        return "这条素材已经人工编辑，未覆盖人工内容"
    if "changed while offline ASR" in message:
        return "素材文字在识别期间已更新，本次没有覆盖新内容"
    return "离线识别失败，请稍后重试"


def _blocked_reason(
    utterance: MemoirVoiceUtterance,
    *,
    has_user_edit: bool,
    status: str,
) -> str:
    if status in {MemoirEditingJobStatus.PENDING.value, MemoirEditingJobStatus.RUNNING.value}:
        return "离线识别正在处理中"
    if utterance.source_material_type == INTERVIEWER_SOURCE_MATERIAL_TYPE:
        return "采访者提问不是正文素材"
    if utterance.audio_file_id is None or utterance.audio_file is None:
        return "这条素材没有可重新识别的音频"
    if has_user_edit:
        return "这条素材已经人工编辑，为避免覆盖不能自动重识别"
    return ""


async def material_offline_asr_states(
    db: AsyncSession,
    utterances: Sequence[MemoirVoiceUtterance],
) -> dict[str, MemoirMaterialOfflineASRStateResponse]:
    items = list(utterances)
    if not items:
        return {}
    utterance_ids = [item.id for item in items]
    dedupe_keys = [material_offline_asr_job_dedupe_key(item) for item in utterance_ids]
    jobs = (
        await db.scalars(
            select(MemoirEditingJob).where(
                MemoirEditingJob.job_type == MemoirEditingJobType.REPROCESS_MATERIAL_OFFLINE_ASR.value,
                MemoirEditingJob.dedupe_key.in_(dedupe_keys),
            )
        )
    ).all()
    jobs_by_key = {job.dedupe_key: job for job in jobs if job.dedupe_key}
    edited_ids = await user_edited_utterance_ids(db, utterance_ids)
    states: dict[str, MemoirMaterialOfflineASRStateResponse] = {}
    for utterance in items:
        job = jobs_by_key.get(material_offline_asr_job_dedupe_key(utterance.id))
        status = _job_status(job)
        applied = material_offline_asr_metadata(utterance)
        profile = _state_profile(utterance, job, applied)
        blocked_reason = _blocked_reason(
            utterance,
            has_user_edit=utterance.id in edited_ids,
            status=status,
        )
        states[utterance.id] = MemoirMaterialOfflineASRStateResponse(
            status=status,
            job_id=job.id if job else None,
            can_start=not blocked_reason,
            blocked_reason=blocked_reason,
            providers=_providers(job, applied),
            narrator_origin=profile.narrator_origin,
            dialect_code=profile.dialect_code,
            dialect_label=profile.dialect_label,
            dialect_strategy=profile.strategy,
            dialect_enhancement_enabled=profile.enhancement_enabled,
            last_applied_at=_last_applied_at(applied),
            error_message=_public_error_message(job),
        )
    return states
