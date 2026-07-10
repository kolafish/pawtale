from __future__ import annotations

from datetime import datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.memoir_material_editor import (
    MemoirMaterialItemResponse,
    MemoirMaterialListResponse,
)
from app.schemas.memoir_voice import (
    MemoirVoiceTranscriptItemResponse,
    MemoirVoiceTranscriptUpdateRequest,
)
from app.services.memoir.access import load_memoir_project
from app.services.memoir import voice_transcripts


CorrectionStatus = Literal["all", "corrected", "uncorrected"]
MaterialSort = Literal["chronological", "recent"]


def _material_item(
    item: MemoirVoiceTranscriptItemResponse,
    manual_corrected_at: datetime | None,
) -> MemoirMaterialItemResponse:
    return MemoirMaterialItemResponse.model_validate(
        {
            **item.model_dump(),
            "has_manual_correction": manual_corrected_at is not None,
            "manual_corrected_at": manual_corrected_at,
        }
    )


async def list_materials(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    search_query: str | None = None,
    correction_status: CorrectionStatus = "all",
    voice_session_id: str | None = None,
    sort: MaterialSort = "chronological",
    limit: int = 50,
    offset: int = 0,
    allow_admin: bool = False,
) -> MemoirMaterialListResponse:
    await load_memoir_project(db, project_id, user_id, allow_admin=allow_admin)
    manual_corrected = {
        "all": None,
        "corrected": True,
        "uncorrected": False,
    }[correction_status]
    result = await voice_transcripts.list_voice_transcripts(
        db,
        user_id,
        project_id=project_id,
        search_query=search_query,
        voice_session_id=voice_session_id,
        manual_corrected=manual_corrected,
        narrator_only=True,
        oldest_first=sort == "chronological",
        interview_time_order=True,
        limit=limit,
        offset=offset,
        allow_admin=allow_admin,
    )
    manual_corrections = await voice_transcripts.manual_corrections_by_utterance_ids(
        db,
        [item.id for item in result.items],
    )
    return MemoirMaterialListResponse(
        items=[_material_item(item, manual_corrections.get(item.id)) for item in result.items],
        total=result.total,
        limit=result.limit,
        offset=result.offset,
    )


async def get_material(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    utterance_id: str,
    allow_admin: bool = False,
) -> MemoirMaterialItemResponse:
    await load_memoir_project(db, project_id, user_id, allow_admin=allow_admin)
    item = await voice_transcripts.get_voice_transcript(
        db,
        utterance_id,
        user_id,
        allow_admin=allow_admin,
        project_id=project_id,
        narrator_only=True,
    )
    manual_corrections = await voice_transcripts.manual_corrections_by_utterance_ids(db, [item.id])
    return _material_item(item, manual_corrections.get(item.id))


async def update_material_transcript(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    utterance_id: str,
    req: MemoirVoiceTranscriptUpdateRequest,
    allow_admin: bool = False,
) -> MemoirMaterialItemResponse:
    await load_memoir_project(db, project_id, user_id, allow_admin=allow_admin)
    item = await voice_transcripts.update_voice_transcript(
        db,
        utterance_id,
        user_id,
        req,
        allow_admin=allow_admin,
        project_id=project_id,
        narrator_only=True,
    )
    manual_corrections = await voice_transcripts.manual_corrections_by_utterance_ids(db, [item.id])
    return _material_item(item, manual_corrections.get(item.id))
