from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.domain.memoir.types import (
    MemoirAutomationEventStatus,
    MemoirAutomationEventType,
    MemoirEditingJobStatus,
    MemoirEditingJobType,
)
from app.models.memoir import MemoirAutomationEvent
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
    event = await test_db.scalar(select(MemoirAutomationEvent).where(MemoirAutomationEvent.project_id == project.id))
    assert event is None


async def test_run_editing_job_records_failed_automation_event(test_db):
    project = await _create_project(test_db)
    job = await enqueue_editing_job(
        test_db,
        project_id=project.id,
        job_type=MemoirEditingJobType.ROUTE_SOURCES,
        payload_json={"utterance_ids": ["u1"], "source": "test_failure"},
        dedupe_key="route:u1",
    )
    await test_db.commit()

    async def handler(db, current_job):
        raise RuntimeError("provider down")

    register_editing_job_handler(MemoirEditingJobType.ROUTE_SOURCES, handler)

    assert await run_editing_job(test_db, job.id) is False
    await test_db.refresh(job)
    assert job.status == MemoirEditingJobStatus.FAILED.value
    assert job.error_message == "provider down"

    event = await test_db.scalar(
        select(MemoirAutomationEvent).where(MemoirAutomationEvent.project_id == project.id)
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
        project_id=project.id,
        job_type=MemoirEditingJobType.ROUTE_SOURCES,
        payload_json={"utterance_ids": ["u1"], "source": "test_failure_retry"},
        dedupe_key="route:u1",
        replace_completed=True,
    )
    await test_db.commit()

    assert await run_editing_job(test_db, job.id) is False
    events = (
        await test_db.execute(
            select(MemoirAutomationEvent).where(MemoirAutomationEvent.project_id == project.id)
        )
    ).scalars().all()
    assert len(events) == 1
    assert events[0].metadata_json["attempts"] == 1
    assert events[0].metadata_json["pipeline_source"] == "test_failure_retry"
    assert "acknowledged_at" not in events[0].metadata_json
    assert "acknowledged_by_user" not in events[0].metadata_json
    assert "voice_played_at" not in events[0].metadata_json
    assert "voice_played_session_id" not in events[0].metadata_json
