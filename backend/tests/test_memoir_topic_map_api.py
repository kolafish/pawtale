from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.domain.memoir.narrative_types import (
    MemoirFrameworkMatchSource,
    MemoirFrameworkUserResolution,
)
from app.models.memoir import MemoirVoiceUtterance
from app.models.memoir_narrative import MemoirFrameworkItem
from app.services.memoir.narrative import frameworks
from tests.memoir_helpers import create_test_voice_transcript


async def _create_project(client, title: str) -> str:
    response = await client.post(
        "/api/mobile/memoir/projects",
        json={"title": title},
    )
    assert response.status_code == 201
    return response.json()["id"]


async def test_mobile_topic_map_is_not_a_completion_checklist(client):
    project_id = await _create_project(client, "人生话题")
    response = await client.get(
        f"/api/mobile/memoir/projects/{project_id}/topic-map"
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["modules"]) == 1
    assert sum(len(module["items"]) for module in payload["modules"]) == 7
    assert "必须全部讲完" in payload["detail"]
    assert {
        item["display_state"]
        for module in payload["modules"]
        for item in module["items"]
    } == {"not_started"}


async def test_user_resolution_survives_material_matching(client, test_db):
    created = await create_test_voice_transcript(
        client,
        test_db,
        text="小时候我在老家院子里生活。",
    )
    project_id = created["session"]["project_id"]
    topic_map = (
        await client.get(f"/api/mobile/memoir/projects/{project_id}/topic-map")
    ).json()
    item = topic_map["modules"][0]["items"][0]
    side_item = topic_map["modules"][0]["items"][1]
    response = await client.patch(
        (
            f"/api/mobile/memoir/projects/{project_id}/topic-map/items/"
            f"{item['id']}/resolution"
        ),
        json={"user_resolution": "do_not_discuss"},
    )
    assert response.status_code == 200

    framework = await frameworks.framework_for_project(
        test_db,
        project_id=project_id,
    )
    material = await test_db.scalar(
        select(MemoirVoiceUtterance)
        .where(MemoirVoiceUtterance.id == created["utterance_id"])
        .options(selectinload(MemoirVoiceUtterance.chat_message))
    )
    await frameworks.reconcile_automatic_material_matches(
        test_db,
        project_framework=framework,
        materials=[material],
        matches=(
            frameworks.FrameworkMaterialMatch(
                utterance_id=material.id,
                item_key=item["item_key"],
                confidence=0.7,
            ),
            frameworks.FrameworkMaterialMatch(
                utterance_id=material.id,
                item_key=side_item["item_key"],
                confidence=0.3,
            ),
        ),
        match_source=MemoirFrameworkMatchSource.EVALUATOR,
        matcher_version="topic-map-api-test",
    )

    stored_item = await test_db.get(MemoirFrameworkItem, item["id"])
    assert stored_item.user_resolution == (
        MemoirFrameworkUserResolution.DO_NOT_DISCUSS.value
    )
    cleared = await client.patch(
        (
            f"/api/mobile/memoir/projects/{project_id}/topic-map/items/"
            f"{item['id']}/resolution"
        ),
        json={"user_resolution": "none"},
    )
    assert cleared.status_code == 200
    assert cleared.json()["display_state"] == "touched"
    refreshed = (
        await client.get(f"/api/mobile/memoir/projects/{project_id}/topic-map")
    ).json()
    states = {
        topic["id"]: topic["display_state"]
        for module in refreshed["modules"]
        for topic in module["items"]
    }
    assert states[item["id"]] == "touched"
    assert states[side_item["id"]] == "not_started"


async def test_topic_map_resolution_rejects_cross_project_item(client):
    first_project_id = await _create_project(client, "第一本")
    second_project_id = await _create_project(client, "第二本")
    second_map = (
        await client.get(
            f"/api/mobile/memoir/projects/{second_project_id}/topic-map"
        )
    ).json()
    second_item_id = second_map["modules"][0]["items"][0]["id"]

    response = await client.patch(
        (
            f"/api/mobile/memoir/projects/{first_project_id}/topic-map/items/"
            f"{second_item_id}/resolution"
        ),
        json={"user_resolution": "not_applicable"},
    )
    assert response.status_code == 404
