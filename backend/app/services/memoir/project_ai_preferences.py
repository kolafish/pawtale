from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.projects import Project


AI_MODEL_METADATA_KEY = "ai_model"


def project_ai_model(project: Project | None) -> str | None:
    metadata = project.metadata_json if project else {}
    value = (metadata or {}).get(AI_MODEL_METADATA_KEY)
    return value.strip() if isinstance(value, str) and value.strip() else None


def remember_project_ai_model(project: Project, ai_model: str | None) -> None:
    model = (ai_model or "").strip()
    if not model:
        return
    project.metadata_json = {
        **(project.metadata_json or {}),
        AI_MODEL_METADATA_KEY: model,
        "ai_model_updated_at": datetime.now(UTC).replace(tzinfo=None).isoformat(),
    }


def select_project_ai_model(project: Project, requested_ai_model: str | None) -> str | None:
    requested = (requested_ai_model or "").strip()
    if requested:
        remember_project_ai_model(project, requested)
        return requested
    return project_ai_model(project)


async def load_project_ai_model(
    db: AsyncSession,
    project_id: str,
    requested_ai_model: str | None = None,
) -> str | None:
    project = await db.get(Project, project_id)
    return select_project_ai_model(project, requested_ai_model) if project else None
