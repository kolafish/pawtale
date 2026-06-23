from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir import DEFAULT_COVERAGE_TOPICS, MemoirCoverageStatus, MemoirOpenQuestionStatus
from app.models.memoir import (
    MemoirAnswer,
    MemoirCoverageSnapshot,
    MemoirCoverageTopic,
    MemoirEventMemory,
    MemoirOpenQuestion,
    MemoirPersonMemory,
    MemoirPlaceMemory,
    MemoirQuestion,
    MemoirSession,
    MemoirThemeMemory,
)
from app.schemas.memoir_memory import (
    MemoirCoverageItemResponse,
    MemoirCoverageSnapshotResponse,
    MemoirCoverageTopicCreate,
    MemoirCoverageTopicResponse,
)
from app.services.memoir.access import load_memoir_project


_DEFAULT_TOPIC_BY_KEY = {topic.key: topic for topic in DEFAULT_COVERAGE_TOPICS}


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    if not text:
        return False
    return any(keyword and keyword in text for keyword in keywords)


def _coverage_status(score: float) -> MemoirCoverageStatus:
    if score >= 70:
        return MemoirCoverageStatus.GREEN
    if score >= 30:
        return MemoirCoverageStatus.YELLOW
    return MemoirCoverageStatus.RED


def _topic_keywords(topic: MemoirCoverageTopic) -> tuple[str, ...]:
    default = _DEFAULT_TOPIC_BY_KEY.get(topic.key)
    if default:
        return default.keywords
    values = [topic.name, topic.life_stage or ""]
    return tuple(value for value in values if value)


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


async def _coverage_source_rows(db: AsyncSession, project_id: str) -> tuple[list[str], list[str], list[str]]:
    answer_rows = await db.execute(
        select(MemoirQuestion.prompt, MemoirAnswer.transcript_raw, MemoirAnswer.transcript_edited)
        .join(MemoirAnswer, MemoirAnswer.question_id == MemoirQuestion.id)
        .join(MemoirSession, MemoirQuestion.session_id == MemoirSession.id)
        .where(MemoirSession.project_id == project_id)
    )
    answer_texts = [
        " ".join(part for part in row if part)
        for row in answer_rows.all()
    ]

    memory_texts: list[str] = []
    for model, columns in (
        (MemoirPersonMemory, (MemoirPersonMemory.canonical_name, MemoirPersonMemory.relationship, MemoirPersonMemory.description)),
        (MemoirEventMemory, (MemoirEventMemory.title, MemoirEventMemory.life_stage, MemoirEventMemory.time_text, MemoirEventMemory.place_text, MemoirEventMemory.process, MemoirEventMemory.emotion)),
        (MemoirPlaceMemory, (MemoirPlaceMemory.name, MemoirPlaceMemory.elder_alias, MemoirPlaceMemory.modern_name, MemoirPlaceMemory.description)),
        (MemoirThemeMemory, (MemoirThemeMemory.name, MemoirThemeMemory.description)),
    ):
        result = await db.execute(select(*columns).where(model.project_id == project_id))
        memory_texts.extend(" ".join(part for part in row if part) for row in result.all())

    open_question_rows = await db.execute(
        select(MemoirOpenQuestion.question, MemoirOpenQuestion.reason)
        .where(
            MemoirOpenQuestion.project_id == project_id,
            MemoirOpenQuestion.status == MemoirOpenQuestionStatus.OPEN.value,
        )
    )
    open_question_texts = [
        " ".join(part for part in row if part)
        for row in open_question_rows.all()
    ]
    return answer_texts, memory_texts, open_question_texts


async def calculate_coverage_snapshots(
    db: AsyncSession,
    user_id: str,
    project_id: str,
) -> list[MemoirCoverageSnapshotResponse]:
    await initialize_default_coverage_topics(db, user_id, project_id)
    result = await db.execute(
        select(MemoirCoverageTopic)
        .where(MemoirCoverageTopic.project_id == project_id)
        .order_by(MemoirCoverageTopic.sort_order, MemoirCoverageTopic.created_at)
    )
    topics = list(result.scalars())
    answer_texts, memory_texts, open_question_texts = await _coverage_source_rows(db, project_id)

    snapshots: list[MemoirCoverageSnapshot] = []
    for topic in topics:
        keywords = _topic_keywords(topic)
        answered_count = sum(_contains_any(text, keywords) for text in answer_texts)
        memory_count = sum(_contains_any(text, keywords) for text in memory_texts)
        open_question_count = sum(_contains_any(text, keywords) for text in open_question_texts)
        coverage_score = min(100.0, answered_count * 25.0 + memory_count * 25.0)
        status = _coverage_status(coverage_score)
        snapshot = MemoirCoverageSnapshot(
            project_id=project_id,
            topic_id=topic.id,
            answered_count=answered_count,
            memory_count=memory_count,
            open_question_count=open_question_count,
            coverage_score=coverage_score,
            status=status.value,
            signals_json={
                "topic_key": topic.key,
                "topic_name": topic.name,
                "keywords": list(keywords),
                "has_open_questions": open_question_count > 0,
            },
        )
        db.add(snapshot)
        snapshots.append(snapshot)

    await db.flush()
    return [MemoirCoverageSnapshotResponse.model_validate(snapshot) for snapshot in snapshots]


async def latest_coverage_snapshots(
    db: AsyncSession,
    user_id: str,
    project_id: str,
) -> list[MemoirCoverageSnapshotResponse]:
    await load_memoir_project(db, project_id, user_id)
    topics = await db.execute(
        select(MemoirCoverageTopic)
        .where(MemoirCoverageTopic.project_id == project_id)
        .order_by(MemoirCoverageTopic.sort_order, MemoirCoverageTopic.created_at)
    )
    responses: list[MemoirCoverageSnapshotResponse] = []
    for topic in topics.scalars():
        snapshot = await db.scalar(
            select(MemoirCoverageSnapshot)
            .where(MemoirCoverageSnapshot.topic_id == topic.id)
            .order_by(MemoirCoverageSnapshot.calculated_at.desc())
            .limit(1)
        )
        if snapshot:
            responses.append(MemoirCoverageSnapshotResponse.model_validate(snapshot))
    return responses


async def get_coverage_map(
    db: AsyncSession,
    user_id: str,
    project_id: str,
) -> list[MemoirCoverageItemResponse]:
    snapshots = await latest_coverage_snapshots(db, user_id, project_id)
    topics = await db.execute(
        select(MemoirCoverageTopic)
        .where(MemoirCoverageTopic.project_id == project_id)
        .order_by(MemoirCoverageTopic.sort_order, MemoirCoverageTopic.created_at)
    )
    topic_by_id = {topic.id: topic for topic in topics.scalars()}
    items: list[MemoirCoverageItemResponse] = []
    for snapshot in snapshots:
        topic = topic_by_id.get(snapshot.topic_id)
        if not topic:
            continue
        items.append(
            MemoirCoverageItemResponse(
                topic_id=topic.id,
                key=topic.key,
                name=topic.name,
                life_stage=topic.life_stage,
                description=topic.description,
                sort_order=topic.sort_order,
                answered_count=snapshot.answered_count,
                memory_count=snapshot.memory_count,
                open_question_count=snapshot.open_question_count,
                coverage_score=snapshot.coverage_score,
                status=snapshot.status,
                signals_json=snapshot.signals_json,
                calculated_at=snapshot.calculated_at,
            )
        )
    return items
