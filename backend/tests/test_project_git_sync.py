from __future__ import annotations

from app.services.projects import git_sync as git_sync_service
from tests.project_helpers import create_project_with_document


class FakeGitHubClient:
    configured = True
    install_url = "https://github.com/apps/pawtale/installations/new"

    def __init__(self):
        self.files: list[dict] = []

    async def get_installation(self, installation_id: int):
        assert installation_id == 123
        return {
            "repository_selection": "selected",
            "account": {"login": "author", "type": "User"},
        }

    async def list_repositories(self, installation_id: int):
        assert installation_id == 123
        return [
            {
                "name": "pawtale-backup",
                "full_name": "author/pawtale-backup",
                "private": True,
                "default_branch": "main",
                "owner": {"login": "author"},
            }
        ]

    async def put_file(self, **kwargs):
        self.files.append(kwargs)
        return f"commit-{len(self.files)}"


async def test_git_sync_status_reports_unconfigured(client):
    project_id, _ = await create_project_with_document(client, "Git 同步")

    response = await client.get(f"/api/projects/{project_id}/git-sync")

    assert response.status_code == 200
    data = response.json()
    assert data["configured"] is False
    assert data["repositories"] == []
    assert data["project_config"] is None


async def test_configure_and_run_git_sync(client, monkeypatch):
    fake = FakeGitHubClient()
    monkeypatch.setattr(git_sync_service, "GitHubAppClient", lambda: fake)
    project_id, document_id = await create_project_with_document(client, "Git 同步项目")
    await client.put(
        f"/api/projects/{project_id}/documents/{document_id}",
        json={"content": "# 第一章\n\n正文。"},
    )
    await client.post(
        f"/api/projects/{project_id}/references",
        json={"type": "outline", "name": "主线", "content": "大纲内容"},
    )

    setup = await client.get("/api/github/setup?installation_id=123", follow_redirects=False)
    assert setup.status_code in {302, 307}

    status = await client.get(f"/api/projects/{project_id}/git-sync")
    assert status.status_code == 200
    status_data = status.json()
    assert status_data["configured"] is True
    assert status_data["repositories"][0]["full_name"] == "author/pawtale-backup"

    config = await client.put(
        f"/api/projects/{project_id}/git-sync",
        json={
            "installation_id": 123,
            "repository_owner": "author",
            "repository_name": "pawtale-backup",
            "branch": "main",
            "base_path": "novels/demo",
        },
    )
    assert config.status_code == 200
    assert config.json()["base_path"] == "novels/demo"

    run = await client.post(f"/api/projects/{project_id}/git-sync/run")
    assert run.status_code == 200
    run_data = run.json()
    assert run_data["ok"] is True
    assert run_data["synced_files"] == 3
    assert run_data["commit_sha"] == "commit-3"

    paths = {item["path"] for item in fake.files}
    assert "novels/demo/pawtale-project.json" in paths
    assert any(path.startswith("novels/demo/documents/") and path.endswith(f"{document_id}.md") for path in paths)
    assert any(path.startswith("novels/demo/references/") and path.endswith(".json") for path in paths)

    saved = await client.get(f"/api/projects/{project_id}/git-sync")
    assert saved.status_code == 200
    saved_config = saved.json()["project_config"]
    assert saved_config["last_commit_sha"] == "commit-3"
    assert saved_config["last_error"] is None
