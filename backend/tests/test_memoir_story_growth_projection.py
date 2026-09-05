from datetime import datetime

from sqlalchemy import select

from app.models.memoir import MemoirAutomationEvent, MemoirSectionMaterial
from app.schemas.memoir_voice import MemoirAutomationEventResponse
from app.services.memoir.story_growth_projection import story_growth_events
from tests.test_memoir_automatic_book_draft import _setup


def event(**patch):
    return MemoirAutomationEventResponse.model_validate({
        "id": "growth-1", "project_id": "project-1", "event_type": "story_structure_updated",
        "status": "completed", "summary_text": "本次讲述已整理", "utterance_ids": ["source-1", "source-2"],
        "metadata_json": {}, "created_at": datetime(2026, 9, 5), "updated_at": datetime(2026, 9, 5),
        **patch,
    })


def project(events):
    return [item.model_dump(mode="json") for item in story_growth_events(
        events, chapter_ids={"chapter-1", "chapter-2"},
        section_chapters={"section-1": "chapter-1", "section-2": "chapter-2"},
        section_by_utterance={"source-1": "section-1", "source-2": "section-2"},
    )]


def test_growth_distinguishes_new_structure_from_material_added_to_an_existing_story():
    events = project([event(metadata_json={
        "new_chapter_ids": ["chapter-2", "chapter-2"], "new_section_ids": ["section-2"],
    })])
    assert events[0]["event_id"] == "growth-1"
    assert events[0]["changes"] == [
        {"kind": "chapter_added", "chapter_id": "chapter-2"},
        {"kind": "section_updated", "chapter_id": "chapter-1", "section_id": "section-1"},
        {"kind": "section_added", "chapter_id": "chapter-2", "section_id": "section-2"},
    ]


def test_growth_resolves_current_membership_and_ignores_missing_or_foreign_targets():
    events = project([event(utterance_ids=["source-1", "deleted-source"], metadata_json={
        "new_chapter_ids": ["foreign-chapter", None], "new_section_ids": ["archived-section", 42],
    })])
    assert events[0]["changes"] == [
        {"kind": "section_updated", "chapter_id": "chapter-1", "section_id": "section-1"},
    ]
    assert project([event(utterance_ids=[], metadata_json={"new_section_ids": "section-1"})])[0]["changes"] == []


def test_draft_and_book_events_are_updates_and_acknowledged_or_unfinished_events_are_absent():
    events = project([
        event(id="chapter", event_type="chapter_draft_generated", chapter_id="chapter-1"),
        event(id="book", event_type="book_output_generated"),
        event(id="failed", status="failed"),
        event(id="acknowledged", metadata_json={"acknowledged_at": "2026-09-05T00:00:00Z"}),
    ])
    assert [item["event_id"] for item in events] == ["chapter", "book"]
    assert events[0]["changes"] == [{"kind": "chapter_updated", "chapter_id": "chapter-1"}]
    assert events[1]["changes"] == [{"kind": "book_updated"}]


async def test_story_snapshot_projects_persisted_growth_without_ai_and_acknowledges_explicitly(
    client, test_db, monkeypatch,
):
    project_id, chapter_id, section_id, fake, _ = await _setup(client, test_db, monkeypatch)
    source_id = await test_db.scalar(select(MemoirSectionMaterial.utterance_id).where(
        MemoirSectionMaterial.section_id == section_id,
    ))
    persisted = MemoirAutomationEvent(
        project_id=project_id, event_type="story_structure_updated", status="completed",
        summary_text="给已有故事补充了一段讲述", utterance_ids_json=[source_id],
        metadata_json={"new_section_ids": ["missing-section"]},
    )
    test_db.add(persisted)
    await test_db.commit()
    for _ in range(2):
        response = await client.get("/api/mobile/memoir/story-snapshot")
        assert response.status_code == 200, response.text
        growth = response.json()["growth_events"]
        assert response.json()["latest_transcript"]["id"]
        assert response.json()["latest_transcript"]["project_id"] == project_id
        assert len(growth) == 1
        assert growth[0]["event_id"] == persisted.id
        assert growth[0]["changes"] == [
            {"kind": "section_updated", "chapter_id": chapter_id, "section_id": section_id},
        ]
    assert not persisted.metadata_json.get("acknowledged_at")
    assert fake.prompts == []
    response = await client.post(f"/api/mobile/memoir/projects/{project_id}/automation-events/{persisted.id}/acknowledge")
    assert response.status_code == 200, response.text
    assert (await client.get("/api/mobile/memoir/story-snapshot")).json()["growth_events"] == []


async def test_empty_or_logged_out_accounts_cannot_read_another_projects_growth(client, anonymous_client, test_db):
    from app.models.projects import Project
    from app.models.users import User

    other_user = User(email="other-story@example.com", password_hash="unused", display_name="其他用户")
    test_db.add(other_user)
    await test_db.flush()
    other_project = Project(user_id=other_user.id, type="memoir", title="其他人的故事")
    test_db.add(other_project)
    await test_db.flush()
    test_db.add(MemoirAutomationEvent(
        project_id=other_project.id, event_type="book_output_generated", status="completed", summary_text="私有书稿已生成",
    ))
    await test_db.commit()
    response = await client.get("/api/mobile/memoir/story-snapshot")
    assert response.status_code == 200
    assert response.json()["growth_events"] == []
    await anonymous_client.post("/api/auth/logout")
    assert (await anonymous_client.get("/api/mobile/memoir/story-snapshot")).status_code == 401
