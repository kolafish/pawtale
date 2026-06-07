from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memoir import MemoirAnswer, MemoirQuestion, MemoirSession, MemoirSourceArtifact
from app.schemas.memoir import MemoirSourceArtifactCreate, MemoirSourceArtifactResponse
from app.services.memoir.access import load_answer, load_memoir_project, load_session


async def _answer_project_id(db: AsyncSession, answer_id: str) -> str | None:
    return await db.scalar(
        select(MemoirSession.project_id)
        .join(MemoirQuestion, MemoirQuestion.session_id == MemoirSession.id)
        .join(MemoirAnswer, MemoirAnswer.question_id == MemoirQuestion.id)
        .where(MemoirAnswer.id == answer_id)
    )


async def create_source_artifact(
    db: AsyncSession,
    user_id: str,
    req: MemoirSourceArtifactCreate,
) -> MemoirSourceArtifactResponse:
    await load_memoir_project(db, req.project_id, user_id)

    if req.session_id:
        session = await load_session(db, req.session_id, user_id)
        if session.project_id != req.project_id:
            raise HTTPException(status_code=400, detail="访谈不属于该回忆录项目")

    if req.answer_id:
        await load_answer(db, req.answer_id, user_id)
        answer_project_id = await _answer_project_id(db, req.answer_id)
        if answer_project_id != req.project_id:
            raise HTTPException(status_code=400, detail="回答不属于该回忆录项目")

    artifact = MemoirSourceArtifact(
        project_id=req.project_id,
        session_id=req.session_id,
        answer_id=req.answer_id,
        artifact_type=req.artifact_type.value,
        storage_path=req.storage_path,
        mime_type=req.mime_type,
        size_bytes=req.size_bytes,
        metadata_json=req.metadata_json,
    )
    db.add(artifact)
    await db.flush()
    return MemoirSourceArtifactResponse.model_validate(artifact)
