
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.clock import utcnow_naive
from app.domain.memoir.types import MemoirChatMessageRole
from app.domain.projects.templates import initialize_project_template
from app.domain.projects.types import ProjectType
from app.models.memoir import MemoirChatMessage
from app.models.projects import DocumentGroup, Project
from app.models.users import User
from app.schemas.memoir_projects import (
    MemoirIdentityResponse,
    MemoirProjectCreate,
    MemoirProjectListResponse,
    MemoirProjectUpdate,
)
from app.schemas.projects import ProjectResponse
import app.services.project_documents as project_docs
from app.services.memoir import project_deletion
from app.services.memoir import book_outputs
from app.services.memoir.project_ai_preferences import (
    remember_project_transcript_polish_level,
)
from app.services.projects.access import load_project


MEMOIR_PROFILE_METADATA_KEY = "memoir_profile"
DEFAULT_GUIDANCE_MODE = "free_narration"
DEFAULT_INTERVIEW_TYPE = "ai_interview"
DEFAULT_SESSION_GOAL = "open_narration"
ALLOWED_NARRATOR_GENDERS = {"male", "female", "unspecified"}


def _clean_text(value: str | None) -> str:
    return (value or "").strip()


def _default_profile() -> dict[str, object]:
    return {
        "narrator_name": "",
        "narrator_gender": "unspecified",
        "narrator_origin": "",
        "narrator_age": None,
        "default_guidance_mode": DEFAULT_GUIDANCE_MODE,
        "default_interview_type": DEFAULT_INTERVIEW_TYPE,
        "default_session_goal": DEFAULT_SESSION_GOAL,
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
    if include_defaults or _field_was_set(req, "default_interview_type"):
        profile["default_interview_type"] = (
            req.default_interview_type.value
            if req.default_interview_type is not None
            else DEFAULT_INTERVIEW_TYPE
        )
    if include_defaults or _field_was_set(req, "default_session_goal"):
        profile["default_session_goal"] = (
            req.default_session_goal.value
            if req.default_session_goal is not None
            else DEFAULT_SESSION_GOAL
        )
    if (
        _field_was_set(req, "default_guidance_mode")
        and not _field_was_set(req, "default_interview_type")
    ):
        # Older clients only send the AI guidance field. Treat that as an
        # explicit return to an AI interview if the project previously used
        # human interview mode.
        profile["default_interview_type"] = DEFAULT_INTERVIEW_TYPE
    if (
        _field_was_set(req, "default_guidance_mode")
        or _field_was_set(req, "default_interview_type")
    ) and not _field_was_set(req, "default_session_goal"):
        # Older clients do not know about the project-level session goal.
        # Changing an interview default there means returning to a regular
        # narration instead of keeping a stale book-completion default.
        profile["default_session_goal"] = DEFAULT_SESSION_GOAL
    if profile["default_session_goal"] == "book_completion":
        profile["default_guidance_mode"] = "guided_interview"
        profile["default_interview_type"] = DEFAULT_INTERVIEW_TYPE
    elif profile["default_interview_type"] == "human_interview":
        profile["default_guidance_mode"] = DEFAULT_GUIDANCE_MODE

    metadata[MEMOIR_PROFILE_METADATA_KEY] = profile
    return metadata


def _memoir_project_options():
    return (
        selectinload(Project.groups).selectinload(DocumentGroup.documents),
        selectinload(Project.references),
    )


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
        select(
            MemoirChatMessage.project_id,
            func.sum(
                func.length(
                    func.regexp_replace(
                        MemoirChatMessage.content_text,
                        "[[:space:]\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]",
                        "",
                        "g",
                    )
                )
            ),
        )
        .join(Project, MemoirChatMessage.project_id == Project.id)
        .where(
            Project.user_id == user_id,
            Project.id.in_(project_ids),
            Project.type == ProjectType.MEMOIR.value,
            MemoirChatMessage.role == MemoirChatMessageRole.USER.value,
            MemoirChatMessage.content_text != "",
        )
        .group_by(MemoirChatMessage.project_id)
    )
    return dict(result.all())


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


async def memoir_identity_response(
    db: AsyncSession,
    user: User,
) -> MemoirIdentityResponse:
    projects = await mobile_project_list(db, user.id)
    return MemoirIdentityResponse(
        id=user.id,
        email=user.email,
        password_login_enabled=user.password_login_enabled,
        role=user.role,
        display_name=user.display_name,
        avatar_url=user.avatar_url,
        created_at=user.created_at,
        active_project_id=projects.active_project_id,
        active_project=projects.active_project,
        projects=projects.projects,
    )


async def create_mobile_memoir_project(
    db: AsyncSession,
    user_id: str,
    req: MemoirProjectCreate,
) -> ProjectResponse:
    title = req.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="回忆录名称不能为空")
    project = Project(
        user_id=user_id,
        type=ProjectType.MEMOIR.value,
        title=title,
        description=req.description,
        metadata_json=_profile_metadata(req, include_defaults=True),
    )
    remember_project_transcript_polish_level(project, req.transcript_polish_level)
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
        await book_outputs.synchronize_book_output_title(
            db,
            project_id=project.id,
            title=title,
        )
    if req.description is not None:
        project.description = req.description
    project.metadata_json = _profile_metadata(req, existing_metadata=project.metadata_json)
    if _field_was_set(req, "transcript_polish_level") and req.transcript_polish_level:
        remember_project_transcript_polish_level(project, req.transcript_polish_level)
    project.updated_at = utcnow_naive()
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
    project.updated_at = utcnow_naive()
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

    await project_deletion.delete_project(db, project)
