from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.narrative_types import (
    MemoirNarrativeEntityType,
    MemoirStoryCompletionStatus,
    MemoirStoryLifecycleStatus,
    MemoirStoryPublicationStatus,
    MemoirStoryType,
    MemoirStoryVerificationStatus,
)
from app.models.memoir_narrative import MemoirNarrativeEntity, MemoirStory, MemoirStorySlot
from app.models.projects import Project
from app.schemas.memoir_narrative import MemoirStoryCreate


def normalize_story_title(value: str) -> str:
    return "".join(value.casefold().split())


class MemoirStoryRepository:
    def __init__(self, db: AsyncSession):
        self._db = db

    async def create(
        self,
        data: MemoirStoryCreate,
        *,
        story_id: str | None = None,
    ) -> MemoirStory:
        if await self._db.scalar(select(Project.id).where(Project.id == data.project_id)) is None:
            raise ValueError("story project does not exist")
        if data.primary_place_entity_id:
            place = await self._db.scalar(
                select(MemoirNarrativeEntity).where(
                    MemoirNarrativeEntity.project_id == data.project_id,
                    MemoirNarrativeEntity.id == data.primary_place_entity_id,
                )
            )
            if place is None or place.entity_type != MemoirNarrativeEntityType.PLACE.value:
                raise ValueError("story primary place must be a place entity in the project")
        story = MemoirStory(
            id=story_id,
            project_id=data.project_id,
            title=data.title,
            title_normalized=normalize_story_title(data.title),
            story_type=data.story_type.value,
            summary=data.summary,
            time_start=data.time_start,
            time_end=data.time_end,
            time_precision=data.time_precision.value,
            primary_place_entity_id=data.primary_place_entity_id,
            lifecycle_status=MemoirStoryLifecycleStatus.ACTIVE.value,
            completion_status=MemoirStoryCompletionStatus.INCOMPLETE.value,
            verification_status=data.verification_status.value,
            publication_status=data.publication_status.value,
            slot_schema_version=data.slot_schema_version,
            version=1,
            created_by=data.created_by.value,
            created_by_user_id=data.created_by_user_id,
            updated_by=data.created_by.value,
            updated_by_user_id=data.created_by_user_id,
        )
        self._db.add(story)
        await self._db.flush()
        return story

    async def get(
        self,
        *,
        project_id: str,
        story_id: str,
        with_relations: bool = False,
    ) -> MemoirStory | None:
        statement = select(MemoirStory).where(
            MemoirStory.project_id == project_id,
            MemoirStory.id == story_id,
        )
        if with_relations:
            statement = statement.options(
                selectinload(MemoirStory.claims),
                selectinload(MemoirStory.slots).selectinload(MemoirStorySlot.claims),
                selectinload(MemoirStory.revisions),
            )
        return await self._db.scalar(statement)

    async def list_for_project(
        self,
        *,
        project_id: str,
        story_type: MemoirStoryType | None = None,
        lifecycle_status: MemoirStoryLifecycleStatus | None = None,
        completion_status: MemoirStoryCompletionStatus | None = None,
        verification_status: MemoirStoryVerificationStatus | None = None,
        publication_status: MemoirStoryPublicationStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[MemoirStory]:
        statement = select(MemoirStory).where(MemoirStory.project_id == project_id)
        filters = (
            (MemoirStory.story_type, story_type),
            (MemoirStory.lifecycle_status, lifecycle_status),
            (MemoirStory.completion_status, completion_status),
            (MemoirStory.verification_status, verification_status),
            (MemoirStory.publication_status, publication_status),
        )
        for column, value in filters:
            if value is not None:
                statement = statement.where(column == value.value)
        statement = statement.order_by(MemoirStory.updated_at.desc(), MemoirStory.id).limit(
            max(1, min(limit, 500))
        ).offset(max(0, offset))
        return list((await self._db.scalars(statement)).all())
