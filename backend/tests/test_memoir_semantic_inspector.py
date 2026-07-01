from app.domain.memoir.types import MemoirAutomationEventType, MemoirBookPlanStatus
from app.services.memoir import automation_events
from app.services.memoir.chapter_cards import CHAPTER_CARD_ENGINE, build_chapter_card_context
from app.services.memoir.semantic_state import (
    upsert_book_quality_audit_snapshot,
    upsert_chapter_card,
    upsert_source_card,
)
from app.services.memoir.source_cards import SOURCE_CARD_ENGINE, source_text_hash
from tests.memoir_helpers import (
    create_test_book_plan,
    create_test_voice_transcript,
    memoir_source_ref,
)


async def test_mobile_memoir_semantic_state_reports_cache_status_and_followups(client, test_db):
    created = await create_test_voice_transcript(
        client,
        test_db,
        "我小时候常在外婆家的院子里摘菜，夏天还会一起剥青豆。",
    )
    project_id = created["session"]["project_id"]
    plan, chapter = await create_test_book_plan(
        test_db,
        project_id=project_id,
        source_ref=memoir_source_ref(created, "外婆家的院子"),
        chapter_title="院子里的童年",
        status=MemoirBookPlanStatus.CONFIRMED,
    )
    await upsert_source_card(
        test_db,
        project_id=project_id,
        utterance_id=created["utterance_id"],
        transcript_version_id=created["transcript_version_id"],
        source_text_hash=source_text_hash("我小时候常在外婆家的院子里摘菜，夏天还会一起剥青豆。"),
        engine=SOURCE_CARD_ENGINE,
        card_json={
            "summary": "回忆外婆家院子里摘菜和剥青豆。",
            "people": ["外婆"],
            "places": ["外婆家院子"],
            "event_tags": ["摘菜", "剥青豆"],
            "key_facts": [{"text": "夏天剥青豆", "evidence_quote": "夏天还会一起剥青豆"}],
            "open_questions": ["剥青豆是怎么开始的？"],
        },
    )
    context = await build_chapter_card_context(test_db, project_id=project_id, chapter_id=chapter.id)
    chapter_card = await upsert_chapter_card(
        test_db,
        project_id=project_id,
        plan_id=plan.id,
        chapter_id=chapter.id,
        draft_version_id=None,
        input_signature=context.input_signature,
        engine=CHAPTER_CARD_ENGINE,
        card_json={
            "summary": "本章围绕外婆家院子的童年生活。",
            "gaps": [{"severity": "medium", "message": "剥青豆的前后经过还不完整"}],
            "next_questions": ["外婆在院子里最常做哪件事？"],
        },
    )
    snapshot = await upsert_book_quality_audit_snapshot(
        test_db,
        project_id=project_id,
        engine="llm_semantic_v1",
        input_signature="audit-signature-1",
        overall_status="needs_updates",
        summary_text="语义审计认为院子章节可继续补问。",
        chapter_audits_json=[
            {
                "chapter_id": chapter.id,
                "summary": "可以继续补充剥青豆起因。",
                "gaps": [{"severity": "high", "message": "剥青豆起因缺失"}],
                "next_questions": ["那天为什么会一起剥青豆？"],
            }
        ],
        chapter_card_ids_json=[chapter_card.id],
    )
    event = await automation_events.record_automation_event(
        test_db,
        project_id=project_id,
        event_type=MemoirAutomationEventType.AUDIT_COMPLETED,
        summary_text="语义审计认为院子章节可继续补问。",
        metadata_json={"snapshot_id": snapshot.id, "snapshot_engine": "llm_semantic_v1"},
    )
    await test_db.commit()

    response = await client.get(f"/api/mobile/memoir/projects/{project_id}/semantic-state")

    assert response.status_code == 200
    data = response.json()
    assert data["project_id"] == project_id
    assert data["source_cards"]["ready"] == 1
    assert data["source_cards"]["missing_current_ready"] == 0
    assert data["chapter_cards"]["ready"] == 1
    assert data["chapter_cards"]["missing_current_ready"] == 0
    assert data["audit_snapshots"]["ready"] == 1
    assert data["audit_snapshots"]["latest"][0]["snapshot_id"] == snapshot.id
    assert data["latest_audit_event_ids"] == [event.id]
    assert data["failure_count"] == 0
    questions = [item["question"] for item in data["follow_up_suggestions"]]
    assert questions[:2] == [
        "外婆在院子里最常做哪件事？",
        "那天为什么会一起剥青豆？",
    ]
    assert data["follow_up_suggestions"][0]["source"] == "chapter_card"
    assert data["follow_up_suggestions"][0]["chapter_title"] == "院子里的童年"
    assert data["follow_up_suggestions"][1]["source"] == "audit_snapshot"


async def test_mobile_memoir_semantic_state_requires_project_access(anonymous_client):
    response = await anonymous_client.get("/api/mobile/memoir/projects/project-1/semantic-state")

    assert response.status_code == 401
