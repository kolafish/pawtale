from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.projects.types import ProjectType
from app.models.memoir import AudioFile
from app.models.projects import Project


async def load_memoir_project(db: AsyncSession, project_id: str, user_id: str) -> Project:
    result = await db.execute(
        select(Project).where(Project.id == project_id, Project.user_id == user_id)
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    if project.type != ProjectType.MEMOIR.value:
        raise HTTPException(status_code=400, detail="项目不是回忆录")
    return project


async def load_audio_file(db: AsyncSession, audio_file_id: str, user_id: str) -> AudioFile:
    result = await db.execute(
        select(AudioFile).where(AudioFile.id == audio_file_id, AudioFile.user_id == user_id)
    )
    audio_file = result.scalar_one_or_none()
    if not audio_file:
        raise HTTPException(status_code=404, detail="音频文件不存在")
    return audio_file
