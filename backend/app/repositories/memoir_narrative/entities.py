from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import (
    MemoirNarrativeEntityType,
    MemoirNarrativeEntityVerificationStatus,
)
from app.models.memoir_narrative import MemoirNarrativeEntity
from app.models.projects import Project
from app.schemas.memoir_narrative import MemoirNarrativeEntityCreate


class MemoirNarrativeEntityRepository:
    def __init__(self, db: AsyncSession):
        self._db = db

    async def create(self, data: MemoirNarrativeEntityCreate) -> MemoirNarrativeEntity:
        project_exists = await self._db.scalar(
            select(Project.id).where(Project.id == data.project_id)
        )
        if project_exists is None:
            raise ValueError("entity project does not exist")
        entity = MemoirNarrativeEntity(
            project_id=data.project_id,
            entity_type=data.entity_type.value,
            canonical_name=data.canonical_name,
            aliases_json=data.aliases,
            description=data.description,
            verification_status=data.verification_status.value,
            created_by=data.created_by.value,
            created_by_user_id=data.created_by_user_id,
            updated_by=data.created_by.value,
            updated_by_user_id=data.created_by_user_id,
        )
        self._db.add(entity)
        await self._db.flush()
        return entity

    async def get(self, *, project_id: str, entity_id: str) -> MemoirNarrativeEntity | None:
        return await self._db.scalar(
            select(MemoirNarrativeEntity).where(
                MemoirNarrativeEntity.project_id == project_id,
                MemoirNarrativeEntity.id == entity_id,
            )
        )

    async def list_for_project(
        self,
        *,
        project_id: str,
        entity_type: MemoirNarrativeEntityType | None = None,
        verification_status: MemoirNarrativeEntityVerificationStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[MemoirNarrativeEntity]:
        limit = max(1, min(limit, 500))
        offset = max(0, offset)
        statement = select(MemoirNarrativeEntity).where(MemoirNarrativeEntity.project_id == project_id)
        if entity_type is not None:
            statement = statement.where(MemoirNarrativeEntity.entity_type == entity_type.value)
        if verification_status is not None:
            statement = statement.where(
                MemoirNarrativeEntity.verification_status == verification_status.value
            )
        statement = statement.order_by(
            MemoirNarrativeEntity.canonical_name,
            MemoirNarrativeEntity.id,
        ).limit(limit).offset(offset)
        return list((await self._db.scalars(statement)).all())
