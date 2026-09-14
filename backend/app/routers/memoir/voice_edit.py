from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.users import User
from app.schemas.memoir_voice_edit import VoiceEditApplyRequest, VoiceEditApplyResponse, VoiceEditPreviewRequest, VoiceEditPreviewResponse
from app.services.memoir.voice_edit import apply_voice_edit, preview_voice_edit

router = APIRouter(tags=["memoir-voice-edit"])


@router.post("/projects/{project_id}/voice-edit/preview", response_model=VoiceEditPreviewResponse)
async def preview(project_id: str, req: VoiceEditPreviewRequest, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return await preview_voice_edit(db, user_id=user.id, project_id=project_id, req=req)


@router.post("/projects/{project_id}/voice-edit/apply", response_model=VoiceEditApplyResponse)
async def apply(project_id: str, req: VoiceEditApplyRequest, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return await apply_voice_edit(db, user_id=user.id, project_id=project_id, req=req)
