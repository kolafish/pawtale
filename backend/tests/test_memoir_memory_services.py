import pytest
from fastapi import HTTPException

from app.domain.memoir import (
    MemoirCreatedBy,
    MemoirMemoryStatus,
    MemoirMemoryType,
    MemoirOpenQuestionStatus,
    MemoirQuestionType,
    MemoirSensitivity,
    MemoirSourceArtifactType,
    MemoirTranscriptVersionType,
)
from app.domain.projects.types import ProjectType
from app.models.memoir import MemoirAnswer, MemoirQuestion, MemoirSession
from app.models.projects import Project
from app.models.users import User
from app.schemas.memoir import (
    MemoirEventMemoryCreate,
    MemoirMemoryEvidenceCreate,
    MemoirOpenQuestionCreate,
    MemoirPersonMemoryCreate,
    MemoirPlaceMemoryCreate,
    MemoirSourceArtifactCreate,
    MemoirThemeMemoryCreate,
    MemoirTranscriptVersionCreate,
)
from app.services.memoir.coverage import initialize_default_coverage_topics
from app.services.memoir.memory_repository import (
    add_memory_evidence,
    create_event_memory,
    create_open_question,
    create_person_memory,
    create_place_memory,
    create_theme_memory,
    list_person_memories,
    update_memory_status,
)
from app.services.memoir.source_artifacts import create_source_artifact
from app.services.memoir.transcript_versions import create_transcript_version


def _user(email: str) -> User:
    return User(email=email, password_hash="hash", display_name=email.split("@")[0])


async def _create_project(
    test_db,
    user: User,
    project_type: ProjectType = ProjectType.MEMOIR,
    title: str = "测试回忆录",
) -> Project:
    project = Project(user_id=user.id, type=project_type.value, title=title)
    test_db.add(project)
    await test_db.flush()
    return project


async def _create_interview_answer(
    test_db,
    project: Project,
    title: str = "访谈",
) -> tuple[MemoirSession, MemoirQuestion, MemoirAnswer]:
    session = MemoirSession(project_id=project.id, title=title)
    question = MemoirQuestion(session=session, prompt="第一次工作是什么时候？")
    answer = MemoirAnswer(
        question=question,
        transcript_raw="第一次去重庆厂里上班。",
        transcript_edited="第一次去重庆厂里上班。",
    )
    test_db.add(session)
    await test_db.flush()
    return session, question, answer


async def test_source_artifact_and_transcript_version_services_round_trip(test_db):
    user = _user("source-artifact@example.com")
    test_db.add(user)
    await test_db.flush()
    project = await _create_project(test_db, user)
    session, _, answer = await _create_interview_answer(test_db, project)

    artifact = await create_source_artifact(
        test_db,
        user.id,
        MemoirSourceArtifactCreate(
            project_id=project.id,
            session_id=session.id,
            answer_id=answer.id,
            artifact_type=MemoirSourceArtifactType.AUDIO,
            storage_path="memoir/audio/answer.m4a",
            mime_type="audio/mp4",
            size_bytes=1024,
            metadata_json={"duration_seconds": 12},
        ),
    )
    version = await create_transcript_version(
        test_db,
        user.id,
        MemoirTranscriptVersionCreate(
            answer_id=answer.id,
            source_artifact_id=artifact.id,
            version_type=MemoirTranscriptVersionType.ASR_RAW,
            content=" 第一次去重庆厂里上班。 ",
            provider="test-asr",
            confidence=0.92,
            created_by=MemoirCreatedBy.ASR,
        ),
    )

    assert artifact.artifact_type == MemoirSourceArtifactType.AUDIO
    assert artifact.metadata_json == {"duration_seconds": 12}
    assert version.content == "第一次去重庆厂里上班。"
    assert version.version_type == MemoirTranscriptVersionType.ASR_RAW

    with pytest.raises(HTTPException) as exc:
        await create_transcript_version(
            test_db,
            user.id,
            MemoirTranscriptVersionCreate(
                answer_id=answer.id,
                version_type=MemoirTranscriptVersionType.USER_EDITED,
                content="  ",
            ),
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == "转写文本不能为空"


async def test_memory_repository_creates_structured_memory_and_evidence(test_db):
    user = _user("memory-service@example.com")
    test_db.add(user)
    await test_db.flush()
    project = await _create_project(test_db, user)
    session, question, answer = await _create_interview_answer(test_db, project)
    artifact = await create_source_artifact(
        test_db,
        user.id,
        MemoirSourceArtifactCreate(
            project_id=project.id,
            session_id=session.id,
            answer_id=answer.id,
            artifact_type=MemoirSourceArtifactType.TEXT,
        ),
    )
    transcript = await create_transcript_version(
        test_db,
        user.id,
        MemoirTranscriptVersionCreate(
            answer_id=answer.id,
            source_artifact_id=artifact.id,
            version_type=MemoirTranscriptVersionType.FINAL,
            content=answer.transcript_edited,
        ),
    )

    person = await create_person_memory(
        test_db,
        user.id,
        MemoirPersonMemoryCreate(
            project_id=project.id,
            canonical_name="老张",
            aliases_json=["张师傅"],
            relationship="同事",
            first_seen_session_id=session.id,
            description="厂里的同事",
            confidence=0.8,
        ),
    )
    event = await create_event_memory(
        test_db,
        user.id,
        MemoirEventMemoryCreate(
            project_id=project.id,
            title="第一次去重庆工作",
            life_stage="青年",
            time_text="二十岁左右",
            place_text="重庆",
            process="离开家去厂里上班",
            emotion="紧张又期待",
            importance=0.8,
            confidence=0.9,
        ),
    )
    place = await create_place_memory(
        test_db,
        user.id,
        MemoirPlaceMemoryCreate(
            project_id=project.id,
            name="重庆厂",
            elder_alias="那个厂",
            modern_name="重庆某机械厂",
            place_type="工作地点",
        ),
    )
    theme = await create_theme_memory(
        test_db,
        user.id,
        MemoirThemeMemoryCreate(
            project_id=project.id,
            name="离乡",
            description="离家去外地谋生",
            strength=0.7,
            mention_count=2,
        ),
    )
    open_question = await create_open_question(
        test_db,
        user.id,
        MemoirOpenQuestionCreate(
            project_id=project.id,
            session_id=session.id,
            target_memory_type=MemoirMemoryType.EVENT,
            target_memory_id=event.id,
            question="当时是谁送你去重庆的？",
            question_type=MemoirQuestionType.SCENE,
            reason="关键事件缺少人物和场景细节",
            sensitivity=MemoirSensitivity.LOW,
            priority=0.8,
        ),
    )
    evidence = await add_memory_evidence(
        test_db,
        user.id,
        MemoirMemoryEvidenceCreate(
            project_id=project.id,
            memory_type=MemoirMemoryType.EVENT,
            memory_id=event.id,
            session_id=session.id,
            question_id=question.id,
            answer_id=answer.id,
            transcript_version_id=transcript.id,
            quote="第一次去重庆厂里上班。",
            confidence=0.9,
        ),
    )
    updated_person = await update_memory_status(
        test_db,
        user.id,
        project.id,
        MemoirMemoryType.PERSON,
        person.id,
        MemoirMemoryStatus.CONFIRMED,
    )
    people = await list_person_memories(test_db, user.id, project.id)

    assert person.canonical_name == "老张"
    assert event.title == "第一次去重庆工作"
    assert place.name == "重庆厂"
    assert theme.mention_count == 2
    assert open_question.status == MemoirOpenQuestionStatus.OPEN
    assert evidence.transcript_version_id == transcript.id
    assert updated_person.status == MemoirMemoryStatus.CONFIRMED.value
    assert [item.canonical_name for item in people] == ["老张"]

    with pytest.raises(HTTPException) as exc:
        await update_memory_status(
            test_db,
            user.id,
            project.id,
            MemoirMemoryType.OPEN_QUESTION,
            open_question.id,
            MemoirMemoryStatus.CONFIRMED,
        )

    assert exc.value.status_code == 400


async def test_memory_services_enforce_project_boundaries(test_db):
    user = _user("memory-owner@example.com")
    other = _user("memory-other@example.com")
    test_db.add_all([user, other])
    await test_db.flush()
    project = await _create_project(test_db, user, title="A")
    other_project = await _create_project(test_db, user, title="B")
    session, question, answer = await _create_interview_answer(test_db, project, "A 访谈")
    other_session, other_question, other_answer = await _create_interview_answer(
        test_db,
        other_project,
        "B 访谈",
    )
    artifact = await create_source_artifact(
        test_db,
        user.id,
        MemoirSourceArtifactCreate(
            project_id=project.id,
            session_id=session.id,
            answer_id=answer.id,
            artifact_type=MemoirSourceArtifactType.TEXT,
        ),
    )
    event = await create_event_memory(
        test_db,
        user.id,
        MemoirEventMemoryCreate(project_id=project.id, title="A 的事件"),
    )

    with pytest.raises(HTTPException) as create_exc:
        await create_person_memory(
            test_db,
            other.id,
            MemoirPersonMemoryCreate(project_id=project.id, canonical_name="老张"),
        )

    with pytest.raises(HTTPException) as artifact_exc:
        await create_source_artifact(
            test_db,
            user.id,
            MemoirSourceArtifactCreate(
                project_id=project.id,
                session_id=other_session.id,
                answer_id=other_answer.id,
                artifact_type=MemoirSourceArtifactType.TEXT,
            ),
        )

    with pytest.raises(HTTPException) as transcript_exc:
        await create_transcript_version(
            test_db,
            user.id,
            MemoirTranscriptVersionCreate(
                answer_id=other_answer.id,
                source_artifact_id=artifact.id,
                version_type=MemoirTranscriptVersionType.FINAL,
                content="回答",
            ),
        )

    with pytest.raises(HTTPException) as evidence_exc:
        await add_memory_evidence(
            test_db,
            user.id,
            MemoirMemoryEvidenceCreate(
                project_id=project.id,
                memory_type=MemoirMemoryType.EVENT,
                memory_id=event.id,
                question_id=other_question.id,
            ),
        )

    assert create_exc.value.status_code == 404
    assert artifact_exc.value.status_code == 400
    assert transcript_exc.value.status_code == 404
    assert evidence_exc.value.status_code == 400


async def test_default_coverage_topics_are_idempotent(test_db):
    user = _user("coverage-service@example.com")
    test_db.add(user)
    await test_db.flush()
    project = await _create_project(test_db, user)

    first = await initialize_default_coverage_topics(test_db, user.id, project.id)
    second = await initialize_default_coverage_topics(test_db, user.id, project.id)

    assert len(first) == 10
    assert len(second) == 10
    assert [topic.key for topic in second][:4] == [
        "childhood",
        "school",
        "family_origin",
        "first_job",
    ]
