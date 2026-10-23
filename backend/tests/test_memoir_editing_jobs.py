import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
    run_due_editing_jobs,
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


@pytest.mark.db_independent
async def test_enqueue_editing_job_deduplicates_concurrent_insert(test_db):
    session_maker = async_sessionmaker(
        test_db.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )
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
    assert job.error_message == "后台任务暂不支持此操作，请联系管理员"
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
    assert "已保存的进度会保留" in job.error_message
    assert job.result_json["failure"]["error_type"] == "RuntimeError"

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
    assert "已保存的进度会保留" in job.error_message
    assert job.result_json["failure"]["error_type"] == "TimeoutError"
    assert "error_type" not in job.result_json


async def test_run_editing_job_deadline_preserves_progress_and_fails_linked_event(test_db, monkeypatch):
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
    progress_saved = asyncio.Event()

    async def handler(db, current_job):
        current_job.result_json = {"completed_section_ids": ["section-1"]}
        await db.commit()
        progress_saved.set()
        try:
            await asyncio.Event().wait()
        finally:
            handler_cancelled.set()

    register_editing_job_handler(
        MemoirEditingJobType.GENERATE_BOOK_EDITORIAL_COPY,
        handler,
    )
    lease = (await claim_due_editing_jobs(test_db, now=_utcnow(), limit=1))[0]

    async def deadline_after_checkpoint(tasks, *, timeout):
        # Exercise cancellation after a real commit without assuming a 10 ms DB round trip.
        await progress_saved.wait()
        return set(), tasks

    monkeypatch.setattr(asyncio, "wait", deadline_after_checkpoint)
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


@pytest.mark.db_independent
async def test_editing_job_claim_is_atomic_across_workers(test_db):
    session_maker = async_sessionmaker(
        test_db.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )
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


@pytest.mark.db_independent
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


@pytest.mark.db_independent
async def test_editing_worker_runs_claimed_jobs_concurrently(test_db):
    session_maker = async_sessionmaker(
        test_db.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    active_handlers = 0
    peak_handlers = 0

    async def handler(db, current_job):
        nonlocal active_handlers, peak_handlers
        active_handlers += 1
        peak_handlers = max(peak_handlers, active_handlers)
        await asyncio.sleep(0.2)
        active_handlers -= 1
        return EditingJobResult(result_json={"job_id": current_job.id})

    async with session_maker() as db:
        for index in range(2):
            project = Project(
                title=f"并发任务 {index}",
                type=ProjectType.MEMOIR.value,
            )
            db.add(project)
            await db.flush()
            await enqueue_editing_job(
                db,
                project_id=project.id,
                job_type=MemoirEditingJobType.ORGANIZE_MATERIAL,
                run_after=_utcnow() - timedelta(seconds=1),
                dedupe_key=f"route:concurrent:{index}",
            )
        await db.commit()

    register_editing_job_handler(MemoirEditingJobType.ORGANIZE_MATERIAL, handler)
    assert await run_due_editing_jobs(session_maker, limit=2) == 2
    assert peak_handlers == 2


@pytest.mark.db_independent
async def test_editing_worker_drains_jobs_enqueued_by_completed_jobs(test_db):
    session_maker = async_sessionmaker(
        test_db.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )

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


@pytest.mark.db_independent
async def test_source_arriving_during_execution_is_processed_by_next_durable_run(test_db):
    project = await _create_project(test_db)
    job = await enqueue_editing_job(test_db, project_id=project.id,
        job_type=MemoirEditingJobType.ORGANIZE_MATERIAL, payload_json={"source": "first"}, dedupe_key="growth")
    seen = []
    async def handler(db, current):
        seen.append(current.payload_json["source"])
        if len(seen) == 1:
            async with AsyncSession(test_db.bind, expire_on_commit=False) as other:
                await enqueue_editing_job(other, project_id=project.id,
                    job_type=MemoirEditingJobType.ORGANIZE_MATERIAL, payload_json={"source": "second"},
                    dedupe_key="growth", preserve_progress=True, reschedule_running=True)
                await other.commit()
        return EditingJobResult(result_json={"checkpoint": "saved"})
    register_editing_job_handler(MemoirEditingJobType.ORGANIZE_MATERIAL, handler)
    lease = (await claim_due_editing_jobs(test_db, now=_utcnow(), limit=1))[0]
    assert not await run_editing_job(test_db, lease)
    await test_db.refresh(job)
    assert job.status == "pending"
    assert job.first_started_at is None
    assert job.result_json == {"checkpoint": "saved"}
    lease = (await claim_due_editing_jobs(test_db, now=_utcnow(), limit=1))[0]
    assert await run_editing_job(test_db, lease)
    assert seen == ["first", "second"]


@pytest.mark.db_independent
@pytest.mark.parametrize("worker", [False, True])
async def test_reclaimed_job_cannot_reset_its_total_deadline(test_db, worker):
    project = await _create_project(test_db)
    job = await enqueue_editing_job(test_db, project_id=project.id,
                                   job_type=MemoirEditingJobType.ORGANIZE_MATERIAL)
    job.first_started_at = _utcnow() - timedelta(hours=2)
    job.result_json = {"checkpoint": "saved"}
    await test_db.commit()
    async def must_not_run(*args):
        pytest.fail("expired job was restarted")
    register_editing_job_handler(MemoirEditingJobType.ORGANIZE_MATERIAL, must_not_run)
    if worker:
        assert await run_due_editing_jobs(async_sessionmaker(test_db.bind, expire_on_commit=False)) == 0
    else:
        lease = (await claim_due_editing_jobs(test_db, now=_utcnow(), limit=1))[0]
        assert not await run_editing_job(test_db, lease)
    await test_db.refresh(job)
    assert job.status == "failed"
    assert job.result_json["checkpoint"] == "saved"
    assert job.result_json["error_type"] == "job_deadline_exceeded"
