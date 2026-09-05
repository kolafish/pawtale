import copy

import pytest
from sqlalchemy import func, select, update

from app.models.memoir import MemoirEditingJob, MemoirImageAsset, MemoirBookImagePlacement
from app.services.memoir import automatic_book_draft
from tests.memoir_helpers import run_test_editing_job
from tests.test_memoir_automatic_book_draft import _setup


async def test_story_snapshot_reads_counts_and_jobs_without_advancing_them(client, test_db, monkeypatch):
    project_id, chapter_id, section_id, fake, parent_id = await _setup(client, test_db, monkeypatch)
    from app.models.memoir import MemoirSectionMaterial
    from app.models.projects import Project

    source_id = await test_db.scalar(
        select(MemoirSectionMaterial.utterance_id).where(MemoirSectionMaterial.section_id == section_id)
    )
    project = await test_db.get(Project, project_id)
    assets = [
        MemoirImageAsset(
            project_id=project_id,
            user_id=project.user_id,
            source_type="manual_upload",
            source_signature=f"asset-{i}",
            source_utterance_id=source_id if i == 0 else None,
        )
        for i in range(3)
    ]
    test_db.add_all(assets)
    await test_db.flush()
    test_db.add(
        MemoirBookImagePlacement(
            project_id=project_id,
            image_asset_id=assets[1].id,
            chapter_id=chapter_id,
            placement_type="chapter_inline",
            enabled=True,
        )
    )
    await test_db.commit()
    job = await test_db.get(MemoirEditingJob, parent_id)
    before = (job.status, copy.deepcopy(job.result_json), job.run_after)
    count = await test_db.scalar(select(func.count()).select_from(MemoirEditingJob))
    response = await client.get("/api/mobile/memoir/story-snapshot")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["progress"]["status"] == "processing"
    assert data["chapters"][0]["sections"][0]["image_count"] == 1
    assert data["chapters"][0]["image_count"] == 1
    assert data["unassigned_image_count"] == 1
    assert data["chapters"][0]["material_count"] == 2
    assert fake.prompts == []
    await test_db.refresh(job)
    assert (job.status, job.result_json, job.run_after) == before
    assert await test_db.scalar(select(func.count()).select_from(MemoirEditingJob)) == count
    assert not await run_test_editing_job(test_db, parent_id)
    assert await run_test_editing_job(test_db, job.result_json["chapters"][chapter_id]["job_id"])
    assert not await run_test_editing_job(test_db, parent_id)
    data = (await client.get("/api/mobile/memoir/story-snapshot")).json()
    assert data["book_readable"] and data["book_char_count"] > 0
    assert data["chapters"][0]["readable"]
    assert data["progress"]["readable_chapter_count"] == 1
    assert "drafts_by_chapter_id" not in data and "output" not in data
    assert "content" not in data["chapters"][0] and "materials" not in data["chapters"][0]["sections"][0]


@pytest.mark.parametrize(
    "chapter_status,expected", [("failed", "failed"), ("needs_review", "needs_input"), ("ready", "ready")]
)
async def test_draft_progress_distinguishes_terminal_outcomes_and_explicit_retry(
    client, test_db, monkeypatch, chapter_status, expected
):
    project_id, chapter_id, _, _, parent_id = await _setup(client, test_db, monkeypatch)
    await test_db.execute(
        update(MemoirEditingJob).where(MemoirEditingJob.project_id == project_id).values(status="succeeded")
    )
    job = await test_db.get(MemoirEditingJob, parent_id)
    job.result_json = automatic_book_draft.DraftProgress(
        chapters={
            chapter_id: automatic_book_draft.ChapterProgress(
                status=chapter_status, error_message="模型超时" if chapter_status == "failed" else ""
            )
        }
    ).model_dump(mode="json")
    await test_db.commit()
    path = f"/api/mobile/memoir/projects/{project_id}/draft-progress"
    response = await client.get(path)
    assert response.status_code == 200
    assert response.json()["status"] == expected
    assert response.json()["can_retry"] is (chapter_status == "failed")
    if chapter_status == "failed":
        retried = await client.post(path + "/retry")
        assert retried.status_code == 202
        assert retried.json()["status"] == "processing"
        await test_db.refresh(job)
        assert job.payload_json["source"] == "retry"
        assert job.result_json["chapters"][chapter_id]["status"] == "failed"


async def test_progress_and_retry_enforce_project_ownership(client, test_db):
    from app.models.projects import Project
    from app.models.users import User

    user = User(email="another-reader@example.com", password_hash="unused", display_name="另一个用户")
    test_db.add(user)
    await test_db.flush()
    project = Project(user_id=user.id, type="memoir", title="另一个人的回忆录")
    test_db.add(project)
    await test_db.commit()
    path = f"/api/mobile/memoir/projects/{project.id}/draft-progress"
    assert (await client.get(path)).status_code == 404
    assert (await client.post(path + "/retry")).status_code == 404
    assert await test_db.scalar(select(func.count()).select_from(MemoirEditingJob)) == 0
