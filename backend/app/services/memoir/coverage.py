from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir import DEFAULT_COVERAGE_TOPICS
from app.models.memoir import MemoirCoverageTopic
from app.schemas.memoir import MemoirCoverageTopicCreate, MemoirCoverageTopicResponse
from app.services.memoir.access import load_memoir_project


async def initialize_default_coverage_topics(
    db: AsyncSession,
    user_id: str,
    project_id: str,
) -> list[MemoirCoverageTopicResponse]:
    await load_memoir_project(db, project_id, user_id)
    existing = await db.execute(
        select(MemoirCoverageTopic).where(MemoirCoverageTopic.project_id == project_id)
    )
    topics_by_key = {topic.key: topic for topic in existing.scalars()}

    for sort_order, default_topic in enumerate(DEFAULT_COVERAGE_TOPICS):
        if default_topic.key in topics_by_key:
            continue
        topic = MemoirCoverageTopic(
            project_id=project_id,
            key=default_topic.key,
            name=default_topic.name,
            life_stage=default_topic.life_stage,
            description=default_topic.description,
            sort_order=sort_order,
        )
        db.add(topic)

    await db.flush()
    return await list_coverage_topics(db, user_id, project_id)


async def create_coverage_topic(
    db: AsyncSession,
    user_id: str,
    req: MemoirCoverageTopicCreate,
) -> MemoirCoverageTopicResponse:
    await load_memoir_project(db, req.project_id, user_id)
    topic = MemoirCoverageTopic(
        project_id=req.project_id,
        key=req.key.strip(),
        name=req.name.strip(),
        life_stage=req.life_stage,
        description=req.description,
        sort_order=req.sort_order,
    )
    db.add(topic)
    await db.flush()
    return MemoirCoverageTopicResponse.model_validate(topic)


async def list_coverage_topics(
    db: AsyncSession,
    user_id: str,
    project_id: str,
) -> list[MemoirCoverageTopicResponse]:
    await load_memoir_project(db, project_id, user_id)
    result = await db.execute(
        select(MemoirCoverageTopic)
        .where(MemoirCoverageTopic.project_id == project_id)
        .order_by(MemoirCoverageTopic.sort_order, MemoirCoverageTopic.created_at)
    )
    return [
        MemoirCoverageTopicResponse.model_validate(topic)
        for topic in result.scalars()
    ]
