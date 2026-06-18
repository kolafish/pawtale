import logging
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import MemoirChatMessageRole
from app.domain.projects.templates import initialize_project_template
from app.domain.projects.types import ProjectType
from app.models.memoir import (
    AudioFile,
    MemoirAnswer,
    MemoirChatMessage,
    MemoirQuestion,
    MemoirSession,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.models.projects import DocumentGroup, Project
from app.schemas.memoir import MemoirProjectCreate, MemoirProjectListResponse, MemoirProjectUpdate
from app.schemas.projects import ProjectResponse
from app.services import project_documents as project_docs
from app.services.memoir import audio_files as memoir_audio_files
from app.services.projects.access import load_project


DEFAULT_MEMOIR_PROJECT_TITLE = "我的回忆录"
MEMOIR_PROFILE_METADATA_KEY = "memoir_profile"
DEFAULT_GUIDANCE_MODE = "free_narration"
ALLOWED_NARRATOR_GENDERS = {"male", "female", "unspecified"}
logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _clean_text(value: str | None) -> str:
    return (value or "").strip()


def _default_profile() -> dict[str, object]:
    return {
        "narrator_name": "",
        "narrator_gender": "unspecified",
        "narrator_origin": "",
        "narrator_age": None,
        "default_guidance_mode": DEFAULT_GUIDANCE_MODE,
    }


def _field_was_set(req: MemoirProjectCreate | MemoirProjectUpdate, field: str) -> bool:
    return field in getattr(req, "model_fields_set", set())


def _profile_metadata(
    req: MemoirProjectCreate | MemoirProjectUpdate,
    *,
    existing_metadata: dict | None = None,
    include_defaults: bool = False,
) -> dict:
    metadata = dict(existing_metadata or {})
    current_profile = metadata.get(MEMOIR_PROFILE_METADATA_KEY)
    profile = {
        **_default_profile(),
        **(current_profile if isinstance(current_profile, dict) else {}),
    }

    if include_defaults or _field_was_set(req, "elder_name"):
        profile["narrator_name"] = _clean_text(req.elder_name)
    if include_defaults or _field_was_set(req, "narrator_gender"):
        gender = _clean_text(req.narrator_gender) or "unspecified"
        if gender not in ALLOWED_NARRATOR_GENDERS:
            raise HTTPException(status_code=400, detail="性别只能选择男、女或未填写")
        profile["narrator_gender"] = gender
    if include_defaults or _field_was_set(req, "narrator_origin"):
        profile["narrator_origin"] = _clean_text(req.narrator_origin)
    if include_defaults or _field_was_set(req, "narrator_age"):
        profile["narrator_age"] = req.narrator_age
    if include_defaults or _field_was_set(req, "default_guidance_mode"):
        profile["default_guidance_mode"] = (
            req.default_guidance_mode.value
            if req.default_guidance_mode is not None
            else DEFAULT_GUIDANCE_MODE
        )

    metadata[MEMOIR_PROFILE_METADATA_KEY] = profile
    return metadata


def _memoir_project_options():
    return (
        selectinload(Project.groups).selectinload(DocumentGroup.documents),
        selectinload(Project.references),
    )


def _voice_text_char_count(text: str) -> int:
    return len("".join(str(text or "").split()))


def _project_response_with_voice_words(
    project: Project,
    voice_char_counts: dict[str, int],
) -> ProjectResponse:
    response = project_docs.project_response(project)
    voice_words = voice_char_counts.get(project.id, 0)
    if not voice_words:
        return response
    return response.model_copy(
        update={"total_word_count": response.total_word_count + voice_words}
    )


async def _memoir_voice_char_counts(
    db: AsyncSession,
    user_id: str,
    project_ids: list[str],
) -> dict[str, int]:
    if not project_ids:
        return {}
    result = await db.execute(
        select(MemoirChatMessage.project_id, MemoirChatMessage.content_text)
        .join(Project, MemoirChatMessage.project_id == Project.id)
        .where(
            Project.user_id == user_id,
            Project.id.in_(project_ids),
            Project.type == ProjectType.MEMOIR.value,
            MemoirChatMessage.role == MemoirChatMessageRole.USER.value,
            MemoirChatMessage.content_text != "",
        )
    )
    counts: dict[str, int] = {}
    for project_id, content_text in result.all():
        counts[project_id] = counts.get(project_id, 0) + _voice_text_char_count(content_text)
    return counts


async def _memoir_project_audio_files(
    db: AsyncSession,
    user_id: str,
    project_id: str,
) -> list[AudioFile]:
    chat_audio_ids = (
        select(MemoirChatMessage.audio_file_id)
        .where(
            MemoirChatMessage.project_id == project_id,
            MemoirChatMessage.audio_file_id.is_not(None),
        )
    )
    utterance_audio_ids = (
        select(MemoirVoiceUtterance.audio_file_id)
        .join(
            MemoirVoiceSession,
            MemoirVoiceUtterance.voice_session_id == MemoirVoiceSession.id,
        )
        .where(
            MemoirVoiceSession.project_id == project_id,
            MemoirVoiceUtterance.audio_file_id.is_not(None),
        )
    )
    answer_audio_ids = (
        select(MemoirAnswer.audio_file_id)
        .join(MemoirQuestion, MemoirAnswer.question_id == MemoirQuestion.id)
        .join(MemoirSession, MemoirQuestion.session_id == MemoirSession.id)
        .where(
            MemoirSession.project_id == project_id,
            MemoirAnswer.audio_file_id.is_not(None),
        )
    )
    result = await db.execute(
        select(AudioFile).where(
            AudioFile.user_id == user_id,
            or_(
                AudioFile.id.in_(chat_audio_ids),
                AudioFile.id.in_(utterance_audio_ids),
                AudioFile.id.in_(answer_audio_ids),
            ),
        )
    )
    return list(result.scalars())


async def _delete_audio_file_rows(db: AsyncSession, audio_file_ids: list[str]) -> None:
    if not audio_file_ids:
        return
    await db.execute(delete(AudioFile).where(AudioFile.id.in_(audio_file_ids)))
    await db.commit()


async def _referenced_audio_file_ids(db: AsyncSession, audio_file_ids: list[str]) -> set[str]:
    if not audio_file_ids:
        return set()

    referenced_ids: set[str] = set()
    queries = [
        select(MemoirChatMessage.audio_file_id).where(
            MemoirChatMessage.audio_file_id.in_(audio_file_ids)
        ),
        select(MemoirVoiceUtterance.audio_file_id).where(
            MemoirVoiceUtterance.audio_file_id.in_(audio_file_ids)
        ),
        select(MemoirAnswer.audio_file_id).where(MemoirAnswer.audio_file_id.in_(audio_file_ids)),
    ]
    for query in queries:
        result = await db.execute(query)
        referenced_ids.update(audio_id for audio_id in result.scalars() if audio_id)
    return referenced_ids


async def _load_memoir_project_response(
    db: AsyncSession,
    project_id: str,
    user_id: str,
) -> ProjectResponse:
    project = await load_project(db, project_id, user_id)
    if project.type != ProjectType.MEMOIR.value:
        raise HTTPException(status_code=400, detail="项目不是回忆录")
    voice_counts = await _memoir_voice_char_counts(db, user_id, [project.id])
    return _project_response_with_voice_words(project, voice_counts)


async def list_mobile_memoir_project_responses(
    db: AsyncSession,
    user_id: str,
) -> list[ProjectResponse]:
    result = await db.execute(
        select(Project)
        .where(Project.user_id == user_id, Project.type == ProjectType.MEMOIR.value)
        .options(*_memoir_project_options())
        .order_by(Project.updated_at.desc(), Project.created_at.desc())
    )
    projects = list(result.scalars())
    voice_counts = await _memoir_voice_char_counts(
        db,
        user_id,
        [project.id for project in projects],
    )
    return [_project_response_with_voice_words(project, voice_counts) for project in projects]


async def active_mobile_memoir_project(
    db: AsyncSession,
    user_id: str,
) -> ProjectResponse | None:
    projects = await list_mobile_memoir_project_responses(db, user_id)
    if projects:
        return projects[0]
    return None


async def get_mobile_memoir_project(
    db: AsyncSession,
    user_id: str,
    project_id: str,
) -> ProjectResponse:
    return await _load_memoir_project_response(db, project_id, user_id)


async def mobile_project_list(
    db: AsyncSession,
    user_id: str,
) -> MemoirProjectListResponse:
    projects = await list_mobile_memoir_project_responses(db, user_id)
    active_project = projects[0] if projects else None
    return MemoirProjectListResponse(
        active_project_id=active_project.id if active_project else "",
        active_project=active_project,
        projects=projects,
    )


async def create_mobile_memoir_project(
    db: AsyncSession,
    user_id: str,
    req: MemoirProjectCreate,
) -> ProjectResponse:
    title = (req.title or "").strip() or DEFAULT_MEMOIR_PROJECT_TITLE
    project = Project(
        user_id=user_id,
        type=ProjectType.MEMOIR.value,
        title=title,
        description=req.description,
        metadata_json=_profile_metadata(req, include_defaults=True),
    )
    db.add(project)
    await db.flush()
    await initialize_project_template(db, project)
    return await _load_memoir_project_response(db, project.id, user_id)


async def update_mobile_memoir_project(
    db: AsyncSession,
    user_id: str,
    project_id: str,
    req: MemoirProjectUpdate,
) -> ProjectResponse:
    project = await load_project(db, project_id, user_id)
    if project.type != ProjectType.MEMOIR.value:
        raise HTTPException(status_code=400, detail="项目不是回忆录")
    if req.title is not None:
        title = req.title.strip()
        if not title:
            raise HTTPException(status_code=400, detail="回忆录名称不能为空")
        project.title = title
    if req.description is not None:
        project.description = req.description
    project.metadata_json = _profile_metadata(req, existing_metadata=project.metadata_json)
    project.updated_at = _utcnow()
    await db.commit()
    return await _load_memoir_project_response(db, project.id, user_id)


async def activate_mobile_memoir_project(
    db: AsyncSession,
    user_id: str,
    project_id: str,
) -> MemoirProjectListResponse:
    project = await load_project(db, project_id, user_id)
    if project.type != ProjectType.MEMOIR.value:
        raise HTTPException(status_code=400, detail="项目不是回忆录")
    project.updated_at = _utcnow()
    await db.commit()
    return await mobile_project_list(db, user_id)


async def delete_mobile_memoir_project(
    db: AsyncSession,
    user_id: str,
    project_id: str,
) -> None:
    project = await load_project(db, project_id, user_id)
    if project.type != ProjectType.MEMOIR.value:
        raise HTTPException(status_code=400, detail="项目不是回忆录")

    projects = await list_mobile_memoir_project_responses(db, user_id)
    if len(projects) <= 1:
        raise HTTPException(status_code=400, detail="至少保留一本回忆录")

    audio_files = await _memoir_project_audio_files(db, user_id, project.id)
    audio_file_ids = [audio_file.id for audio_file in audio_files]

    await project_docs.delete_project(db, project)
    still_referenced_audio_ids = await _referenced_audio_file_ids(db, audio_file_ids)
    deletable_audio_files = [
        audio_file
        for audio_file in audio_files
        if audio_file.id not in still_referenced_audio_ids
    ]
    await _delete_audio_file_rows(db, [audio_file.id for audio_file in deletable_audio_files])
    try:
        await memoir_audio_files.delete_audio_file_paths(deletable_audio_files)
    except Exception:
        logger.warning(
            "Failed to delete memoir project audio files project_id=%s",
            project.id,
            exc_info=True,
        )
