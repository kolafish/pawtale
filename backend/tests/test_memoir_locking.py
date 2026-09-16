import asyncio

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import MemoirEditingJobType
from app.domain.projects.types import ProjectType
from app.models.memoir import MemoirVoiceUtterance
from app.models.projects import MemoirProjectRevisionEvent, Project
from app.services.memoir.editing_jobs import enqueue_editing_job
from app.services.memoir.project_ai_preferences import load_project_ai_model
from app.services.memoir.project_revisions import project_data_revision
from app.services.memoir.utterance_editing_pipeline import (
    claim_transcript_mutation_guard,
    transcript_mutation_guard,
)
from tests.memoir_helpers import create_test_voice_transcript


async def test_revision_events_are_monotonic_and_coalesced_per_transaction(test_db):
    project = Project(title="锁测试", type=ProjectType.MEMOIR.value)
    test_db.add(project)
    await test_db.commit()

    legacy_revision = project.memoir_data_revision
    project.title = "第一次修改"
    await test_db.flush()
    project.description = "同一事务内的第二次修改"
    await test_db.flush()

    events = (
        await test_db.scalars(
            select(MemoirProjectRevisionEvent).where(
                MemoirProjectRevisionEvent.project_id == project.id
            )
        )
    ).all()
    assert len(events) == 1
    first_revision = await project_data_revision(test_db, project)
    assert first_revision > legacy_revision
    assert project.memoir_data_revision == legacy_revision
    await test_db.commit()

    project.title = "下一次修改"
    await test_db.commit()
    assert await project_data_revision(test_db, project) > first_revision


async def test_editing_job_bookkeeping_does_not_advance_content_revision(test_db):
    project = Project(title="任务版本测试", type=ProjectType.MEMOIR.value)
    test_db.add(project)
    await test_db.commit()
    baseline = await project_data_revision(test_db, project)

    job = await enqueue_editing_job(
        test_db,
        project_id=project.id,
        job_type=MemoirEditingJobType.UPDATE_TOPIC_MAP,
        dedupe_key="lock-test:job",
    )
    await test_db.commit()
    job.result_json = {"stage": "running"}
    await test_db.commit()

    assert await project_data_revision(test_db, project) == baseline


async def test_child_revision_does_not_wait_for_project_metadata_update(
    client,
    test_db,
):
    created = await create_test_voice_transcript(client, test_db)
    project_id = created["session"]["project_id"]
    session_maker = async_sessionmaker(
        test_db.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with session_maker() as project_writer, session_maker() as material_writer:
        await project_writer.execute(
            update(Project)
            .where(Project.id == project_id)
            .values(description="尚未提交的项目资料")
        )
        await asyncio.wait_for(
            material_writer.execute(
                update(MemoirVoiceUtterance)
                .where(MemoirVoiceUtterance.id == created["utterance_id"])
                .values(transcript_edited="并发保存的讲述文字")
            ),
            timeout=1,
        )
        await material_writer.commit()
        await project_writer.rollback()


async def test_background_model_resolution_is_read_only(test_db):
    project = Project(
        title="模型设置测试",
        type=ProjectType.MEMOIR.value,
        metadata_json={"ai_model": "stored-model"},
    )
    test_db.add(project)
    await test_db.commit()

    assert await load_project_ai_model(test_db, project.id, "job-model") == "job-model"
    await test_db.refresh(project)
    assert project.metadata_json == {"ai_model": "stored-model"}
    assert not test_db.is_modified(project)


async def test_transcript_guard_locks_without_emitting_content_revision(
    client,
    test_db,
):
    created = await create_test_voice_transcript(client, test_db)
    session_maker = async_sessionmaker(
        test_db.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_maker() as db:
        utterance = await db.scalar(
            select(MemoirVoiceUtterance)
            .where(MemoirVoiceUtterance.id == created["utterance_id"])
            .options(selectinload(MemoirVoiceUtterance.chat_message))
        )
        before = await db.scalar(
            select(func.count(MemoirProjectRevisionEvent.revision)).where(
                MemoirProjectRevisionEvent.project_id == created["session"]["project_id"]
            )
        )
        assert await claim_transcript_mutation_guard(
            db,
            utterance=utterance,
            expected=transcript_mutation_guard(utterance),
        )
        after = await db.scalar(
            select(func.count(MemoirProjectRevisionEvent.revision)).where(
                MemoirProjectRevisionEvent.project_id == created["session"]["project_id"]
            )
        )
        assert after == before
        await db.rollback()


async def test_editing_job_trigger_is_absent(test_db):
    trigger_exists = await test_db.scalar(
        text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM pg_trigger
                WHERE tgname = 'trg_memoir_revision_memoir_editing_jobs'
                  AND NOT tgisinternal
            )
            """
        )
    )
    assert trigger_exists is False
