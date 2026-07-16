from app.domain.memoir.narrative_types import (
    MemoirClaimEvidenceRole,
    MemoirClaimPublicationStatus,
    MemoirClaimType,
    MemoirClaimVerificationStatus,
    MemoirFrameworkMatchSource,
    MemoirFrameworkUserResolution,
    MemoirNarrativeActorType,
)
from app.models.memoir_narrative import MemoirClaim, MemoirFrameworkItem
from app.services.memoir.narrative import frameworks


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
    assert payload["project_id"] == project_id
    assert payload["template_key"] == "memoir_life_topics"
    assert payload["template_version"] == "1"
    assert len(payload["modules"]) == 5
    assert sum(len(module["items"]) for module in payload["modules"]) == 12
    assert "不是章节目录" in payload["detail"]
    assert "必须全部讲完" in payload["detail"]
    assert {
        item["display_state"]
        for module in payload["modules"]
        for item in module["items"]
    } == {"can_share"}
    serialized = response.text
    assert "sufficient" not in serialized
    assert "coverage" not in serialized


async def test_user_resolution_can_be_set_and_revoked_without_evaluator_override(
    client,
    test_db,
):
    project_id = await _create_project(client, "用户选择")
    topic_map = (
        await client.get(f"/api/mobile/memoir/projects/{project_id}/topic-map")
    ).json()
    item = topic_map["modules"][0]["items"][0]

    response = await client.patch(
        (
            f"/api/mobile/memoir/projects/{project_id}/topic-map/items/"
            f"{item['id']}/resolution"
        ),
        json={
            "user_resolution": "do_not_discuss",
        },
    )

    assert response.status_code == 200
    resolved = response.json()
    assert resolved["display_state"] == "do_not_discuss"
    assert resolved["state_text"] == "不想聊这个"
    assert resolved["resolved_at"] is not None
    stored_item = await test_db.get(MemoirFrameworkItem, item["id"])
    first_resolved_at = stored_item.resolved_at
    assert stored_item.user_resolution == (
        MemoirFrameworkUserResolution.DO_NOT_DISCUSS.value
    )
    assert stored_item.user_resolution_note == ""
    assert stored_item.resolved_by_user_id is not None

    repeated = await client.patch(
        (
            f"/api/mobile/memoir/projects/{project_id}/topic-map/items/"
            f"{item['id']}/resolution"
        ),
        json={"user_resolution": "do_not_discuss"},
    )
    assert repeated.status_code == 200
    await test_db.refresh(stored_item)
    assert stored_item.resolved_at == first_resolved_at
    assert stored_item.user_resolution_note == ""

    claim = MemoirClaim(
        project_id=project_id,
        claim_type=MemoirClaimType.EVENT.value,
        statement="小时候我在老家院子里生活。",
        verification_status=MemoirClaimVerificationStatus.UNREVIEWED.value,
        publication_status=MemoirClaimPublicationStatus.ELIGIBLE.value,
        created_by=MemoirNarrativeActorType.AI.value,
        updated_by=MemoirNarrativeActorType.AI.value,
    )
    test_db.add(claim)
    await test_db.flush()
    project_framework = await frameworks.framework_for_project(
        test_db,
        project_id=project_id,
    )
    await frameworks.reconcile_automatic_claim_matches(
        test_db,
        project_framework=project_framework,
        claims=[claim],
        matches=(
            frameworks.FrameworkClaimMatch(
                claim_id=claim.id,
                item_key=item["item_key"],
                evidence_role=MemoirClaimEvidenceRole.SUPPORTS,
                confidence=0.7,
            ),
        ),
        match_source=MemoirFrameworkMatchSource.EVALUATOR,
        matcher_version="topic-map-api-test",
    )
    assert stored_item.user_resolution == (
        MemoirFrameworkUserResolution.DO_NOT_DISCUSS.value
    )
    refreshed_map = await client.get(
        f"/api/mobile/memoir/projects/{project_id}/topic-map"
    )
    assert refreshed_map.status_code == 200
    assert claim.statement not in refreshed_map.text

    cleared = await client.patch(
        (
            f"/api/mobile/memoir/projects/{project_id}/topic-map/items/"
            f"{item['id']}/resolution"
        ),
        json={"user_resolution": "none"},
    )

    assert cleared.status_code == 200
    assert cleared.json()["display_state"] == "already_shared"
    await test_db.refresh(stored_item)
    assert stored_item.user_resolution == MemoirFrameworkUserResolution.NONE.value
    assert stored_item.user_resolution_note == ""
    assert stored_item.resolved_by_user_id is None
    assert stored_item.resolved_at is None


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
