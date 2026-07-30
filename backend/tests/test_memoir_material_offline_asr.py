from __future__ import annotations

from sqlalchemy import select

from app.domain.memoir.types import MemoirCreatedBy, MemoirEditingJobType, MemoirTranscriptVersionType
from app.models.memoir import MemoirEditingJob, MemoirTranscriptVersion
from app.models.projects import Project
from app.services.memoir import material_offline_asr_jobs
from app.services.memoir.editing_job_handlers import register_memoir_editing_job_handlers
from app.services.memoir.editing_jobs import clear_editing_job_handlers
from tests.memoir_helpers import create_test_voice_transcript, run_test_editing_job


async def _set_narrator_origin(test_db, project_id: str, origin: str) -> None:
    project = await test_db.get(Project, project_id)
    assert project is not None
    metadata = dict(project.metadata_json or {})
    metadata["memoir_profile"] = {"narrator_origin": origin}
    metadata["transcript_polish_level"] = "medium"
    project.metadata_json = metadata
    await test_db.commit()


async def test_material_offline_asr_endpoint_queues_and_runs_one_sichuan_dialect_job(
    client,
    test_db,
    monkeypatch,
):
    created = await create_test_voice_transcript(client, test_db)
    await _set_narrator_origin(test_db, created["session"]["project_id"], "四川成都")
    url = f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/offline-asr"

    response = await client.post(url, json={})
    duplicate = await client.post(url, json={})

    assert response.status_code == 202
    assert duplicate.status_code == 202
    state = response.json()
    assert state["status"] == "pending"
    assert state["job_id"] == duplicate.json()["job_id"]
    assert state["can_start"] is False
    assert state["providers"] == ["volc", "stepfun"]
    assert state["narrator_origin"] == "四川成都"
    assert state["dialect_code"] == "zh-CN-sichuanese"
    assert state["dialect_label"] == "四川话"
    assert state["dialect_enhancement_enabled"] is True

    jobs = (
        await test_db.scalars(
            select(MemoirEditingJob).where(
                MemoirEditingJob.job_type == MemoirEditingJobType.REPROCESS_MATERIAL_OFFLINE_ASR.value
            )
        )
    ).all()
    assert len(jobs) == 1
    assert jobs[0].payload_json["utterance_id"] == created["utterance_id"]
    assert jobs[0].payload_json["dialect_label"] == "四川话"
    assert jobs[0].payload_json["transcript_polish_level"] == "medium"

    detail = await client.get(f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}")
    assert detail.status_code == 200
    assert detail.json()["offline_asr"] == duplicate.json()

    async def fake_reprocess(db, **kwargs):
        assert kwargs["transcript_polish_level"] == "medium"
        db.expunge_all()
        return {
            "utterance_id": kwargs["utterance_id"],
            "providers": ["volc", "stepfun"],
            "narrator_origin": "四川成都",
            "dialect_code": "zh-CN-sichuanese",
            "dialect_label": "四川话",
            "dialect_strategy": "sichuanese",
            "dialect_enhancement_enabled": True,
            "applied_at": "2026-07-10T12:00:00+00:00",
        }

    monkeypatch.setattr(material_offline_asr_jobs, "reprocess_offline_asr_utterance", fake_reprocess)
    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        assert await run_test_editing_job(test_db, state["job_id"]) is True
    finally:
        clear_editing_job_handlers()
    test_db.expunge_all()
    completed_job = await test_db.get(MemoirEditingJob, state["job_id"])
    assert completed_job is not None
    assert completed_job.status == "succeeded"
    assert completed_job.result_json["dialect_label"] == "四川话"


async def test_material_offline_asr_does_not_overwrite_user_edited_material(client, test_db):
    created = await create_test_voice_transcript(client, test_db)
    test_db.add(
        MemoirTranscriptVersion(
            voice_session_id=created["session"]["id"],
            source_artifact_id=created["utterance_id"],
            version_type=MemoirTranscriptVersionType.USER_EDITED.value,
            content="人工确认后的文字",
            created_by=MemoirCreatedBy.USER.value,
        )
    )
    await test_db.commit()

    detail = await client.get(f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}")
    response = await client.post(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/offline-asr",
        json={},
    )

    assert detail.status_code == 200
    assert detail.json()["offline_asr"]["can_start"] is False
    assert "人工编辑" in detail.json()["offline_asr"]["blocked_reason"]
    assert response.status_code == 409
    assert "避免覆盖" in response.json()["detail"]
