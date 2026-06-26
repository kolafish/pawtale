from app.models.memoir import MemoirBookPlanChapter
from tests.memoir_helpers import (
    create_test_book_plan,
    create_test_voice_transcript,
    memoir_source_ref,
)


async def _add_chapter(test_db, plan, *, project_id: str, title: str, source_ref: dict, sort_order: int):
    chapter = MemoirBookPlanChapter(
        plan_id=plan.id,
        project_id=project_id,
        title=title,
        sort_order=sort_order,
        source_refs_json=[source_ref],
        metadata_json={},
    )
    test_db.add(chapter)
    await test_db.commit()
    return chapter


async def test_source_router_assigns_related_uncovered_source_to_existing_chapter(client, test_db):
    first = await create_test_voice_transcript(
        client,
        test_db,
        "小时候在外婆家的院子里摘菜，夏天还会剥青豆。",
    )
    project_id = first["session"]["project_id"]
    plan, chapter = await create_test_book_plan(
        test_db,
        project_id=project_id,
        source_ref=memoir_source_ref(first, "外婆家的院子"),
        title="爷爷主题",
        chapter_title="院子里的童年",
    )
    second = await create_test_voice_transcript(
        client,
        test_db,
        "我参加工作以后在供销社上班，师傅对我很照顾。",
    )
    await _add_chapter(
        test_db,
        plan,
        project_id=project_id,
        title="初入职场",
        source_ref=memoir_source_ref(second, "供销社上班"),
        sort_order=1,
    )
    uncovered = await create_test_voice_transcript(
        client,
        test_db,
        "后来想起外婆家的院子，最记得夏天一起摘菜和剥青豆。",
    )

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-source-routing/dry-run",
        json={"utterance_id": uncovered["utterance_id"]},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["action"] == "assign_to_chapter"
    assert data["target_chapter_id"] == chapter.id
    assert data["target_chapter_title"] == "院子里的童年"
    assert data["confidence"] >= 0.52
    assert data["evidence"][0]["utterance_id"] == uncovered["utterance_id"]


async def test_source_router_suggests_new_chapter_for_clear_new_topic(client, test_db):
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
        title="爷爷主题",
        chapter_title="院子里的童年",
    )
    uncovered = await create_test_voice_transcript(
        client,
        test_db,
        "参加工作以后，我在供销社负责盘点货物，第一次拿到奖金特别高兴。",
    )

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-source-routing/dry-run",
        json={"utterance_id": uncovered["utterance_id"]},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["action"] == "suggest_new_chapter"
    assert data["suggested_title"] == "工作经历"
    assert data["target_chapter_id"] is None


async def test_source_router_holds_short_or_ambiguous_source(client, test_db):
    first = await create_test_voice_transcript(client, test_db, "小时候在老家院子里玩。")
    project_id = first["session"]["project_id"]
    await create_test_book_plan(
        test_db,
        project_id=project_id,
        source_ref=memoir_source_ref(first, "老家院子"),
        chapter_title="老家院子",
    )
    short = await create_test_voice_transcript(client, test_db, "嗯。")

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-source-routing/dry-run",
        json={"utterance_id": short["utterance_id"]},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["action"] == "hold_for_review"
    assert data["reason"] == "讲述内容太短，不能可靠判断章节归属"


async def test_source_router_rejects_already_included_source(client, test_db):
    first = await create_test_voice_transcript(client, test_db, "小时候在外婆家的院子里摘菜。")
    project_id = first["session"]["project_id"]
    await create_test_book_plan(
        test_db,
        project_id=project_id,
        source_ref=memoir_source_ref(first, "外婆家的院子"),
    )

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-source-routing/dry-run",
        json={"utterance_id": first["utterance_id"]},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "讲述记录已收录到章节"
