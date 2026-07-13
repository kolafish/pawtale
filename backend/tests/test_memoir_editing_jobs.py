import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, register_models
from app.domain.memoir.types import (
    MemoirAutomationEventStatus,
    MemoirAutomationEventType,
    MemoirEditingJobStatus,
    MemoirEditingJobType,
)
from app.models.memoir import MemoirAutomationEvent, MemoirEditingJob
from app.domain.projects.types import ProjectType
from app.models.projects import Project
from app.services.memoir.editing_jobs import (
    EditingJobResult,
    claim_due_editing_jobs,
    clear_editing_job_handlers,
    enqueue_editing_job,
    register_editing_job_handler,
    run_editing_job,
)
from app.services.memoir.job_leases import JobLeaseLostError, run_with_lease_heartbeat


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@pytest.fixture(autouse=True)
def clear_handlers():
    clear_editing_job_handlers()
    yield
    clear_editing_job_handlers()


async def _create_project(test_db) -> Project:
    project = Project(title="爷爷主题", type=ProjectType.MEMOIR.value)
    test_db.add(project)
    await test_db.flush()
    return project


async def test_enqueue_editing_job_deduplicates_pending_job(test_db):
    project = await _create_project(test_db)
    first = await enqueue_editing_job(
        test_db,
        project_id=project.id,
        job_type=MemoirEditingJobType.ROUTE_SOURCES,
        payload_json={"utterance_ids": ["u1"]},
        dedupe_key="route:u1",
    )
    second = await enqueue_editing_job(
        test_db,
        project_id=project.id,
        job_type=MemoirEditingJobType.ROUTE_SOURCES,
        payload_json={"utterance_ids": ["u1"], "source": "updated"},
        dedupe_key="route:u1",
    )

    assert second.id == first.id
    assert second.status == MemoirEditingJobStatus.PENDING.value
    assert second.payload_json == {"utterance_ids": ["u1"], "source": "updated"}


async def test_enqueue_editing_job_deduplicates_concurrent_insert(tmp_path):
    register_models()
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'editing-enqueue.db'}", echo=False)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_maker() as db:
            project = Project(title="并发排队", type=ProjectType.MEMOIR.value)
            db.add(project)
            await db.commit()
            project_id = project.id

        async def enqueue_once() -> str:
            async with session_maker() as db:
                job = await enqueue_editing_job(
                    db,
                    project_id=project_id,
                    job_type=MemoirEditingJobType.INGEST_NARRATIVE_STATE,
                    dedupe_key="ingest:concurrent",
                )
                await db.commit()
                return job.id

        job_ids = await asyncio.gather(enqueue_once(), enqueue_once())

        assert job_ids[0] == job_ids[1]
        async with session_maker() as db:
            jobs = (await db.scalars(select(MemoirEditingJob))).all()
            assert len(jobs) == 1
    finally:
        await engine.dispose()


async def test_enqueue_editing_job_does_not_reset_running_job(test_db):
    project = await _create_project(test_db)
    job = await enqueue_editing_job(
        test_db,
        project_id=project.id,
        job_type=MemoirEditingJobType.ROUTE_SOURCES,
        payload_json={"utterance_ids": ["u1"]},
        run_after=_utcnow() - timedelta(seconds=1),
        dedupe_key="route:u1",
    )
    await test_db.commit()
    claimed = await claim_due_editing_jobs(test_db, now=_utcnow(), limit=1)
    assert [lease.job_id for lease in claimed] == [job.id]

    duplicate = await enqueue_editing_job(
        test_db,
        project_id=project.id,
        job_type=MemoirEditingJobType.ROUTE_SOURCES,
        payload_json={"utterance_ids": ["u1"], "source": "new"},
        dedupe_key="route:u1",
    )

    assert duplicate.id == job.id
    assert duplicate.status == MemoirEditingJobStatus.RUNNING.value
    assert duplicate.payload_json == {"utterance_ids": ["u1"]}


async def test_claim_and_run_editing_job_with_registered_handler(test_db):
    project = await _create_project(test_db)
    job = await enqueue_editing_job(
        test_db,
        project_id=project.id,
        job_type=MemoirEditingJobType.ROUTE_SOURCES,
        payload_json={"utterance_ids": ["u1"]},
        run_after=_utcnow() - timedelta(seconds=1),
        dedupe_key="route:u1",
    )
    await test_db.commit()

    async def handler(db, current_job):
        return EditingJobResult(result_json={"handled": current_job.payload_json["utterance_ids"]})

    register_editing_job_handler(MemoirEditingJobType.ROUTE_SOURCES, handler)
    claimed = await claim_due_editing_jobs(test_db, now=_utcnow(), limit=1)

    assert [lease.job_id for lease in claimed] == [job.id]
    assert await run_editing_job(test_db, claimed[0]) is True
    await test_db.refresh(job)
    assert job.status == MemoirEditingJobStatus.SUCCEEDED.value
    assert job.attempts == 1
    assert job.lease_token is None
    assert job.lease_expires_at is None
    assert job.locked_at is None
    assert job.result_json == {"handled": ["u1"]}


async def test_run_editing_job_skips_when_handler_missing(test_db):
    project = await _create_project(test_db)
    job = await enqueue_editing_job(
        test_db,
        project_id=project.id,
        job_type=MemoirEditingJobType.QUALITY_AUDIT,
        payload_json={},
    )
    await test_db.commit()

    lease = (await claim_due_editing_jobs(test_db, now=_utcnow(), limit=1))[0]
    assert await run_editing_job(test_db, lease) is False
    await test_db.refresh(job)
    assert job.status == MemoirEditingJobStatus.SKIPPED.value
    assert job.error_message == "no handler registered for quality_audit"
    event = await test_db.scalar(select(MemoirAutomationEvent).where(MemoirAutomationEvent.project_id == project.id))
    assert event is None


async def test_run_editing_job_records_failed_automation_event(test_db):
    project = await _create_project(test_db)
    project_id = project.id
    job = await enqueue_editing_job(
        test_db,
        project_id=project_id,
        job_type=MemoirEditingJobType.ROUTE_SOURCES,
        payload_json={"utterance_ids": ["u1"], "source": "test_failure"},
        dedupe_key="route:u1",
    )
    await test_db.commit()

    async def handler(db, current_job):
        raise RuntimeError("provider down")

    register_editing_job_handler(MemoirEditingJobType.ROUTE_SOURCES, handler)

    lease = (await claim_due_editing_jobs(test_db, now=_utcnow(), limit=1))[0]
    assert await run_editing_job(test_db, lease) is False
    await test_db.refresh(job)
    assert job.status == MemoirEditingJobStatus.FAILED.value
    assert job.error_message == "provider down"

    event = await test_db.scalar(
        select(MemoirAutomationEvent).where(MemoirAutomationEvent.project_id == project_id)
    )
    assert event is not None
    assert event.event_type == MemoirAutomationEventType.SOURCE_HELD.value
    assert event.status == MemoirAutomationEventStatus.FAILED.value
    assert event.utterance_ids_json == ["u1"]
    assert event.summary_text == "自动判断讲述归属失败，稍后会继续重试或可手动整理。"
    assert event.metadata_json["source"] == "editing_job_failure"
    assert event.metadata_json["event_signature"] == f"editing_job_failed:{job.id}"
    assert event.metadata_json["editing_job_id"] == job.id
    assert event.metadata_json["job_type"] == "route_sources"
    assert event.metadata_json["dedupe_key"] == "route:u1"
    assert event.metadata_json["attempts"] == 1
    assert event.metadata_json["pipeline_source"] == "test_failure"
    assert event.metadata_json["error_message"] == "provider down"
    event.metadata_json = {
        **event.metadata_json,
        "acknowledged_at": "2026-07-01T00:00:00+00:00",
        "acknowledged_by_user": True,
        "voice_played_at": "2026-07-01T00:00:00+00:00",
        "voice_played_session_id": "voice-1",
    }
    test_db.add(event)
    await test_db.commit()

    await enqueue_editing_job(
        test_db,
        project_id=project_id,
        job_type=MemoirEditingJobType.ROUTE_SOURCES,
        payload_json={"utterance_ids": ["u1"], "source": "test_failure_retry"},
        dedupe_key="route:u1",
        replace_completed=True,
    )
    await test_db.commit()

    retry_lease = (await claim_due_editing_jobs(test_db, now=_utcnow(), limit=1))[0]
    assert await run_editing_job(test_db, retry_lease) is False
    events = (
        await test_db.execute(
            select(MemoirAutomationEvent).where(MemoirAutomationEvent.project_id == project_id)
        )
    ).scalars().all()
    assert len(events) == 1
    assert events[0].metadata_json["attempts"] == 1
    assert events[0].metadata_json["pipeline_source"] == "test_failure_retry"
    assert "acknowledged_at" not in events[0].metadata_json
    assert "acknowledged_by_user" not in events[0].metadata_json
    assert "voice_played_at" not in events[0].metadata_json
    assert "voice_played_session_id" not in events[0].metadata_json


async def test_expired_editing_job_lease_is_reclaimed_and_fences_old_worker(test_db):
    project = await _create_project(test_db)
    claimed_at = _utcnow()
    job = await enqueue_editing_job(
        test_db,
        project_id=project.id,
        job_type=MemoirEditingJobType.ROUTE_SOURCES,
        payload_json={"utterance_ids": ["u1"]},
        run_after=claimed_at - timedelta(seconds=1),
    )
    await test_db.commit()

    first = await claim_due_editing_jobs(
        test_db,
        now=claimed_at,
        limit=1,
        lease_seconds=30,
    )
    still_leased = await claim_due_editing_jobs(
        test_db,
        now=claimed_at + timedelta(seconds=29),
        limit=1,
        lease_seconds=30,
    )
    reclaimed = await claim_due_editing_jobs(
        test_db,
        now=claimed_at + timedelta(seconds=31),
        limit=1,
        lease_seconds=30,
    )

    assert len(first) == 1
    assert still_leased == []
    assert len(reclaimed) == 1
    assert reclaimed[0].job_id == job.id
    assert reclaimed[0].token != first[0].token
    assert await run_editing_job(test_db, first[0]) is False
    await test_db.refresh(job)
    assert job.status == MemoirEditingJobStatus.RUNNING.value
    assert job.lease_token == reclaimed[0].token
    assert job.attempts == 2


async def test_editing_job_claim_is_atomic_across_workers(tmp_path):
    register_models()
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'editing-claims.db'}", echo=False)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        due_at = _utcnow()
        async with session_maker() as db:
            project = Project(title="并发领取", type=ProjectType.MEMOIR.value)
            db.add(project)
            await db.flush()
            job = await enqueue_editing_job(
                db,
                project_id=project.id,
                job_type=MemoirEditingJobType.ROUTE_SOURCES,
                payload_json={"utterance_ids": ["u1"]},
                run_after=due_at - timedelta(seconds=1),
            )
            job_id = job.id
            await db.commit()

        async def claim_once():
            async with session_maker() as db:
                return await claim_due_editing_jobs(
                    db,
                    now=due_at,
                    limit=1,
                    lease_seconds=30,
                )

        claims = await asyncio.gather(claim_once(), claim_once())
        leases = [lease for worker_claims in claims for lease in worker_claims]

        assert len(leases) == 1
        assert leases[0].job_id == job_id
        async with session_maker() as db:
            stored_job = await db.get(MemoirEditingJob, job_id)
            assert stored_job is not None
            assert stored_job.status == MemoirEditingJobStatus.RUNNING.value
            assert stored_job.lease_token == leases[0].token
            assert stored_job.attempts == 1
    finally:
        await engine.dispose()


async def test_lease_heartbeat_cancels_work_after_ownership_is_lost():
    work_cancelled = asyncio.Event()

    async def work():
        try:
            await asyncio.Event().wait()
        finally:
            work_cancelled.set()

    async def lose_lease():
        return False

    with pytest.raises(JobLeaseLostError):
        await run_with_lease_heartbeat(
            work,
            lose_lease,
            interval_seconds=0.01,
        )

    assert work_cancelled.is_set()
