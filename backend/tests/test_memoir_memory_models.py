import pytest
import uuid
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.domain.memoir import (
    MemoirCoverageStatus,
    MemoirCreatedBy,
    MemoirInterviewStage,
    MemoirMemoryStatus,
    MemoirMemoryType,
    MemoirOpenQuestionStatus,
    MemoirOutputStatus,
    MemoirOutputType,
    MemoirQuestionCandidateStatus,
    MemoirQuestionType,
    MemoirSensitivity,
    MemoirSourceArtifactType,
    MemoirTranscriptVersionType,
)
from app.domain.projects.types import ProjectType
from app.models.memoir import (
    MemoirAnswer,
    MemoirCoverageSnapshot,
    MemoirCoverageTopic,
    MemoirEventMemory,
    MemoirMemoryEvidence,
    MemoirMemoryLink,
    MemoirOpenQuestion,
    MemoirOutput,
    MemoirOutputVersion,
    MemoirPersonMemory,
    MemoirPlaceMemory,
    MemoirQuestion,
    MemoirQuestionCandidate,
    MemoirSession,
    MemoirSessionSummary,
    MemoirSourceArtifact,
    MemoirThemeMemory,
    MemoirTranscriptVersion,
)
from app.models.projects import Project
from app.models.users import User
from app.schemas.memoir import MemoirEventMemoryResponse, MemoirSourceArtifactResponse


def _user(email: str) -> User:
    return User(email=email, password_hash="hash", display_name=email.split("@")[0])


async def _create_memoir_project(test_db, user: User, title: str = "我的回忆录") -> Project:
    project = Project(user_id=user.id, type=ProjectType.MEMOIR.value, title=title)
    test_db.add(project)
    await test_db.flush()
    return project


async def _count(test_db, model) -> int:
    return await test_db.scalar(select(func.count()).select_from(model))


async def test_memoir_memory_models_round_trip(test_db):
    user = _user("memory-model@example.com")
    test_db.add(user)
    await test_db.flush()
    project = await _create_memoir_project(test_db, user)

    session = MemoirSession(project_id=project.id, title="青年")
    question = MemoirQuestion(session=session, prompt="第一次工作是什么时候？")
    answer = MemoirAnswer(
        question=question,
        transcript_raw="第一次去重庆厂里上班。",
        transcript_edited="第一次去重庆厂里上班。",
    )
    artifact = MemoirSourceArtifact(
        project=project,
        session=session,
        answer=answer,
        artifact_type=MemoirSourceArtifactType.AUDIO.value,
        storage_path="memoir/audio/first-job.m4a",
        mime_type="audio/mp4",
        size_bytes=1024,
        metadata_json={"duration": 12},
    )
    transcript = MemoirTranscriptVersion(
        answer=answer,
        source_artifact=artifact,
        version_type=MemoirTranscriptVersionType.ASR_RAW.value,
        content="第一次去重庆厂里上班。",
        provider="test-asr",
        confidence=0.9,
        created_by=MemoirCreatedBy.ASR.value,
    )
    person_id = str(uuid.uuid4())
    event_id = str(uuid.uuid4())
    person = MemoirPersonMemory(
        id=person_id,
        project=project,
        canonical_name="老张",
        aliases_json=["张师傅"],
        relationship="同事",
        first_seen_session=session,
        description="厂里的同事",
        status=MemoirMemoryStatus.DRAFT.value,
        created_by=MemoirCreatedBy.AI.value,
    )
    event = MemoirEventMemory(
        id=event_id,
        project=project,
        title="第一次去重庆工作",
        life_stage="青年",
        time_text="二十岁左右",
        place_text="重庆",
        process="离开家去厂里上班",
        emotion="紧张又期待",
        importance=0.8,
        status=MemoirMemoryStatus.CONFIRMED.value,
    )
    place = MemoirPlaceMemory(
        project=project,
        name="重庆厂",
        elder_alias="那个厂",
        modern_name="重庆某机械厂",
        place_type="工作地点",
        description="第一次工作的地方",
    )
    theme = MemoirThemeMemory(
        project=project,
        name="离乡",
        description="多次提到离开家去外地谋生",
        strength=0.7,
        mention_count=2,
    )
    link = MemoirMemoryLink(
        project=project,
        source_type=MemoirMemoryType.EVENT.value,
        source_id=event_id,
        target_type=MemoirMemoryType.PERSON.value,
        target_id=person_id,
        relation_type="participant",
        confidence=0.8,
    )
    evidence = MemoirMemoryEvidence(
        project=project,
        memory_type=MemoirMemoryType.EVENT.value,
        memory_id=event_id,
        session=session,
        question=question,
        answer=answer,
        transcript_version=transcript,
        quote="第一次去重庆厂里上班。",
        confidence=0.9,
    )
    open_question = MemoirOpenQuestion(
        project=project,
        session=session,
        target_memory_type=MemoirMemoryType.EVENT.value,
        target_memory_id=event_id,
        question="当时是谁送你去重庆的？",
        question_type=MemoirQuestionType.SCENE.value,
        reason="关键事件缺少人物和场景细节",
        sensitivity=MemoirSensitivity.LOW.value,
        priority=0.8,
        status=MemoirOpenQuestionStatus.OPEN.value,
    )
    topic = MemoirCoverageTopic(
        project=project,
        key="first_job",
        name="第一次工作",
        life_stage="青年",
        description="第一次工作和职业起点",
    )
    snapshot = MemoirCoverageSnapshot(
        project=project,
        topic=topic,
        answered_count=1,
        memory_count=4,
        open_question_count=1,
        coverage_score=0.4,
        status=MemoirCoverageStatus.YELLOW.value,
        signals_json={"low_coverage": True},
    )
    candidate = MemoirQuestionCandidate(
        project=project,
        session=session,
        coverage_topic=topic,
        question="第一次进厂那天宿舍是什么样的？",
        question_type=MemoirQuestionType.SCENE.value,
        target_memory_type=MemoirMemoryType.EVENT.value,
        target_memory_id=event_id,
        reason="场景细节不足",
        sensitivity=MemoirSensitivity.LOW.value,
        stage=MemoirInterviewStage.DEPTH.value,
        priority=0.82,
        status=MemoirQuestionCandidateStatus.CANDIDATE.value,
    )
    summary = MemoirSessionSummary(
        project=project,
        session=session,
        summary="本次聊到第一次去重庆工作。",
        new_people_json=[{"name": "老张"}],
        new_events_json=[{"title": "第一次去重庆工作"}],
        open_questions_json=[{"question": open_question.question}],
    )
    output = MemoirOutput(
        project=project,
        output_type=MemoirOutputType.STORY_FRAGMENT.value,
        title="第一次离乡工作",
        source_session=session,
        status=MemoirOutputStatus.DRAFT.value,
    )
    MemoirOutputVersion(
        output=output,
        version=1,
        content="第一次离乡工作，是从去重庆厂里开始的。",
        model="test-model",
    )

    test_db.add_all([
        session,
        artifact,
        transcript,
        person,
        event,
        place,
        theme,
        link,
        evidence,
        open_question,
        topic,
        snapshot,
        candidate,
        summary,
        output,
    ])
    await test_db.commit()

    artifact_response = MemoirSourceArtifactResponse.model_validate(artifact)
    event_response = MemoirEventMemoryResponse.model_validate(event)

    assert artifact_response.artifact_type == MemoirSourceArtifactType.AUDIO
    assert artifact_response.metadata_json == {"duration": 12}
    assert event_response.status == MemoirMemoryStatus.CONFIRMED
    assert event_response.title == "第一次去重庆工作"


async def test_deleting_project_cascades_memoir_memory_data(test_db):
    user = _user("memory-cascade@example.com")
    test_db.add(user)
    await test_db.flush()
    project = await _create_memoir_project(test_db, user)

    session = MemoirSession(project=project, title="访谈")
    question = MemoirQuestion(session=session, prompt="问题")
    answer = MemoirAnswer(question=question, transcript_edited="回答")
    artifact = MemoirSourceArtifact(
        project=project,
        session=session,
        answer=answer,
        artifact_type=MemoirSourceArtifactType.TEXT.value,
    )
    transcript = MemoirTranscriptVersion(
        answer=answer,
        source_artifact=artifact,
        version_type=MemoirTranscriptVersionType.FINAL.value,
        content="回答",
    )
    event_id = str(uuid.uuid4())
    event = MemoirEventMemory(id=event_id, project=project, title="一次经历")
    evidence = MemoirMemoryEvidence(
        project=project,
        memory_type=MemoirMemoryType.EVENT.value,
        memory_id=event_id,
        session=session,
        answer=answer,
        transcript_version=transcript,
    )
    topic = MemoirCoverageTopic(project=project, key="childhood", name="童年")
    snapshot = MemoirCoverageSnapshot(project=project, topic=topic)
    output = MemoirOutput(
        project=project,
        output_type=MemoirOutputType.SESSION_SUMMARY.value,
        title="本次总结",
    )
    version = MemoirOutputVersion(output=output, version=1, content="总结")
    test_db.add_all([session, artifact, transcript, event, evidence, topic, snapshot, output, version])
    await test_db.commit()

    await test_db.delete(project)
    await test_db.commit()

    for model in (
        MemoirSourceArtifact,
        MemoirTranscriptVersion,
        MemoirEventMemory,
        MemoirMemoryEvidence,
        MemoirCoverageTopic,
        MemoirCoverageSnapshot,
        MemoirOutput,
        MemoirOutputVersion,
    ):
        assert await _count(test_db, model) == 0


async def test_memoir_memory_constraints_reject_unknown_values(test_db):
    user = _user("memory-constraint@example.com")
    test_db.add(user)
    await test_db.flush()
    project = await _create_memoir_project(test_db, user)

    test_db.add(MemoirSourceArtifact(project_id=project.id, artifact_type="unknown"))

    with pytest.raises(IntegrityError):
        await test_db.flush()
    await test_db.rollback()

    test_db.add(MemoirPersonMemory(project_id=project.id, canonical_name="老张", status="unknown"))

    with pytest.raises(IntegrityError):
        await test_db.flush()
    await test_db.rollback()
