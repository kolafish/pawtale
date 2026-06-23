from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir import MemoirOutputStatus, MemoirOutputType
from app.models.memoir import MemoirOutput, MemoirOutputVersion
from app.schemas.memoir_legacy import (
    MemoirOutputDetailResponse,
    MemoirOutputResponse,
)
from app.services.memoir.access import load_memoir_project


async def upsert_session_output_version(
    db: AsyncSession,
    *,
    project_id: str,
    session_id: str,
    output_type: MemoirOutputType,
    title: str,
    content: str,
    model: str | None,
    prompt_snapshot: str,
) -> MemoirOutputResponse:
    output = await db.scalar(
        select(MemoirOutput).where(
            MemoirOutput.project_id == project_id,
            MemoirOutput.source_session_id == session_id,
            MemoirOutput.output_type == output_type.value,
        )
    )
    if not output:
        output = MemoirOutput(
            project_id=project_id,
            output_type=output_type.value,
            title=title,
            source_session_id=session_id,
            status=MemoirOutputStatus.DRAFT.value,
        )
        db.add(output)
        await db.flush()
    else:
        output.title = title
        output.status = MemoirOutputStatus.DRAFT.value

    latest_version = await db.scalar(
        select(func.max(MemoirOutputVersion.version)).where(
            MemoirOutputVersion.output_id == output.id
        )
    )
    db.add(
        MemoirOutputVersion(
            output_id=output.id,
            version=(latest_version or 0) + 1,
            content=content,
            model=model,
            prompt_snapshot=prompt_snapshot,
        )
    )
    await db.flush()
    return MemoirOutputResponse.model_validate(output)


async def list_outputs(
    db: AsyncSession,
    user_id: str,
    project_id: str,
) -> list[MemoirOutputResponse]:
    await load_memoir_project(db, project_id, user_id)
    result = await db.execute(
        select(MemoirOutput)
        .where(MemoirOutput.project_id == project_id)
        .order_by(MemoirOutput.updated_at.desc(), MemoirOutput.created_at.desc())
    )
    return [MemoirOutputResponse.model_validate(output) for output in result.scalars()]


async def get_output(
    db: AsyncSession,
    user_id: str,
    project_id: str,
    output_id: str,
) -> MemoirOutputDetailResponse:
    await load_memoir_project(db, project_id, user_id)
    output = await db.scalar(
        select(MemoirOutput)
        .where(MemoirOutput.project_id == project_id, MemoirOutput.id == output_id)
        .options(selectinload(MemoirOutput.versions))
    )
    if not output:
        raise HTTPException(status_code=404, detail="回忆录输出不存在")
    return MemoirOutputDetailResponse.model_validate(output)
