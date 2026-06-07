from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memoir import MemoirSourceArtifact, MemoirTranscriptVersion
from app.schemas.memoir import MemoirTranscriptVersionCreate, MemoirTranscriptVersionResponse
from app.services.memoir.access import load_answer


async def create_transcript_version(
    db: AsyncSession,
    user_id: str,
    req: MemoirTranscriptVersionCreate,
) -> MemoirTranscriptVersionResponse:
    answer = await load_answer(db, req.answer_id, user_id)
    content = req.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="转写文本不能为空")

    if req.source_artifact_id:
        artifact = await db.scalar(
            select(MemoirSourceArtifact).where(
                MemoirSourceArtifact.id == req.source_artifact_id,
                MemoirSourceArtifact.answer_id == answer.id,
            )
        )
        if not artifact:
            raise HTTPException(status_code=404, detail="原始材料不存在")

    version = MemoirTranscriptVersion(
        answer_id=answer.id,
        source_artifact_id=req.source_artifact_id,
        version_type=req.version_type.value,
        content=content,
        provider=req.provider,
        confidence=req.confidence,
        created_by=req.created_by.value,
    )
    db.add(version)
    await db.flush()
    return MemoirTranscriptVersionResponse.model_validate(version)
