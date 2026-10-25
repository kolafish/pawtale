from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import DEFAULT_AI_TEXT_MODEL
from app.domain.memoir.types import MemoirTranscriptPolishLevel
from app.models.projects import Project
from app.services.ai_access import restrict_requested_ai_model
from app.services.memoir.share_video_styles import ShareVideoStyle, normalize_share_video_style


AI_MODEL_METADATA_KEY = "ai_model"
DISABLED_AI_MODEL_ALIASES = frozenset(
    {
        "claude-fable-5",
        "claude-opus-4-8",
        "claude-sonnet-4",
        "claude-sonnet-4-6",
    }
)
TRANSCRIPT_POLISH_LEVEL_METADATA_KEY = "transcript_polish_level"
SHARE_VIDEO_STYLE_METADATA_KEY = "share_video_style"
TEXT_POLISH_REQUIREMENTS = {
    MemoirTranscriptPolishLevel.LOW: (
        "- 去掉口头填充词、说话磕绊、赘词和没有信息增量的重复，清理面向听众的无信息开场与转场\n"
        "- 修正语法、标点和口语倒装，拆开过长句子，写成自然的书面句子；保留个人用词、语气和叙述顺序，不刻意追求文采"
    ),
    MemoirTranscriptPolishLevel.MEDIUM: (
        "- 完成低档清理，把口语句式改成简洁书面语，使表达自然、连贯、偏书面\n"
        "- 合并同义赘述，围绕同一件事组织句群并自然分段，段落间换行；保留个人口吻及全部实质细节，不把具体经历压成概述"
    ),
    MemoirTranscriptPolishLevel.HIGH: (
        "- 完成中档整理，将逐句口述改写为成熟、流畅的回忆录段落；可较大幅度重组句式与表达层次，围绕已有主题调整叙述顺序和重点，段落间换行\n"
        "- 文采来自准确用词和句式节奏；主动替换绕口、松散的口语表达，保留个人口吻、具体事实与细节；调整讲述顺序不能改变事件实际先后，不添加场景、感慨或升华"
    ),
}
TEXT_POLISH_FIDELITY = (
    "\n- 三档只改变表达，不新增事实、人物、关系、时间、地点、事件、因果或情绪；保留数量、否定、情绪强度及有意义的重复。"
    "\n- 保留听说、推测、记不清等信息来源与不确定性；动作、环境、外貌、身体感觉、动机、心理和对话必须有原述依据。短素材可以写短。"
    "\n- 指代、动作归属、时间跨度或因果不明确时保留疑义，不把相邻句补成因果或持续过程。第一人称仅属于讲述者，不把他人的经历、转述或访谈员的话改成讲述者亲历。"
)


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
    return restrict_requested_ai_model(selected) if selected else None


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
        return MemoirTranscriptPolishLevel.HIGH


def text_polish_requirements(
    value: str | MemoirTranscriptPolishLevel | None,
) -> str:
    return TEXT_POLISH_REQUIREMENTS[normalize_transcript_polish_level(value)] + TEXT_POLISH_FIDELITY


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


def project_share_video_style(project: Project | None) -> ShareVideoStyle:
    metadata = project.metadata_json if project else {}
    return normalize_share_video_style((metadata or {}).get(SHARE_VIDEO_STYLE_METADATA_KEY))


def remember_project_share_video_style(project: Project, style: ShareVideoStyle) -> None:
    project.metadata_json = {
        **(project.metadata_json or {}),
        SHARE_VIDEO_STYLE_METADATA_KEY: style,
    }
