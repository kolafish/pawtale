from app.domain.memoir.types import (
    MemoirAutomationEventStatus,
    MemoirAutomationEventType,
    MemoirBookPlanStatus,
    MemoirSemanticCacheStatus,
)
from app.models.memoir import (
    MemoirAutomationEvent,
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirBookQualityAuditSnapshot,
    MemoirChapterCard,
)
from app.services.memoir.automation_event_audit_snapshots import audit_snapshot_metadata_by_event
from tests.memoir_helpers import create_test_voice_transcript


async def test_audit_snapshot_metadata_expands_current_project_snapshot(client, test_db):
    created = await create_test_voice_transcript(client, test_db, text="我小时候常在外婆家院子里摘菜。")
    project_id = created["session"]["project_id"]
    plan = MemoirBookPlan(
        project_id=project_id,
        title="成书规划",
        status=MemoirBookPlanStatus.CONFIRMED.value,
    )
    test_db.add(plan)
    await test_db.flush()
    chapter = MemoirBookPlanChapter(
        project_id=project_id,
        plan_id=plan.id,
        title="院子里的童年",
        sort_order=1,
    )
    test_db.add(chapter)
    await test_db.flush()
    card = MemoirChapterCard(
        project_id=project_id,
        plan_id=plan.id,
        chapter_id=chapter.id,
        input_signature="chapter-card-signature",
        engine="rules_v1",
        status=MemoirSemanticCacheStatus.READY.value,
        card_json={
            "summary": "本章围绕院子里的童年生活。",
            "gaps": [{"type": "event_sequence", "severity": "medium", "message": "剥青豆经过还不连贯"}],
            "next_questions": ["外婆在院子里最常做哪件事？"],
            "source_card_ids": ["source-card-1"],
            "evidence_refs": [{"utterance_id": created["utterance_id"], "quote": "外婆家院子里摘菜"}],
        },
    )
    test_db.add(card)
    await test_db.flush()
    snapshot = MemoirBookQualityAuditSnapshot(
        project_id=project_id,
        engine="llm_semantic_v1",
        input_signature="audit-signature",
        overall_status="needs_updates",
        summary_text="语义审计认为可以继续补充。",
        display_json={"title": "继续补问"},
        primary_action_json={"type": "continue_interview", "chapter_id": chapter.id},
        chapter_audits_json=[
            {
                "chapter_id": chapter.id,
                "summary": "可以补充剥青豆的起因。",
                "next_questions": ["那天为什么会一起剥青豆？"],
            }
        ],
        chapter_card_ids_json=[card.id],
    )
    test_db.add(snapshot)
    await test_db.flush()
    event = MemoirAutomationEvent(
        project_id=project_id,
        event_type=MemoirAutomationEventType.AUDIT_COMPLETED.value,
        status=MemoirAutomationEventStatus.NEEDS_REVIEW.value,
        summary_text="语义审计认为可以继续补充。",
        metadata_json={"source": "book_quality_audit", "snapshot_id": snapshot.id},
    )
    test_db.add(event)
    await test_db.flush()

    metadata_by_event = await audit_snapshot_metadata_by_event(test_db, [event])

    audit_snapshot = metadata_by_event[event.id]
    assert audit_snapshot["id"] == snapshot.id
    assert audit_snapshot["chapter_cards"][0]["chapter_title"] == "院子里的童年"
    assert audit_snapshot["chapter_cards"][0]["next_questions"] == ["外婆在院子里最常做哪件事？"]
    assert audit_snapshot["chapter_audits"][0]["chapter_title"] == "院子里的童年"
    assert audit_snapshot["next_questions"] == ["外婆在院子里最常做哪件事？"]


async def test_audit_snapshot_metadata_ignores_cross_project_snapshot(client, test_db):
    created = await create_test_voice_transcript(client, test_db, text="我小时候常在外婆家院子里摘菜。")
    other_project_response = await client.post("/api/mobile/memoir/projects", json={"title": "另一本回忆录"})
    assert other_project_response.status_code == 201
    other_snapshot = MemoirBookQualityAuditSnapshot(
        project_id=other_project_response.json()["id"],
        engine="llm_semantic_v1",
        input_signature="other-audit-signature",
        overall_status="needs_updates",
        summary_text="另一个项目的审计摘要。",
    )
    test_db.add(other_snapshot)
    await test_db.flush()
    event = MemoirAutomationEvent(
        project_id=created["session"]["project_id"],
        event_type=MemoirAutomationEventType.AUDIT_COMPLETED.value,
        status=MemoirAutomationEventStatus.NEEDS_REVIEW.value,
        summary_text="当前项目事件。",
        metadata_json={"source": "book_quality_audit", "snapshot_id": other_snapshot.id},
    )
    test_db.add(event)
    await test_db.flush()

    assert await audit_snapshot_metadata_by_event(test_db, [event]) == {}
