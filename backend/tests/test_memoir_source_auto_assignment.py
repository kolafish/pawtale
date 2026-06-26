from sqlalchemy import select

from app.domain.memoir.types import (
    MemoirAutomationEventType,
    MemoirBookAutoRevisionJobStatus,
)
from app.models.memoir import MemoirAutomationEvent, MemoirBookAutoRevisionJob
from tests.memoir_helpers import (
    create_test_book_plan,
    create_test_chapter_draft,
    create_test_voice_transcript,
    memoir_source_ref,
)


async def test_source_auto_assignment_assigns_high_confidence_source_and_records_progress(
    client,
    test_db,
):
    first = await create_test_voice_transcript(
        client,
        test_db,
        "小时候在外婆家的院子里摘菜，夏天还会剥青豆。",
    )
    project_id = first["session"]["project_id"]
    _, chapter = await create_test_book_plan(
        test_db,
        project_id=project_id,
        source_ref=memoir_source_ref(first, "外婆家的院子"),
        chapter_title="院子里的童年",
    )
    await create_test_chapter_draft(
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
        content="小时候在外婆家的院子里摘菜。",
    )
    uncovered = await create_test_voice_transcript(
        client,
        test_db,
        "后来想起外婆家的院子，最记得夏天一起摘菜和剥青豆。",
    )

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-source-auto-assignments/run",
        json={"ai_model": "deepseek-v4-pro"},
    )

    assert response.status_code == 200, response.json()
    data = response.json()
    assert data["evaluated_source_count"] == 1
    assert data["assigned_source_count"] == 1
    assert data["skipped_source_count"] == 0
    assert data["items"][0]["assigned"] is True
    assert data["items"][0]["target_chapter_id"] == chapter.id
    assert data["events"][0]["event_type"] == "source_assigned"
    assert data["events"][0]["utterance_ids"] == [uncovered["utterance_id"]]

    plan_chapter = data["plan"]["chapters"][0]
    source_ids = [ref["utterance_id"] for ref in plan_chapter["source_refs"]]
    assert source_ids == [first["utterance_id"], uncovered["utterance_id"]]
    assert plan_chapter["pending_source_count"] == 1

    event = await test_db.scalar(
        select(MemoirAutomationEvent).where(
            MemoirAutomationEvent.project_id == project_id,
            MemoirAutomationEvent.event_type == MemoirAutomationEventType.SOURCE_ASSIGNED.value,
        )
    )
    assert event is not None
    assert event.chapter_id == chapter.id
    assert event.utterance_ids_json == [uncovered["utterance_id"]]
    assert event.metadata_json["source"] == "auto_assignment"
    assert event.metadata_json["confidence_threshold"] == 0.53

    job = await test_db.scalar(
        select(MemoirBookAutoRevisionJob).where(MemoirBookAutoRevisionJob.chapter_id == chapter.id)
    )
    assert job is not None
    assert job.status == MemoirBookAutoRevisionJobStatus.PENDING.value
    assert job.metadata_json["pending_source_utterance_ids"] == [uncovered["utterance_id"]]
    assert job.metadata_json["ai_model"] == "deepseek-v4-pro"


async def test_source_auto_assignment_skips_new_topic_without_changing_plan(client, test_db):
    first = await create_test_voice_transcript(
        client,
        test_db,
        "小时候在外婆家的院子里摘菜，夏天还会剥青豆。",
    )
    project_id = first["session"]["project_id"]
    await create_test_book_plan(
        test_db,
        project_id=project_id,
        source_ref=memoir_source_ref(first, "外婆家的院子"),
        chapter_title="院子里的童年",
    )
    uncovered = await create_test_voice_transcript(
        client,
        test_db,
        "参加工作以后，我在供销社负责盘点货物，第一次拿到奖金特别高兴。",
    )

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-source-auto-assignments/run",
        json={},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["evaluated_source_count"] == 1
    assert data["assigned_source_count"] == 0
    assert data["skipped_source_count"] == 1
    assert data["items"][0]["utterance_id"] == uncovered["utterance_id"]
    assert data["items"][0]["action"] == "suggest_new_chapter"
    assert data["items"][0]["assigned"] is False
    assert data["items"][0]["skip_reason"] == "not_existing_chapter_assignment"
    assert data["events"] == []
    assert [ref["utterance_id"] for ref in data["plan"]["chapters"][0]["source_refs"]] == [
        first["utterance_id"],
    ]
