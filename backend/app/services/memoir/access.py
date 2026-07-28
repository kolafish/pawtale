from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application_errors import InvalidRequestError, ResourceNotFoundError
from app.domain.projects.types import ProjectType
from app.models.memoir import AudioFile
from app.models.projects import Project


async def load_memoir_project(
    db: AsyncSession,
    project_id: str,
    user_id: str,
    *,
    allow_admin: bool = False,
) -> Project:
    filters = [Project.id == project_id]
    if not allow_admin:
        filters.append(Project.user_id == user_id)
    result = await db.execute(
        select(Project).where(*filters)
    )
    project = result.scalar_one_or_none()
    if not project:
        raise ResourceNotFoundError("项目不存在")
    if project.type != ProjectType.MEMOIR.value:
        raise InvalidRequestError("项目不是回忆录")
    return project


async def load_audio_file(
    db: AsyncSession,
    audio_file_id: str,
    user_id: str,
    *,
    allow_admin: bool = False,
) -> AudioFile:
    filters = [AudioFile.id == audio_file_id]
    if not allow_admin:
        filters.append(AudioFile.user_id == user_id)
    result = await db.execute(
        select(AudioFile).where(*filters)
    )
    audio_file = result.scalar_one_or_none()
    if not audio_file:
        raise ResourceNotFoundError("音频文件不存在")
    return audio_file
