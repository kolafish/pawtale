from __future__ import annotations

from collections.abc import Sequence

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import MemoirEditingJobStatus, MemoirEditingJobType
from app.models.memoir import MemoirEditingJob, MemoirVoiceUtterance
from app.schemas.memoir_voice import MemoirMaterialOfflineASRStateResponse
from app.services.memoir.dialect_profile import resolve_memoir_dialect_profile
from app.services.memoir.editing_jobs import EditingJobResult, enqueue_editing_job
from app.services.memoir.material_offline_asr_state import (
    material_offline_asr_job_dedupe_key,
    material_offline_asr_states,
    user_edited_utterance_ids,
)
from app.services.memoir.offline_asr import (
    DEFAULT_OFFLINE_ASR_PROVIDERS,
    memoir_narrator_origin,
    normalize_offline_asr_providers,
)
from app.services.memoir.offline_asr_reprocessing import (
    load_reprocess_utterance,
    reprocess_offline_asr_utterance,
)
from app.services.memoir.material_roles import is_narrator_material
from app.services.memoir.project_ai_preferences import select_project_ai_model


async def _load_owned_material(
    db: AsyncSession,
    *,
    utterance_id: str,
    user_id: str,
    allow_admin: bool,
) -> MemoirVoiceUtterance:
    utterance = await load_reprocess_utterance(db, utterance_id)
    if utterance is None:
        raise HTTPException(status_code=404, detail="素材不存在")
    project = utterance.voice_session.project
    if not allow_admin and project.user_id != user_id:
        raise HTTPException(status_code=404, detail="素材不存在")
    if not is_narrator_material(utterance):
        raise HTTPException(status_code=400, detail="采访者提问不支持离线增强识别")
    if utterance.audio_file_id is None or utterance.audio_file is None:
        raise HTTPException(status_code=400, detail="这条素材没有可重新识别的音频")
    if utterance.id in await user_edited_utterance_ids(db, [utterance.id]):
        raise HTTPException(status_code=409, detail="这条素材已经人工编辑，为避免覆盖不能自动重识别")
    return utterance


async def enqueue_material_offline_asr(
    db: AsyncSession,
    *,
    utterance_id: str,
    user_id: str,
    ai_model: str | None = None,
    providers: Sequence[str] | str = DEFAULT_OFFLINE_ASR_PROVIDERS,
    allow_admin: bool = False,
) -> MemoirMaterialOfflineASRStateResponse:
    utterance = await _load_owned_material(
        db,
        utterance_id=utterance_id,
        user_id=user_id,
        allow_admin=allow_admin,
    )
    normalized_providers = normalize_offline_asr_providers(providers)
    project = utterance.voice_session.project
    selected_ai_model = select_project_ai_model(project, ai_model)
    dialect_profile = resolve_memoir_dialect_profile(memoir_narrator_origin(project))
    await enqueue_editing_job(
        db,
        project_id=project.id,
        job_type=MemoirEditingJobType.REPROCESS_MATERIAL_OFFLINE_ASR,
        payload_json={
            "utterance_id": utterance.id,
            "providers": list(normalized_providers),
            "ai_model": selected_ai_model,
            "source": "user_material_offline_asr",
            **dialect_profile.metadata(),
        },
        dedupe_key=material_offline_asr_job_dedupe_key(utterance.id),
        replace_completed=True,
    )
    await db.commit()
    states = await material_offline_asr_states(db, [utterance])
    return states[utterance.id]


async def run_material_offline_asr_job(
    db: AsyncSession,
    job: MemoirEditingJob,
) -> EditingJobResult:
    payload = job.payload_json if isinstance(job.payload_json, dict) else {}
    utterance_id = str(payload.get("utterance_id") or "").strip()
    if not utterance_id:
        return EditingJobResult(
            status=MemoirEditingJobStatus.FAILED,
            error_message="material offline ASR job is missing utterance_id",
        )
    providers = payload.get("providers")
    result = await reprocess_offline_asr_utterance(
        db,
        utterance_id=utterance_id,
        providers=providers if isinstance(providers, list) else DEFAULT_OFFLINE_ASR_PROVIDERS,
        ai_model=str(payload.get("ai_model") or "").strip() or None,
    )
    return EditingJobResult(result_json=result)
