from app.domain.memoir.types import MemoirEditingJobStatus
from app.domain.projects.types import ProjectType
from app.models.memoir import MemoirEditingJob
from app.models.projects import Project
from app.models.users import User
from tests.memoir_helpers import ensure_mobile_memoir_project


def _job_status_url(project_id: str) -> str:
    return f"/api/mobile/memoir/projects/{project_id}/book-output/editorial-copy/regenerate/jobs"


async def test_book_editorial_copy_jobs_report_each_book_level_target_status(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    url = _job_status_url(project_id)

    empty = await client.get(url)
    assert empty.status_code == 200
    assert empty.json() == {"items": []}

    combined = await client.post(url, json={"targets": ["preface", "afterword"]})
    assert combined.status_code == 200
    assert (await client.get(url)).json() == {"items": []}

    job_ids = {}
    for target in ("preface", "afterword", "back_cover_quote", "subtitle"):
        response = await client.post(url, json={"targets": [target]})
        assert response.status_code == 200
        job_ids[target] = response.json()["editing_job_id"]

    afterword = await test_db.get(MemoirEditingJob, job_ids["afterword"])
    back_cover = await test_db.get(MemoirEditingJob, job_ids["back_cover_quote"])
    afterword.status = MemoirEditingJobStatus.RUNNING.value
    back_cover.status = MemoirEditingJobStatus.SUCCEEDED.value
    await test_db.commit()

    response = await client.get(url)
    assert response.status_code == 200
    items = response.json()["items"]
    assert [(item["target"], item["job_id"], item["status"]) for item in items] == [
        ("preface", job_ids["preface"], "pending"),
        ("afterword", job_ids["afterword"], "running"),
        ("back_cover_quote", job_ids["back_cover_quote"], "succeeded"),
    ]
    opted_in = (await client.get(url, params={"include_subtitle": True})).json()["items"]
    assert [(j["target"], j["job_id"], j["status"]) for j in opted_in if j["target"] == "subtitle"] == [
        ("subtitle", job_ids["subtitle"], "pending"),
    ]
    assert all(item["updated_at"] and item["error_message"] == "" for item in items)

    back_cover.status = MemoirEditingJobStatus.FAILED.value
    back_cover.error_message = "生成失败，请重试"
    await test_db.commit()
    failed = (await client.get(url)).json()["items"][2]
    assert failed["status"] == "failed"
    assert failed["error_message"] == "生成失败，请重试"


async def test_book_editorial_copy_jobs_are_scoped_to_project_and_owner(
    client,
    test_db,
    test_user_password_hash,
):
    project_id = await ensure_mobile_memoir_project(client)
    other_project_response = await client.post(
        "/api/mobile/memoir/projects", json={"title": "另一本回忆录"}
    )
    assert other_project_response.status_code == 201
    other_project_id = other_project_response.json()["id"]
    queued = await client.post(_job_status_url(other_project_id), json={"targets": ["preface"]})
    assert queued.status_code == 200
    assert (await client.get(_job_status_url(project_id))).json() == {"items": []}
    other_items = (await client.get(_job_status_url(other_project_id))).json()["items"]
    assert len(other_items) == 1
    assert other_items[0]["job_id"] == queued.json()["editing_job_id"]

    other_user = User(
        email="other-editorial@example.com",
        password_hash=test_user_password_hash,
        display_name="其他用户",
    )
    test_db.add(other_user)
    await test_db.flush()
    foreign_project = Project(
        user_id=other_user.id,
        type=ProjectType.MEMOIR.value,
        title="其他用户的回忆录",
    )
    test_db.add(foreign_project)
    await test_db.commit()

    assert (await client.get(_job_status_url(foreign_project.id))).status_code == 404


async def test_book_editorial_copy_jobs_require_login(anonymous_client):
    response = await anonymous_client.get(_job_status_url("missing-project"))
    assert response.status_code == 401
