import json

from sqlalchemy import select

from app.services.ai_base import AITextServiceBase
from app.domain.memoir.types import (
    MemoirSectionMaterialAssignedBy,
)
from app.models.memoir import MemoirSection, MemoirSectionMaterial
from app.services.memoir.book_plan_sources import (
    renumber_chapters,
    set_temporary_chapter_orders,
    unique_source_ref_dicts,
)
from tests.memoir_helpers import (
    create_test_voice_transcript,
    ensure_mobile_memoir_project,
    memoir_source_ref,
)


class FakeBookPlanAI(AITextServiceBase):
    provider_name = "fake-ai"
    model_name = "fake-book-plan"

    def __init__(self):
        self.prompts: list[str] = []

    async def _call_llm(self, _system_prompt, user_prompt, **_kwargs):
        self.prompts.append(user_prompt)
        return json.dumps(
            {
                "title": "外婆回忆录章节规划",
                "chapters": [
                    {
                        "title": "外婆家的童年",
                        "source_indices": [1],
                        "evidence_quote": "外婆家的院子",
                    },
                    {
                        "title": "离家求学",
                        "source_indices": [2],
                        "evidence_quote": "第一次离家",
                    },
                ],
            },
            ensure_ascii=False,
        )


def test_book_plan_source_ref_helpers():
    assert unique_source_ref_dicts(
        [
            {"utterance_id": "u1", "quote": "一"},
            {"utterance_id": "u1", "quote": "重复"},
            {"utterance_id": "u2", "quote": "二"},
            {"missing": "ignored"},
        ]
    ) == [
        {"utterance_id": "u1", "quote": "一"},
        {"utterance_id": "u2", "quote": "二"},
    ]

    class Chapter:
        sort_order = 0

    first, second = Chapter(), Chapter()
    set_temporary_chapter_orders([first, second])
    assert [first.sort_order, second.sort_order] == [10000, 10001]
    renumber_chapters([second, first])
    assert [second.sort_order, first.sort_order] == [0, 1]


async def test_book_plan_routes_require_login(anonymous_client):
    response = await anonymous_client.post(
        "/api/mobile/memoir/projects/project-1/book-plan/generate",
        json={},
    )
    assert response.status_code == 401


async def test_book_plan_requires_material(client):
    project_id = await ensure_mobile_memoir_project(client)
    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/generate",
        json={},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "暂无讲述内容，无法规划章节"


async def test_generate_plan_creates_single_section_material_ownership(
    client,
    test_db,
    monkeypatch,
):
    first = await create_test_voice_transcript(
        client,
        test_db,
        "我小时候常在外婆家的院子里晒太阳。",
    )
    second = await create_test_voice_transcript(
        client,
        test_db,
        "后来我第一次离家去县城读书。",
    )
    project_id = first["session"]["project_id"]
    fake_ai = FakeBookPlanAI()
    monkeypatch.setattr(
        "app.services.memoir.book_plan_ai.get_ai_service",
        lambda _model=None: fake_ai,
    )

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/generate",
        json={},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "confirmed"
    assert first["utterance_id"] not in fake_ai.prompts[0]
    assert first["message_id"] not in fake_ai.prompts[0]
    assert "source_index: 1" in fake_ai.prompts[0]
    assert [item["title"] for item in payload["chapters"]] == [
        "外婆家的童年",
        "离家求学",
    ]
    first_section = payload["chapters"][0]["sections"][0]
    assert first_section["title"] == "外婆家的童年"
    assert first_section["section_type"] == "life_fragment"
    assert first_section["material_count"] == 1
    assert first_section["included_material_count"] == 1
    assert first_section["materials"] == [
        {
            "utterance_id": first["utterance_id"],
            "sort_order": 0,
            "text": "我小时候常在外婆家的院子里晒太阳。",
            "include_in_book": True,
        }
    ]
    materials = list(
        (
            await test_db.scalars(
                select(MemoirSectionMaterial).where(
                    MemoirSectionMaterial.project_id == project_id
                )
            )
        ).all()
    )
    sections = list(
        (
            await test_db.scalars(
                select(MemoirSection).where(
                    MemoirSection.project_id == project_id
                )
            )
        ).all()
    )
    assert {item.utterance_id for item in materials} == {
        first["utterance_id"],
        second["utterance_id"],
    }
    assert len(sections) == 2
    assert all(
        item.assigned_by == MemoirSectionMaterialAssignedBy.AI.value
        for item in materials
    )


async def test_confirm_plan_replaces_sections_without_legacy_links(
    client,
    test_db,
):
    first = await create_test_voice_transcript(client, test_db, "外婆家的院子。")
    second = await create_test_voice_transcript(client, test_db, "第一次离家。")
    project_id = first["session"]["project_id"]

    response = await client.put(
        f"/api/mobile/memoir/projects/{project_id}/book-plan",
        json={
            "title": "我的回忆录",
            "chapters": [
                {
                    "title": "童年与离家",
                    "source_refs": [
                        memoir_source_ref(first),
                        memoir_source_ref(second),
                    ],
                }
            ],
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "confirmed"
    assert len(payload["chapters"]) == 1
    assert {
        ref["utterance_id"]
        for ref in payload["chapters"][0]["source_refs"]
    } == {first["utterance_id"], second["utterance_id"]}
    memberships = list(
        (
            await test_db.scalars(
                select(MemoirSectionMaterial).where(
                    MemoirSectionMaterial.project_id == project_id
                )
            )
        ).all()
    )
    assert len(memberships) == 2
    assert len({item.section_id for item in memberships}) == 1

    snapshot_response = await client.get("/api/mobile/memoir/book-snapshot")
    assert snapshot_response.status_code == 200
    snapshot = snapshot_response.json()
    chapter_id = payload["chapters"][0]["id"]
    assert snapshot["plan"]["id"] == payload["id"]
    assert snapshot["plan"]["chapters"][0]["id"] == chapter_id
    assert snapshot["drafts_by_chapter_id"] == {chapter_id: None}


async def test_book_plan_can_hide_and_restore_chapter(client, test_db):
    first = await create_test_voice_transcript(client, test_db, "童年。")
    second = await create_test_voice_transcript(client, test_db, "工作。")
    project_id = first["session"]["project_id"]
    confirmed = await client.put(
        f"/api/mobile/memoir/projects/{project_id}/book-plan",
        json={
            "chapters": [
                {"title": "童年", "source_refs": [memoir_source_ref(first)]},
                {"title": "工作", "source_refs": [memoir_source_ref(second)]},
            ]
        },
    )
    chapters = confirmed.json()["chapters"]

    hidden = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/structure-commands",
        json={
            "type": "set_chapter_book_inclusion",
            "chapter_id": chapters[1]["id"],
            "include_in_book": False,
        },
    )
    restored = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/structure-commands",
        json={
            "type": "set_chapter_book_inclusion",
            "chapter_id": chapters[1]["id"],
            "include_in_book": True,
        },
    )

    assert hidden.status_code == 200
    assert hidden.json()["chapters"][1]["include_in_book"] is False
    assert restored.status_code == 200
    assert all(
        chapter["include_in_book"]
        for chapter in restored.json()["chapters"]
    )


async def test_confirm_plan_rejects_foreign_material(client, test_db):
    own = await create_test_voice_transcript(client, test_db, "自己的素材。")
    await client.post("/api/auth/logout")
    await client.post(
        "/api/auth/register",
        json={
            "email": "other-plan@example.com",
            "password": "password123",
            "display_name": "另一个用户",
        },
    )
    other = await create_test_voice_transcript(client, test_db, "别人的素材。")
    await client.post("/api/auth/logout")
    await client.post(
        "/api/auth/login",
        json={"email": "tester@example.com", "password": "password123"},
    )

    response = await client.put(
        (
            f"/api/mobile/memoir/projects/"
            f"{own['session']['project_id']}/book-plan"
        ),
        json={
            "chapters": [
                {
                    "title": "错误章节",
                    "source_refs": [memoir_source_ref(other)],
                }
            ]
        },
    )
    assert response.status_code == 400


async def test_confirm_plan_rejects_duplicate_material_without_replacing_current_plan(client, test_db):
    source = await create_test_voice_transcript(client, test_db, "一段完整的童年经历。")
    project_id = source["session"]["project_id"]
    url = f"/api/mobile/memoir/projects/{project_id}/book-plan"
    saved = await client.put(url, json={"chapters": [
        {"title": "童年", "source_refs": [memoir_source_ref(source)]},
    ]})
    assert saved.status_code == 200, saved.text
    before = saved.json()

    rejected = await client.put(url, json={"chapters": [
        {"title": "童年", "source_refs": [memoir_source_ref(source)]},
        {"title": "另一章", "source_refs": [memoir_source_ref(source)]},
    ]})

    assert rejected.status_code == 400
    current = (await client.get(url)).json()
    assert current["id"] == before["id"]
    assert current["chapters"] == before["chapters"]
