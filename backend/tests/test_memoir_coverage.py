from sqlalchemy import func, select

from app.domain.memoir import MemoirCoverageStatus
from app.domain.projects.types import ProjectType
from app.models.memoir import MemoirCoverageSnapshot
from app.models.projects import Project
from app.models.users import User
from app.schemas.memoir import MemoirEventMemoryCreate, MemoirOpenQuestionCreate
from app.domain.memoir import MemoirMemoryType, MemoirQuestionType
from app.services.memoir.answers import answer_question_with_text
from app.services.memoir.coverage import calculate_coverage_snapshots, get_coverage_map, latest_coverage_snapshots
from app.services.memoir.interview import get_or_create_current_session
from app.services.memoir.memory_repository import create_event_memory, create_open_question


def _user(email: str) -> User:
    return User(email=email, password_hash="hash", display_name=email.split("@")[0])


async def _create_project(test_db, user: User) -> Project:
    project = Project(user_id=user.id, type=ProjectType.MEMOIR.value, title="我的回忆录")
    test_db.add(project)
    await test_db.flush()
    return project


async def test_calculate_coverage_snapshots_scores_default_topics(test_db):
    user = _user("coverage@example.com")
    test_db.add(user)
    await test_db.flush()
    project = await _create_project(test_db, user)
    state = await get_or_create_current_session(test_db, project.id, user.id)
    await answer_question_with_text(
        test_db,
        state.current_question.id,
        user.id,
        "我第一次去重庆厂里上班，那个工作让我很骄傲。",
    )
    event = await create_event_memory(
        test_db,
        user.id,
        MemoirEventMemoryCreate(
            project_id=project.id,
            title="第一次去重庆工作",
            life_stage="青年",
            time_text="第一次去重庆厂里上班",
            place_text="重庆厂",
            process="去厂里开始工作",
            emotion="骄傲",
        ),
    )
    await create_open_question(
        test_db,
        user.id,
        MemoirOpenQuestionCreate(
            project_id=project.id,
            target_memory_type=MemoirMemoryType.EVENT,
            target_memory_id=event.id,
            question="你第一次去重庆厂里那天是谁送你的？",
            question_type=MemoirQuestionType.SCENE,
            reason="第一次工作缺少场景细节",
        ),
    )

    snapshots = await calculate_coverage_snapshots(test_db, user.id, project.id)
    latest = await latest_coverage_snapshots(test_db, user.id, project.id)
    by_key = {snapshot.signals_json["topic_key"]: snapshot for snapshot in snapshots}

    assert len(snapshots) == 10
    assert len(latest) == 10
    assert by_key["first_job"].answered_count == 1
    assert by_key["first_job"].memory_count >= 1
    assert by_key["first_job"].open_question_count == 1
    assert by_key["first_job"].coverage_score >= 50
    assert by_key["first_job"].status in {MemoirCoverageStatus.YELLOW, MemoirCoverageStatus.GREEN}
    assert by_key["marriage"].coverage_score == 0
    assert by_key["marriage"].status == MemoirCoverageStatus.RED


async def test_latest_coverage_snapshots_are_empty_before_calculation(test_db):
    user = _user("coverage-empty@example.com")
    test_db.add(user)
    await test_db.flush()
    project = await _create_project(test_db, user)

    assert await latest_coverage_snapshots(test_db, user.id, project.id) == []

    await calculate_coverage_snapshots(test_db, user.id, project.id)

    result = await test_db.execute(select(Project).where(Project.id == project.id))
    assert result.scalar_one().id == project.id
    assert len(await latest_coverage_snapshots(test_db, user.id, project.id)) == 10


async def test_get_coverage_map_reads_latest_snapshots_without_writes(test_db):
    user = _user("coverage-readonly@example.com")
    test_db.add(user)
    await test_db.flush()
    project = await _create_project(test_db, user)

    assert await get_coverage_map(test_db, user.id, project.id) == []
    assert await test_db.scalar(select(func.count()).select_from(MemoirCoverageSnapshot)) == 0

    await calculate_coverage_snapshots(test_db, user.id, project.id)
    first = await get_coverage_map(test_db, user.id, project.id)
    second = await get_coverage_map(test_db, user.id, project.id)

    assert len(first) == 10
    assert [item.topic_id for item in second] == [item.topic_id for item in first]
    assert await test_db.scalar(select(func.count()).select_from(MemoirCoverageSnapshot)) == 10
