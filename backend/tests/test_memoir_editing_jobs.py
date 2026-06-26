from datetime import UTC, datetime, timedelta

import pytest

from app.domain.memoir.types import MemoirEditingJobStatus, MemoirEditingJobType
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
    assert claimed == [job.id]

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

    assert claimed == [job.id]
    assert await run_editing_job(test_db, job.id) is True
    await test_db.refresh(job)
    assert job.status == MemoirEditingJobStatus.SUCCEEDED.value
    assert job.attempts == 1
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

    assert await run_editing_job(test_db, job.id) is False
    await test_db.refresh(job)
    assert job.status == MemoirEditingJobStatus.SKIPPED.value
    assert job.error_message == "no handler registered for quality_audit"
