from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import DEFAULT_AI_TEXT_MODEL
from app.domain.memoir.types import MemoirTranscriptPolishLevel
from app.models.projects import Project


AI_MODEL_METADATA_KEY = "ai_model"
DISABLED_AI_MODEL_ALIASES = frozenset({"claude-fable-5"})
TRANSCRIPT_POLISH_LEVEL_METADATA_KEY = "transcript_polish_level"
TEXT_POLISH_REQUIREMENTS = {
    MemoirTranscriptPolishLevel.LOW: (
        "- 去掉口头填充词、说话磕绊、赘词和没有信息增量的重复\n"
        "- 除上述清理外，只整理基础标点和极少量语序；不书面化、不文学化，长度尽量接近原文"
    ),
    MemoirTranscriptPolishLevel.MEDIUM: (
        "- 完成低档的全部清理，并适量调整口语化语序和用词，使表达自然、连贯、偏书面\n"
        "- 可以轻度提炼和润色，但不概括事实、不合并或省略有信息量的细节，保留讲述者语气"
    ),
    MemoirTranscriptPolishLevel.HIGH: (
        "- 完成低档的全部清理，并可较大幅度重组句式与表达层次，提炼重点、增强节奏和文采\n"
        "- 可以压缩无信息量的铺陈，但不得省略具体事实与有意义的细节，仍保持第一人称讲述"
    ),
}


def project_ai_model(project: Project | None) -> str | None:
    metadata = project.metadata_json if project else {}
    value = (metadata or {}).get(AI_MODEL_METADATA_KEY)
    return value.strip() if isinstance(value, str) and value.strip() else None


def remember_project_ai_model(project: Project, ai_model: str | None) -> None:
    model = (ai_model or "").strip()
    if not model or project_ai_model(project) == model:
        return
    project.metadata_json = {
        **(project.metadata_json or {}),
        AI_MODEL_METADATA_KEY: model,
        "ai_model_updated_at": datetime.now(UTC).replace(tzinfo=None).isoformat(),
    }


def resolve_project_ai_model(project: Project, requested_ai_model: str | None) -> str | None:
    requested = (requested_ai_model or "").strip()
    selected = requested or project_ai_model(project)
    if selected and selected.lower() in DISABLED_AI_MODEL_ALIASES:
        return DEFAULT_AI_TEXT_MODEL
    return selected


def select_project_ai_model(project: Project, requested_ai_model: str | None) -> str | None:
    selected = resolve_project_ai_model(project, requested_ai_model)
    remember_project_ai_model(project, selected)
    return selected


async def load_project_ai_model(
    db: AsyncSession,
    project_id: str,
    requested_ai_model: str | None = None,
) -> str | None:
    project = await db.get(Project, project_id)
    return resolve_project_ai_model(project, requested_ai_model) if project else None


def normalize_transcript_polish_level(
    value: str | MemoirTranscriptPolishLevel | None,
) -> MemoirTranscriptPolishLevel:
    try:
        return MemoirTranscriptPolishLevel(value)
    except (TypeError, ValueError):
        return MemoirTranscriptPolishLevel.MEDIUM


def text_polish_requirements(
    value: str | MemoirTranscriptPolishLevel | None,
) -> str:
    return TEXT_POLISH_REQUIREMENTS[normalize_transcript_polish_level(value)]


def project_transcript_polish_level(project: Project | None) -> MemoirTranscriptPolishLevel:
    metadata = project.metadata_json if project else {}
    return normalize_transcript_polish_level(
        (metadata or {}).get(TRANSCRIPT_POLISH_LEVEL_METADATA_KEY)
    )


def remember_project_transcript_polish_level(
    project: Project,
    level: str | MemoirTranscriptPolishLevel,
) -> None:
    project.metadata_json = {
        **(project.metadata_json or {}),
        TRANSCRIPT_POLISH_LEVEL_METADATA_KEY: normalize_transcript_polish_level(level).value,
    }


async def load_project_transcript_polish_level(
    db: AsyncSession,
    project_id: str,
    requested_level: str | MemoirTranscriptPolishLevel | None = None,
) -> MemoirTranscriptPolishLevel:
    if requested_level is not None:
        return normalize_transcript_polish_level(requested_level)
    return project_transcript_polish_level(await db.get(Project, project_id))
