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
    run_available_editing_jobs,
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
        job_type=MemoirEditingJobType.ORGANIZE_MATERIAL,
        payload_json={"utterance_ids": ["u1"]},
        dedupe_key="route:u1",
    )
    second = await enqueue_editing_job(
        test_db,
        project_id=project.id,
        job_type=MemoirEditingJobType.ORGANIZE_MATERIAL,
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
                    job_type=MemoirEditingJobType.UPDATE_TOPIC_MAP,
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
        job_type=MemoirEditingJobType.ORGANIZE_MATERIAL,
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
        job_type=MemoirEditingJobType.ORGANIZE_MATERIAL,
        payload_json={"utterance_ids": ["u1"], "source": "new"},
        dedupe_key="route:u1",
    )

    assert duplicate.id == job.id
    assert duplicate.status == MemoirEditingJobStatus.RUNNING.value
    assert duplicate.payload_json == {"utterance_ids": ["u1"]}


async def test_claim_due_editing_jobs_can_filter_by_job_type_and_project(test_db):
    project = await _create_project(test_db)
    other_project = await _create_project(test_db)
    ingestion = await enqueue_editing_job(
        test_db,
        project_id=project.id,
        job_type=MemoirEditingJobType.UPDATE_TOPIC_MAP,
        run_after=_utcnow() - timedelta(seconds=1),
    )
    routing = await enqueue_editing_job(
        test_db,
        project_id=project.id,
        job_type=MemoirEditingJobType.ORGANIZE_MATERIAL,
        run_after=_utcnow() - timedelta(seconds=1),
    )
    other_ingestion = await enqueue_editing_job(
        test_db,
        project_id=other_project.id,
        job_type=MemoirEditingJobType.UPDATE_TOPIC_MAP,
        run_after=_utcnow() - timedelta(seconds=1),
    )
    await test_db.commit()

    claimed = await claim_due_editing_jobs(
        test_db,
        now=_utcnow(),
        limit=5,
        job_type=MemoirEditingJobType.UPDATE_TOPIC_MAP,
        project_id=project.id,
    )

    assert [lease.job_id for lease in claimed] == [ingestion.id]
    await test_db.refresh(routing)
    assert routing.status == MemoirEditingJobStatus.PENDING.value
    await test_db.refresh(other_ingestion)
    assert other_ingestion.status == MemoirEditingJobStatus.PENDING.value


async def test_claim_due_editing_jobs_deprioritizes_policy_evaluations(test_db):
    project = await _create_project(test_db)
    shadow = await enqueue_editing_job(
        test_db,
        project_id=project.id,
        job_type=MemoirEditingJobType.EVALUATE_INTERVIEW_POLICY_SHADOW,
        run_after=_utcnow() - timedelta(minutes=1),
    )
    outcome = await enqueue_editing_job(
        test_db,
        project_id=project.id,
        job_type=MemoirEditingJobType.EVALUATE_INTERVIEW_POLICY_OUTCOME,
        run_after=_utcnow() - timedelta(minutes=1),
    )
    routing = await enqueue_editing_job(
        test_db,
        project_id=project.id,
        job_type=MemoirEditingJobType.ORGANIZE_MATERIAL,
        run_after=_utcnow() - timedelta(seconds=1),
    )
    await test_db.commit()

    claimed = await claim_due_editing_jobs(
        test_db,
        now=_utcnow(),
        limit=1,
    )

    assert [lease.job_id for lease in claimed] == [routing.id]
    await test_db.refresh(shadow)
    assert shadow.status == MemoirEditingJobStatus.PENDING.value
    await test_db.refresh(outcome)
    assert outcome.status == MemoirEditingJobStatus.PENDING.value


async def test_claim_and_run_editing_job_with_registered_handler(test_db):
    project = await _create_project(test_db)
    job = await enqueue_editing_job(
        test_db,
        project_id=project.id,
        job_type=MemoirEditingJobType.ORGANIZE_MATERIAL,
        payload_json={"utterance_ids": ["u1"]},
        run_after=_utcnow() - timedelta(seconds=1),
        dedupe_key="route:u1",
    )
    await test_db.commit()

    async def handler(db, current_job):
        return EditingJobResult(result_json={"handled": current_job.payload_json["utterance_ids"]})

    register_editing_job_handler(MemoirEditingJobType.ORGANIZE_MATERIAL, handler)
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
        job_type=MemoirEditingJobType.GENERATE_BOOK_EDITORIAL_COPY,
        payload_json={},
    )
    await test_db.commit()

    lease = (await claim_due_editing_jobs(test_db, now=_utcnow(), limit=1))[0]
    assert await run_editing_job(test_db, lease) is False
    await test_db.refresh(job)
    assert job.status == MemoirEditingJobStatus.SKIPPED.value
    assert job.error_message == "no handler registered for generate_book_editorial_copy"
    event = await test_db.scalar(select(MemoirAutomationEvent).where(MemoirAutomationEvent.project_id == project.id))
    assert event is None


async def test_run_editing_job_records_failure_without_legacy_business_event(test_db):
    project = await _create_project(test_db)
    project_id = project.id
    job = await enqueue_editing_job(
        test_db,
        project_id=project_id,
        job_type=MemoirEditingJobType.ORGANIZE_MATERIAL,
        payload_json={"utterance_ids": ["u1"], "source": "test_failure"},
        dedupe_key="route:u1",
    )
    await test_db.commit()

    async def handler(db, current_job):
        raise RuntimeError("provider down")

    register_editing_job_handler(MemoirEditingJobType.ORGANIZE_MATERIAL, handler)

    lease = (await claim_due_editing_jobs(test_db, now=_utcnow(), limit=1))[0]
    assert await run_editing_job(test_db, lease) is False
    await test_db.refresh(job)
    assert job.status == MemoirEditingJobStatus.FAILED.value
    assert job.error_message == "provider down"

    event = await test_db.scalar(
        select(MemoirAutomationEvent).where(MemoirAutomationEvent.project_id == project_id)
    )
    assert event is None


async def test_provider_timeout_is_not_misclassified_as_job_deadline(test_db):
    project = await _create_project(test_db)
    job = await enqueue_editing_job(
        test_db,
        project_id=project.id,
        job_type=MemoirEditingJobType.ORGANIZE_MATERIAL,
    )
    await test_db.commit()

    async def handler(_db, _current_job):
        raise TimeoutError("provider deadline exceeded")

    register_editing_job_handler(MemoirEditingJobType.ORGANIZE_MATERIAL, handler)
    lease = (await claim_due_editing_jobs(test_db, now=_utcnow(), limit=1))[0]

    assert await run_editing_job(test_db, lease, timeout_seconds=1) is False
    await test_db.refresh(job)
    assert job.status == MemoirEditingJobStatus.FAILED.value
    assert job.error_message == "provider deadline exceeded"
    assert "error_type" not in job.result_json


async def test_run_editing_job_deadline_preserves_progress_and_fails_linked_event(test_db):
    project = await _create_project(test_db)
    job = await enqueue_editing_job(
        test_db,
        project_id=project.id,
        job_type=MemoirEditingJobType.GENERATE_BOOK_EDITORIAL_COPY,
        payload_json={"target_label": "前言"},
    )
    event = MemoirAutomationEvent(
        project_id=project.id,
        editing_job_id=job.id,
        event_type=MemoirAutomationEventType.BOOK_EDITORIAL_COPY_GENERATED.value,
        status=MemoirAutomationEventStatus.PENDING.value,
        summary_text="前言已提交后台重生成。",
    )
    test_db.add(event)
    await test_db.commit()
    handler_cancelled = asyncio.Event()

    async def handler(db, current_job):
        current_job.result_json = {"completed_section_ids": ["section-1"]}
        await db.commit()
        try:
            await asyncio.Event().wait()
        finally:
            handler_cancelled.set()

    register_editing_job_handler(
        MemoirEditingJobType.GENERATE_BOOK_EDITORIAL_COPY,
        handler,
    )
    lease = (await claim_due_editing_jobs(test_db, now=_utcnow(), limit=1))[0]

    assert await run_editing_job(test_db, lease, timeout_seconds=0.01) is False
    assert handler_cancelled.is_set()
    await test_db.refresh(job)
    await test_db.refresh(event)
    assert job.status == MemoirEditingJobStatus.FAILED.value
    assert job.result_json["completed_section_ids"] == ["section-1"]
    assert job.result_json["error_type"] == "job_deadline_exceeded"
    assert job.result_json["timeout_seconds"] == 0.01
    assert "已保存的进度会保留" in job.error_message
    assert event.status == MemoirAutomationEventStatus.FAILED.value
    assert event.metadata_json["error_type"] == "job_deadline_exceeded"
    assert event.metadata_json["timeout_seconds"] == 0.01


async def test_expired_editing_job_lease_is_reclaimed_and_fences_old_worker(test_db):
    project = await _create_project(test_db)
    claimed_at = _utcnow()
    job = await enqueue_editing_job(
        test_db,
        project_id=project.id,
        job_type=MemoirEditingJobType.ORGANIZE_MATERIAL,
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
                job_type=MemoirEditingJobType.ORGANIZE_MATERIAL,
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


async def test_postgres_editing_claim_skips_locked_queue_head(test_db):
    if test_db.get_bind().dialect.name != "postgresql":
        pytest.skip("requires PostgreSQL row-level locking")
    due_at = _utcnow()
    project = await _create_project(test_db)
    first = await enqueue_editing_job(
        test_db,
        project_id=project.id,
        job_type=MemoirEditingJobType.ORGANIZE_MATERIAL,
        run_after=due_at - timedelta(seconds=2),
        dedupe_key="skip-locked:first",
    )
    second = await enqueue_editing_job(
        test_db,
        project_id=project.id,
        job_type=MemoirEditingJobType.ORGANIZE_MATERIAL,
        run_after=due_at - timedelta(seconds=1),
        dedupe_key="skip-locked:second",
    )
    await test_db.commit()
    session_maker = async_sessionmaker(
        test_db.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with session_maker() as locking_db:
        await locking_db.execute(
            select(MemoirEditingJob)
            .where(MemoirEditingJob.id == first.id)
            .with_for_update()
        )
        async with session_maker() as claiming_db:
            leases = await asyncio.wait_for(
                claim_due_editing_jobs(
                    claiming_db,
                    now=due_at,
                    limit=1,
                    lease_seconds=30,
                ),
                timeout=2,
            )
        await locking_db.rollback()

    assert [lease.job_id for lease in leases] == [second.id]


async def test_sqlite_editing_worker_serializes_claimed_job_writes(tmp_path):
    register_models()
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'editing-serial.db'}", echo=False)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    active_handlers = 0
    peak_handlers = 0

    async def handler(db, current_job):
        nonlocal active_handlers, peak_handlers
        active_handlers += 1
        peak_handlers = max(peak_handlers, active_handlers)
        await asyncio.sleep(0.01)
        active_handlers -= 1
        return EditingJobResult(result_json={"job_id": current_job.id})

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_maker() as db:
            project = Project(title="串行任务", type=ProjectType.MEMOIR.value)
            db.add(project)
            await db.flush()
            for index in range(2):
                await enqueue_editing_job(
                    db,
                    project_id=project.id,
                    job_type=MemoirEditingJobType.ORGANIZE_MATERIAL,
                    run_after=_utcnow() - timedelta(seconds=1),
                    dedupe_key=f"route:serial:{index}",
                )
            await db.commit()

        register_editing_job_handler(MemoirEditingJobType.ORGANIZE_MATERIAL, handler)
        assert await run_available_editing_jobs(session_maker) == 2
        assert peak_handlers == 1
    finally:
        await engine.dispose()


async def test_editing_worker_drains_jobs_enqueued_by_completed_jobs(tmp_path):
    register_models()
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'editing-drain.db'}", echo=False)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def first_handler(db, current_job):
        await enqueue_editing_job(
            db,
            project_id=current_job.project_id,
            job_type=MemoirEditingJobType.GENERATE_BOOK_EDITORIAL_COPY,
            run_after=_utcnow() - timedelta(seconds=1),
            dedupe_key=f"quality:after:{current_job.id}",
        )
        return EditingJobResult()

    async def second_handler(db, current_job):
        return EditingJobResult(result_json={"drained": current_job.id})

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_maker() as db:
            project = Project(title="连续任务", type=ProjectType.MEMOIR.value)
            db.add(project)
            await db.flush()
            await enqueue_editing_job(
                db,
                project_id=project.id,
                job_type=MemoirEditingJobType.ORGANIZE_MATERIAL,
                run_after=_utcnow() - timedelta(seconds=1),
                dedupe_key="route:drain",
            )
            await db.commit()

        register_editing_job_handler(MemoirEditingJobType.ORGANIZE_MATERIAL, first_handler)
        register_editing_job_handler(MemoirEditingJobType.GENERATE_BOOK_EDITORIAL_COPY, second_handler)
        assert await run_available_editing_jobs(session_maker) == 2
        async with session_maker() as db:
            statuses = (await db.scalars(select(MemoirEditingJob.status))).all()
            assert len(statuses) == 2
            assert set(statuses) == {MemoirEditingJobStatus.SUCCEEDED.value}
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
