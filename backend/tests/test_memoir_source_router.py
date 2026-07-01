import json

from app.services.memoir.chapter_cards import CHAPTER_CARD_ENGINE, build_chapter_card_context
from app.services.memoir.semantic_state import upsert_chapter_card
from app.models.memoir import MemoirBookPlanChapter
from tests.memoir_helpers import (
    create_test_book_plan,
    create_test_voice_transcript,
    memoir_source_ref,
)


class FakeSourceRouterAI:
    provider_name = "fake-ai"
    model_name = "fake-source-router"

    def __init__(self, *payloads: dict[str, object] | str):
        self.payloads = list(payloads)
        self.calls: list[dict[str, object]] = []

    async def _call_llm(self, system_prompt: str, user_prompt: str, **kwargs):
        self.calls.append({"system_prompt": system_prompt, "user_prompt": user_prompt, **kwargs})
        if not self.payloads:
            raise AssertionError("fake source router payload missing")
        payload = self.payloads.pop(0)
        if isinstance(payload, str):
            return payload
        return json.dumps(payload, ensure_ascii=False)


class FakeSourceCardAI:
    provider_name = "fake-ai"
    model_name = "fake-source-card"

    async def _call_llm(self, system_prompt: str, user_prompt: str, **kwargs):
        return json.dumps(
            {
                "short_title": "外婆家院子",
                "summary": "回忆外婆家院子里夏天摘菜和剥青豆。",
                "people": ["外婆", "我"],
                "places": ["外婆家院子"],
                "time_hints": ["夏天"],
                "event_tags": ["摘菜", "剥青豆"],
                "emotion_tags": ["怀念"],
                "key_facts": [
                    {"text": "夏天一起摘菜和剥青豆", "evidence_quote": "夏天一起摘菜和剥青豆"}
                ],
                "open_questions": ["那次剥青豆是怎么开始的？"],
            },
            ensure_ascii=False,
        )


def _patch_source_card_ai(monkeypatch) -> FakeSourceCardAI:
    fake = FakeSourceCardAI()
    monkeypatch.setattr("app.services.memoir.source_cards.get_ai_service", lambda ai_model=None: fake)
    return fake


def _patch_source_router_ai(monkeypatch, *payloads: dict[str, object] | str) -> FakeSourceRouterAI:
    fake = FakeSourceRouterAI(*payloads)
    _patch_source_card_ai(monkeypatch)
    monkeypatch.setattr("app.services.memoir.source_router.get_ai_service", lambda ai_model=None: fake)
    return fake


def _assign_route(chapter: MemoirBookPlanChapter, *, confidence: float = 0.91) -> dict[str, object]:
    return {
        "material_type": "memoir_content",
        "action": "assign_to_chapter",
        "target_chapter_id": chapter.id,
        "suggested_title": None,
        "confidence": confidence,
        "reason": "讲述延续同一个童年院子主题",
        "evidence": [{"quote": "夏天一起摘菜和剥青豆", "reason": "与章节已有素材一致"}],
    }


def _new_chapter_route(title: str) -> dict[str, object]:
    return {
        "material_type": "memoir_content",
        "action": "suggest_new_chapter",
        "target_chapter_id": None,
        "suggested_title": title,
        "confidence": 0.88,
        "reason": "讲述集中在新的工作经历",
        "evidence": [{"quote": "在供销社负责盘点货物", "reason": "是明确的新人生阶段"}],
    }


def _ignore_route(*, material_type: str = "ai_interaction", reason: str = "这是和 AI 的对话") -> dict[str, object]:
    return {
        "material_type": material_type,
        "action": "ignore",
        "target_chapter_id": None,
        "suggested_title": None,
        "confidence": 0.92,
        "reason": reason,
        "evidence": [{"quote": "你能不能别问了", "reason": "不是回忆录素材"}],
    }


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


async def test_source_router_assigns_related_uncovered_source_to_existing_chapter(client, test_db, monkeypatch):
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
    fake_ai = _patch_source_router_ai(monkeypatch, _assign_route(chapter))

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
    assert fake_ai.calls
    assert "已有章节" in fake_ai.calls[0]["user_prompt"]


async def test_source_router_prompt_uses_source_and_chapter_cards(client, test_db, monkeypatch):
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
    context = await build_chapter_card_context(test_db, project_id=project_id, chapter_id=chapter.id)
    await upsert_chapter_card(
        test_db,
        project_id=project_id,
        plan_id=chapter.plan_id,
        chapter_id=chapter.id,
        draft_version_id=context.draft_version.id if context.draft_version else None,
        input_signature=context.input_signature,
        engine=CHAPTER_CARD_ENGINE,
        card_json={
            "summary": "本章已经覆盖外婆家院子里的童年生活。",
            "covered": {"places": ["外婆家院子"], "events": ["摘菜"]},
            "gaps": [{"severity": "medium", "message": "剥青豆的前后经过还不完整"}],
            "next_questions": ["那次剥青豆是怎么开始的？"],
        },
    )
    await test_db.commit()
    uncovered = await create_test_voice_transcript(
        client,
        test_db,
        "后来想起外婆家的院子，最记得夏天一起摘菜和剥青豆。",
    )
    fake_ai = _patch_source_router_ai(monkeypatch, _assign_route(chapter))

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-source-routing/dry-run",
        json={"utterance_id": uncovered["utterance_id"]},
    )

    assert response.status_code == 200
    prompt = fake_ai.calls[0]["user_prompt"]
    assert "当前讲述素材卡片" in prompt
    assert "回忆外婆家院子里夏天摘菜和剥青豆" in prompt
    assert "chapter_card_summary: 本章已经覆盖外婆家院子里的童年生活。" in prompt
    assert "chapter_card_gaps: ['medium: 剥青豆的前后经过还不完整']" in prompt


async def test_source_router_suggests_new_chapter_for_clear_new_topic(client, test_db, monkeypatch):
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
    _patch_source_router_ai(monkeypatch, _new_chapter_route("供销社工作初记"))

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-source-routing/dry-run",
        json={"utterance_id": uncovered["utterance_id"]},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["action"] == "suggest_new_chapter"
    assert data["material_type"] == "memoir_content"
    assert data["suggested_title"] == "供销社工作初记"
    assert data["target_chapter_id"] is None


async def test_source_router_ignores_non_memoir_source(client, test_db, monkeypatch):
    first = await create_test_voice_transcript(client, test_db, "小时候在老家院子里玩。")
    project_id = first["session"]["project_id"]
    await create_test_book_plan(
        test_db,
        project_id=project_id,
        source_ref=memoir_source_ref(first, "老家院子"),
        chapter_title="老家院子",
    )
    short = await create_test_voice_transcript(client, test_db, "嗯。")
    _patch_source_router_ai(
        monkeypatch,
        _ignore_route(material_type="too_short", reason="内容太短，无法判断为回忆录素材"),
    )

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-source-routing/dry-run",
        json={"utterance_id": short["utterance_id"]},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["material_type"] == "too_short"
    assert data["action"] == "ignore"
    assert data["target_chapter_id"] is None
    assert data["suggested_title"] is None
    assert data["reason"] == "内容太短，无法判断为回忆录素材"


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


async def test_source_router_rejects_invalid_llm_payload_without_fallback(client, test_db, monkeypatch):
    first = await create_test_voice_transcript(client, test_db, "小时候在外婆家的院子里摘菜。")
    project_id = first["session"]["project_id"]
    await create_test_book_plan(
        test_db,
        project_id=project_id,
        source_ref=memoir_source_ref(first, "外婆家的院子"),
    )
    uncovered = await create_test_voice_transcript(client, test_db, "你能没完没了的吗？")
    _patch_source_router_ai(monkeypatch, "不是 JSON")

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-source-routing/dry-run",
        json={"utterance_id": uncovered["utterance_id"]},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "AI 未返回可用归章 JSON"


async def test_source_review_queue_creates_and_lists_pending_route_suggestion(client, test_db, monkeypatch):
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
    uncovered = await create_test_voice_transcript(
        client,
        test_db,
        "后来想起外婆家的院子，最记得夏天一起摘菜和剥青豆。",
    )
    _patch_source_router_ai(monkeypatch, _assign_route(chapter), _assign_route(chapter))

    created = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-source-review-items",
        json={"utterance_id": uncovered["utterance_id"]},
    )

    assert created.status_code == 201
    item = created.json()
    assert item["status"] == "needs_review"
    assert item["route_action"] == "assign_to_chapter"
    assert item["utterance_id"] == uncovered["utterance_id"]
    assert item["target_chapter_title"] == "院子里的童年"

    duplicate = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-source-review-items",
        json={"utterance_id": uncovered["utterance_id"]},
    )
    assert duplicate.status_code == 201
    assert duplicate.json()["id"] == item["id"]

    listed = await client.get(f"/api/mobile/memoir/projects/{project_id}/book-source-review-items")

    assert listed.status_code == 200
    data = listed.json()
    assert data["total"] == 1
    assert data["items"][0]["id"] == item["id"]

    acknowledge = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/automation-events/{item['id']}/acknowledge"
    )
    assert acknowledge.status_code == 400
    assert acknowledge.json()["detail"] == "自动处理记录仍需处理，不能直接确认隐藏"


async def test_source_review_queue_confirm_assigns_source_and_resolves_item(client, test_db, monkeypatch):
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
    uncovered = await create_test_voice_transcript(
        client,
        test_db,
        "后来想起外婆家的院子，最记得夏天一起摘菜和剥青豆。",
    )
    _patch_source_router_ai(monkeypatch, _assign_route(chapter))
    created = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-source-review-items",
        json={"utterance_id": uncovered["utterance_id"]},
    )
    event_id = created.json()["id"]

    confirmed = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-source-review-items/{event_id}/confirm",
        json={},
    )

    assert confirmed.status_code == 200
    data = confirmed.json()
    assert data["item"]["status"] == "completed"
    assert data["item"]["metadata_json"]["resolution"] == "confirmed"
    chapters = {item["id"]: item for item in data["plan"]["chapters"]}
    source_ids = [ref["utterance_id"] for ref in chapters[chapter.id]["source_refs"]]
    assert uncovered["utterance_id"] in source_ids
    assert chapters[chapter.id]["pending_source_count"] == 1

    pending = await client.get(f"/api/mobile/memoir/projects/{project_id}/book-source-review-items")
    assert pending.json()["total"] == 0


async def test_source_review_queue_confirm_creates_new_chapter_and_resolves_item(client, test_db, monkeypatch):
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
    _patch_source_router_ai(monkeypatch, _new_chapter_route("供销社工作初记"))
    created = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-source-review-items",
        json={"utterance_id": uncovered["utterance_id"]},
    )
    event_id = created.json()["id"]

    confirmed = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-source-review-items/{event_id}/confirm",
        json={},
    )

    assert confirmed.status_code == 200
    data = confirmed.json()
    new_chapter = next(
        chapter for chapter in data["plan"]["chapters"]
        if chapter["title"] == "供销社工作初记"
    )
    assert any(ref["utterance_id"] == uncovered["utterance_id"] for ref in new_chapter["source_refs"])
    assert data["item"]["target_chapter_id"] == new_chapter["id"]
    assert data["item"]["metadata_json"]["resolved_chapter_id"] == new_chapter["id"]
    assert data["item"]["metadata_json"]["resolved_chapter_title"] == "供销社工作初记"


async def test_source_review_queue_rejects_ignored_route(client, test_db, monkeypatch):
    first = await create_test_voice_transcript(client, test_db, "小时候在老家院子里玩。")
    project_id = first["session"]["project_id"]
    await create_test_book_plan(
        test_db,
        project_id=project_id,
        source_ref=memoir_source_ref(first, "老家院子"),
        chapter_title="老家院子",
    )
    short = await create_test_voice_transcript(client, test_db, "你能不能别问了")
    _patch_source_router_ai(monkeypatch, _ignore_route())

    created = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-source-review-items",
        json={"utterance_id": short["utterance_id"]},
    )

    assert created.status_code == 400
    assert created.json()["detail"] == "这条讲述不适合收入章节"


async def test_source_review_queue_ignore_resolves_without_changing_plan(client, test_db, monkeypatch):
    first = await create_test_voice_transcript(client, test_db, "小时候在老家院子里玩。")
    project_id = first["session"]["project_id"]
    await create_test_book_plan(
        test_db,
        project_id=project_id,
        source_ref=memoir_source_ref(first, "老家院子"),
        chapter_title="老家院子",
    )
    uncovered = await create_test_voice_transcript(client, test_db, "后来还记得老家院子里的枣树。")
    _patch_source_router_ai(monkeypatch, _new_chapter_route("老家院子补充"))
    created = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-source-review-items",
        json={"utterance_id": uncovered["utterance_id"]},
    )
    event_id = created.json()["id"]

    ignored = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-source-review-items/{event_id}/ignore",
    )

    assert ignored.status_code == 200
    data = ignored.json()
    assert data["status"] == "completed"
    assert data["metadata_json"]["resolution"] == "ignored"

    resolved = await client.get(
        f"/api/mobile/memoir/projects/{project_id}/book-source-review-items",
        params={"include_resolved": "true"},
    )
    assert resolved.json()["total"] == 1
    assert resolved.json()["items"][0]["id"] == event_id
