from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memoir import MemoirSourceArtifact
from app.schemas.memoir import MemoirSourceArtifactCreate, MemoirSourceArtifactResponse
from app.services.memoir.access import load_answer, load_memoir_project, load_session


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
        answer = await load_answer(db, req.answer_id, user_id)
        if answer.question.session.project_id != req.project_id:
            raise HTTPException(status_code=400, detail="回答不属于该回忆录项目")
        if req.session_id and answer.question.session_id != req.session_id:
            raise HTTPException(status_code=400, detail="回答不属于该访谈")

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
