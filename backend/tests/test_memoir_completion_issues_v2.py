import json

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.database import Base, register_models
from app.domain.memoir.types import (
    MemoirCreatedBy,
    MemoirCompletionIssueOutcome,
    MemoirCompletionIssueStatus,
    MemoirEditingJobStatus,
    MemoirEditingJobType,
    MemoirTranscriptVersionType,
)
from app.migrations.memoir_completion import (
    assert_memoir_completion_schema,
    migrate_memoir_completion_schema,
)
from app.migrations.memoir_voice import _ensure_completion_issue_columns
from app.models.memoir import (
    MemoirCompletionIssue,
    MemoirEditingJob,
    MemoirSectionMaterial,
    MemoirTranscriptVersion,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.services.memoir import (
    completion_issues,
    sections,
    voice_transcript_correction,
)
from app.services.memoir.asr import StreamingASREvent
from app.services.memoir.voice_final_persistence import (
    VoiceFinalPersistenceService,
)
from app.services.memoir.voice_transcripts import CleanedTranscriptText
from tests.memoir_helpers import (
    create_test_book_plan_with_chapters,
    create_test_voice_transcript,
    ensure_mobile_memoir_project,
    memoir_source_ref,
    post_mobile_memoir_voice_session,
)


class FakeDigestAI:
    provider_name = "test"
    model_name = "test-section-digest"

    def __init__(self, payload: dict):
        self.payload = payload
        self.calls = 0

    async def _call_llm(
        self,
        system_prompt,
        user_prompt,
        temperature=0.7,
        max_tokens=4000,
        timeout=60.0,
        return_raw=False,
    ):
        self.calls += 1
        return json.dumps(self.payload, ensure_ascii=False)


async def _section(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    first = await create_test_voice_transcript(
        client,
        test_db,
        "朋友发来一张山桃花照片，后来邀请我一起爬山。",
        project_id=project_id,
    )
    second = await create_test_voice_transcript(
        client,
        test_db,
        "那天六个人一起爬了西山，正赶上花开。",
        project_id=project_id,
    )
    await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["山桃花开的心动"],
        source_refs_by_chapter=[
            [memoir_source_ref(first), memoir_source_ref(second)]
        ],
    )
    response = await client.post(
        f"/api/memoir/projects/{project_id}/sections/bootstrap"
    )
    assert response.status_code == 200
    return project_id, response.json()["sections"][0], (first, second)


def _digest_payload(material_id: str) -> dict:
    return {
        "summary": "朋友发来山桃花照片，随后一起参加爬山。",
        "focus": "一段疏远的关系如何重新搭上线。",
        "covered_points": ["重新联系的契机", "第一次共同活动"],
        "candidate_issues": [
            {
                "issue_type": "meaning",
                "question": "这次重新联系对你来说，最特别的地方是什么？",
                "reason": "个人意义会让重点更鲜明。",
                "priority": "high",
                "material_ids": [material_id],
            },
            {
                "issue_type": "context",
                "question": "当时你们为什么已经很少联系了？",
                "reason": "这能帮助读者理解重新联系的背景。",
                "priority": "low",
                "material_ids": [material_id],
            },
        ],
    }


async def test_completion_issue_migration_has_only_layered_targets():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(text("DROP TABLE memoir_completion_issues"))

        first = await migrate_memoir_completion_schema(engine)
        second = await migrate_memoir_completion_schema(engine)
        assert first["changed_tables"] == ["memoir_completion_issues"]
        assert second["changed_tables"] == []
        await assert_memoir_completion_schema(engine)

        async with engine.begin() as conn:
            columns = {
                row[1]
                for row in (
                    await conn.execute(
                        text("PRAGMA table_info(memoir_completion_issues)")
                    )
                ).fetchall()
            }
            table_sql = (
                await conn.execute(
                    text(
                        "SELECT sql FROM sqlite_master "
                        "WHERE type = 'table' "
                        "AND name = 'memoir_completion_issues'"
                    )
                )
            ).scalar_one()
        assert {"section_id", "chapter_id", "related_material_ids_json"} <= columns
        assert "story_id" not in columns
        assert "claim_id" not in columns
        assert "evidence_id" not in columns
        assert "ck_memoir_completion_issues_lifecycle" in table_sql
    finally:
        await engine.dispose()


async def test_voice_migration_adds_layered_completion_targets_idempotently():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("CREATE TABLE memoir_completion_issues (id VARCHAR(36) PRIMARY KEY)")
            )
            await conn.execute(
                text(
                    "CREATE TABLE memoir_voice_sessions ("
                    "id VARCHAR(36) PRIMARY KEY, "
                    "current_completion_item_id VARCHAR(36))"
                )
            )
            await conn.execute(
                text(
                    "CREATE TABLE memoir_voice_utterances ("
                    "id VARCHAR(36) PRIMARY KEY, "
                    "completion_item_id VARCHAR(36))"
                )
            )
            first = await _ensure_completion_issue_columns(conn)
            second = await _ensure_completion_issue_columns(conn)
            session_columns = {
                row[1]
                for row in (
                    await conn.execute(
                        text("PRAGMA table_info(memoir_voice_sessions)")
                    )
                ).fetchall()
            }
            utterance_columns = {
                row[1]
                for row in (
                    await conn.execute(
                        text("PRAGMA table_info(memoir_voice_utterances)")
                    )
                ).fetchall()
            }
            indexes = {
                row[1]
                for row in (
                    await conn.execute(
                        text(
                            "SELECT type, name FROM sqlite_master "
                            "WHERE type = 'index'"
                        )
                    )
                ).fetchall()
            }

        assert first == [
            "memoir_voice_sessions.current_completion_issue_id",
            "memoir_voice_utterances.completion_issue_id",
        ]
        assert second == []
        assert "current_completion_issue_id" in session_columns
        assert "completion_issue_id" in utterance_columns
        assert {
            "ix_memoir_voice_sessions_current_completion_issue_id",
            "ix_memoir_voice_utterances_completion_issue_id",
        } <= indexes
    finally:
        await engine.dispose()


async def test_section_digest_creates_reusable_ranked_issues(client, test_db):
    project_id, section, materials = await _section(client, test_db)
    fake = FakeDigestAI(_digest_payload(materials[0]["utterance_id"]))

    first = await completion_issues.refresh_section_issues(
        test_db,
        project_id=project_id,
        section_id=section["id"],
        ai_client=fake,
    )
    await test_db.commit()
    second = await completion_issues.refresh_section_issues(
        test_db,
        project_id=project_id,
        section_id=section["id"],
        ai_client=fake,
    )
    await test_db.commit()

    assert len(first) == 2
    assert [item.id for item in second] == [item.id for item in first]
    assert fake.calls == 1
    assert first[0].priority == "high"
    assert first[0].section_id == section["id"]
    assert first[0].chapter_id == section["chapter_id"]
    assert not hasattr(first[0], "story_id")

    response = await client.get(
        f"/api/memoir/projects/{project_id}/completion-issues",
        params={"chapter_id": section["chapter_id"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["issues"]) == 2
    assert body["current_issue"]["priority"] == "high"
    assert body["current_issue"]["related_material_ids"] == [
        materials[0]["utterance_id"]
    ]


async def test_closed_issue_is_not_reopened_or_rephrased(client, test_db):
    project_id, section, materials = await _section(client, test_db)
    fake = FakeDigestAI(_digest_payload(materials[0]["utterance_id"]))
    issues = await completion_issues.refresh_section_issues(
        test_db,
        project_id=project_id,
        section_id=section["id"],
        ai_client=fake,
    )
    declined = issues[0]
    await completion_issues.close_issue(
        test_db,
        project_id=project_id,
        issue_id=declined.id,
        outcome=MemoirCompletionIssueOutcome.DECLINED,
        note="不愿讨论",
        answered_material_id=None,
    )
    await test_db.commit()

    await completion_issues.refresh_section_issues(
        test_db,
        project_id=project_id,
        section_id=section["id"],
        ai_client=fake,
    )
    await test_db.commit()
    persisted = await test_db.get(MemoirCompletionIssue, declined.id)

    assert persisted is not None
    assert persisted.status == MemoirCompletionIssueStatus.CLOSED.value
    assert persisted.outcome == MemoirCompletionIssueOutcome.DECLINED.value
    assert persisted.outcome_note == "不愿讨论"
    current = await completion_issues.collection_response(
        test_db,
        project_id=project_id,
        chapter_id=section["chapter_id"],
    )
    assert current.current_issue is not None
    assert current.current_issue.id != declined.id


async def test_stale_active_issues_are_closed_as_superseded(client, test_db):
    project_id, section, materials = await _section(client, test_db)
    fake = FakeDigestAI(_digest_payload(materials[0]["utterance_id"]))
    issues = await completion_issues.refresh_section_issues(
        test_db,
        project_id=project_id,
        section_id=section["id"],
        ai_client=fake,
    )
    await completion_issues.start_issue(
        test_db,
        project_id=project_id,
        issue_id=issues[0].id,
    )
    await test_db.commit()

    update = await client.patch(
        f"/api/memoir/projects/{project_id}/sections/{section['id']}",
        json={
            "title": "重新联系",
            "expected_version": section["version"],
        },
    )
    assert update.status_code == 200
    empty = FakeDigestAI(
        {
            "summary": "经过已经讲清楚。",
            "focus": "重新联系。",
            "covered_points": ["契机", "经过", "感受"],
            "candidate_issues": [],
        }
    )
    refreshed = await completion_issues.refresh_section_issues(
        test_db,
        project_id=project_id,
        section_id=section["id"],
        ai_client=empty,
    )
    await test_db.commit()

    assert refreshed == []
    for issue in issues:
        persisted = await test_db.get(MemoirCompletionIssue, issue.id)
        assert persisted is not None
        assert persisted.status == MemoirCompletionIssueStatus.CLOSED.value
        assert persisted.outcome == MemoirCompletionIssueOutcome.SUPERSEDED.value


async def test_answered_issue_requires_material_from_same_project(client, test_db):
    project_id, section, materials = await _section(client, test_db)
    fake = FakeDigestAI(_digest_payload(materials[0]["utterance_id"]))
    issue = (
        await completion_issues.refresh_section_issues(
            test_db,
            project_id=project_id,
            section_id=section["id"],
            ai_client=fake,
        )
    )[0]

    closed = await completion_issues.close_issue(
        test_db,
        project_id=project_id,
        issue_id=issue.id,
        outcome=MemoirCompletionIssueOutcome.ANSWERED,
        note="已经补讲",
        answered_material_id=materials[1]["utterance_id"],
    )
    await test_db.commit()

    assert closed.status == MemoirCompletionIssueStatus.CLOSED.value
    assert closed.answered_material_id == materials[1]["utterance_id"]
    persisted = await test_db.scalar(
        select(MemoirCompletionIssue).where(
            MemoirCompletionIssue.id == issue.id
        )
    )
    assert persisted is not None
    assert persisted.outcome == MemoirCompletionIssueOutcome.ANSWERED.value


async def test_completion_voice_session_appends_every_answer_segment_to_section(
    client,
    test_db,
):
    project_id, section, materials = await _section(client, test_db)
    issue = (
        await completion_issues.refresh_section_issues(
            test_db,
            project_id=project_id,
            section_id=section["id"],
            ai_client=FakeDigestAI(
                _digest_payload(materials[0]["utterance_id"])
            ),
        )
    )[0]
    await test_db.commit()

    created = await post_mobile_memoir_voice_session(
        client,
        project_id=project_id,
        json={
            "guidance_mode": "guided_interview",
            "session_goal": "book_completion",
            "current_completion_issue_id": issue.id,
        },
    )
    assert created.status_code == 201
    session_id = created.json()["session"]["id"]
    assert created.json()["session"]["current_completion_issue_id"] == issue.id
    persistence = VoiceFinalPersistenceService(
        db=test_db,
        voice_session_id=session_id,
        user_id=(await client.get("/api/auth/me")).json()["id"],
    )

    first = await persistence.persist_final(
        StreamingASREvent(
            type="final",
            text="那时我们已经两年没有见面了。",
            start_ms=0,
            end_ms=1600,
            raw={"segment": 1},
        )
    )
    second = await persistence.persist_final(
        StreamingASREvent(
            type="final",
            text="这次爬山让我们重新熟悉起来。",
            start_ms=1600,
            end_ms=3200,
            raw={"segment": 2},
        )
    )

    assignments = list(
        (
            await test_db.scalars(
                select(MemoirSectionMaterial)
                .where(
                    MemoirSectionMaterial.utterance_id.in_(
                        [first.utterance_id, second.utterance_id]
                    )
                )
                .order_by(MemoirSectionMaterial.sort_order)
            )
        ).all()
    )
    await test_db.refresh(issue)
    session = await test_db.get(MemoirVoiceSession, session_id)

    assert [item.utterance_id for item in assignments] == [
        first.utterance_id,
        second.utterance_id,
    ]
    assert {item.section_id for item in assignments} == {section["id"]}
    assert all(
        item.assignment_reason == f"completion_issue:{issue.id}"
        for item in assignments
    )
    assert issue.status == MemoirCompletionIssueStatus.CLOSED.value
    assert issue.outcome == MemoirCompletionIssueOutcome.ANSWERED.value
    assert issue.answered_material_id == first.utterance_id
    assert session is not None
    assert session.current_completion_issue_id == issue.id

    stopped = await client.post(
        f"/api/mobile/memoir/voice-sessions/{session_id}/stop",
        json={},
    )
    assert stopped.status_code == 200
    assert stopped.json()["session"]["current_completion_issue_id"] is None


async def test_stopping_completion_session_without_answer_reopens_issue(
    client,
    test_db,
):
    project_id, section, materials = await _section(client, test_db)
    issue = (
        await completion_issues.refresh_section_issues(
            test_db,
            project_id=project_id,
            section_id=section["id"],
            ai_client=FakeDigestAI(
                _digest_payload(materials[0]["utterance_id"])
            ),
        )
    )[0]
    await test_db.commit()
    created = await post_mobile_memoir_voice_session(
        client,
        project_id=project_id,
        json={
            "guidance_mode": "guided_interview",
            "session_goal": "book_completion",
            "current_completion_issue_id": issue.id,
        },
    )
    assert created.status_code == 201
    session_id = created.json()["session"]["id"]
    test_db.add(
        MemoirVoiceUtterance(
            voice_session_id=session_id,
            completion_issue_id=issue.id,
            transcript_raw="   ",
        )
    )
    await test_db.commit()

    stopped = await client.post(
        f"/api/mobile/memoir/voice-sessions/{session_id}/stop",
        json={},
    )
    await test_db.refresh(issue)

    assert stopped.status_code == 200
    assert issue.status == MemoirCompletionIssueStatus.OPEN.value
    assert issue.started_at is None
    assert stopped.json()["session"]["current_completion_issue_id"] is None


async def test_completion_answer_correction_refreshes_layered_material_version(
    client,
    test_db,
    monkeypatch,
):
    project_id, section, materials = await _section(client, test_db)
    issue = (
        await completion_issues.refresh_section_issues(
            test_db,
            project_id=project_id,
            section_id=section["id"],
            ai_client=FakeDigestAI(
                _digest_payload(materials[0]["utterance_id"])
            ),
        )
    )[0]
    await test_db.commit()
    created = await post_mobile_memoir_voice_session(
        client,
        project_id=project_id,
        json={
            "guidance_mode": "guided_interview",
            "session_goal": "book_completion",
            "current_completion_issue_id": issue.id,
        },
    )
    assert created.status_code == 201
    session_id = created.json()["session"]["id"]
    persistence = VoiceFinalPersistenceService(
        db=test_db,
        voice_session_id=session_id,
        user_id=(await client.get("/api/auth/me")).json()["id"],
    )
    persisted = await persistence.persist_final(
        StreamingASREvent(
            type="final",
            text="嗯，那次爬山让我们又熟悉了。",
            raw={"segment": 1},
        )
    )
    correction_job = await test_db.scalar(
        select(MemoirEditingJob).where(
            MemoirEditingJob.project_id == project_id,
            MemoirEditingJob.job_type
            == MemoirEditingJobType.CORRECT_TRANSCRIPT.value,
            MemoirEditingJob.dedupe_key
            == f"correct_transcript:{persisted.utterance_id}",
        )
    )
    assert correction_job is not None

    async def fake_clean(*_args, **_kwargs):
        return CleanedTranscriptText(
            content="那次爬山让我们重新熟悉起来。",
            provider="test",
            created_by=MemoirCreatedBy.AI,
        )

    monkeypatch.setattr(
        voice_transcript_correction,
        "generate_cleaned_transcript_text",
        fake_clean,
    )
    result = await voice_transcript_correction.run_transcript_correction_job(
        test_db,
        correction_job,
    )
    material = await test_db.scalar(
        select(MemoirSectionMaterial).where(
            MemoirSectionMaterial.utterance_id == persisted.utterance_id
        )
    )
    corrected = await test_db.scalar(
        select(MemoirTranscriptVersion).where(
            MemoirTranscriptVersion.source_artifact_id
            == persisted.utterance_id,
            MemoirTranscriptVersion.version_type
            == MemoirTranscriptVersionType.DIALECT_CORRECTED.value,
        )
    )

    assert result.status == MemoirEditingJobStatus.SUCCEEDED
    assert material is not None
    assert material.section_id == section["id"]
    assert corrected is not None
    assert material.transcript_version_id == corrected.id
    assert result.result_json["organization_job_id"] is None
    with pytest.raises(
        HTTPException,
        match="正文文本版本不属于当前讲述素材",
    ):
        await sections.refresh_material_transcript_version(
            test_db,
            project_id=project_id,
            utterance_id=persisted.utterance_id,
            transcript_version_id=materials[0]["transcript_version_id"],
        )
