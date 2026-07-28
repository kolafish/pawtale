from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from sqlalchemy import select

from app.clock import utcnow_naive
from app.models.git_sync import GitHubSetupState
from app.services.projects import git_sync as git_sync_service
from tests.project_helpers import create_project_with_document


class FakeGitHubClient:
    configured = True
    install_url = "https://github.com/apps/pawtale/installations/new"

    def __init__(self):
        self.files: list[dict] = []
        self.commits: list[dict] = []

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

    async def commit_files(self, **kwargs):
        self.commits.append(kwargs)
        self.files.extend(
            {"path": file.path, "content": file.content}
            for file in kwargs["files"]
        )
        return f"commit-{len(self.commits)}"


class TokenGitHubClient(git_sync_service.GitHubAppClient):
    def __init__(self, responses: list[dict]):
        super().__init__()
        self.responses = responses
        self.requests: list[dict] = []

    def _app_jwt(self) -> str:
        return "app-jwt"

    async def _request(self, method, path, *, token, json_body=None, expected_status=None):
        self.requests.append(
            {
                "method": method,
                "path": path,
                "token": token,
                "json_body": json_body,
                "expected_status": expected_status,
            }
        )
        return self.responses.pop(0)


def _github_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _state_from_install_url(install_url: str) -> str:
    values = parse_qs(urlparse(install_url).query).get("state") or []
    assert len(values) == 1
    return values[0]


async def _create_setup_state(client) -> str:
    response = await client.post("/api/github/setup-url")
    assert response.status_code == 200
    data = response.json()
    assert data["install_url"].startswith("https://github.com/apps/pawtale/installations/new")
    assert data["expires_at"]
    return _state_from_install_url(data["install_url"])


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

    state = await _create_setup_state(client)
    setup = await client.get(f"/api/github/setup?installation_id=123&state={state}", follow_redirects=False)
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
    assert run_data["commit_sha"] == "commit-1"
    assert len(fake.commits) == 1
    assert fake.commits[0]["message"] == "Sync Pawtale project backup"

    paths = {item["path"] for item in fake.files}
    assert "novels/demo/pawtale-project.json" in paths
    assert any(path.startswith("novels/demo/documents/") and path.endswith(f"{document_id}.md") for path in paths)
    assert any(path.startswith("novels/demo/references/") and path.endswith(".json") for path in paths)

    saved = await client.get(f"/api/projects/{project_id}/git-sync")
    assert saved.status_code == 200
    saved_config = saved.json()["project_config"]
    assert saved_config["last_commit_sha"] == "commit-1"
    assert saved_config["last_error"] is None


async def test_github_setup_callback_requires_state(client, monkeypatch):
    fake = FakeGitHubClient()
    monkeypatch.setattr(git_sync_service, "GitHubAppClient", lambda: fake)

    missing = await client.get("/api/github/setup?installation_id=123", follow_redirects=False)
    invalid = await client.get("/api/github/setup?installation_id=123&state=not-real", follow_redirects=False)

    assert missing.status_code == 422
    assert invalid.status_code == 400
    assert invalid.json()["detail"] == "GitHub setup state 无效"


async def test_github_setup_state_is_single_use(client, monkeypatch):
    fake = FakeGitHubClient()
    monkeypatch.setattr(git_sync_service, "GitHubAppClient", lambda: fake)
    state = await _create_setup_state(client)

    first = await client.get(f"/api/github/setup?installation_id=123&state={state}", follow_redirects=False)
    second = await client.get(f"/api/github/setup?installation_id=123&state={state}", follow_redirects=False)

    assert first.status_code in {302, 307}
    assert second.status_code == 400
    assert second.json()["detail"] == "GitHub setup state 已使用"


async def test_github_setup_state_is_user_scoped(anonymous_client, monkeypatch):
    fake = FakeGitHubClient()
    monkeypatch.setattr(git_sync_service, "GitHubAppClient", lambda: fake)
    first_register = await anonymous_client.post(
        "/api/auth/register",
        json={"email": "github-first@example.com", "password": "password123"},
    )
    assert first_register.status_code == 201
    state = await _create_setup_state(anonymous_client)
    await anonymous_client.post("/api/auth/logout")
    second_register = await anonymous_client.post(
        "/api/auth/register",
        json={"email": "github-second@example.com", "password": "password123"},
    )
    assert second_register.status_code == 201

    response = await anonymous_client.get(
        f"/api/github/setup?installation_id=123&state={state}",
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "GitHub setup state 不属于当前用户"


async def test_github_setup_state_expires(client, test_db, monkeypatch):
    fake = FakeGitHubClient()
    monkeypatch.setattr(git_sync_service, "GitHubAppClient", lambda: fake)
    state = await _create_setup_state(client)
    row = await test_db.scalar(select(GitHubSetupState).where(GitHubSetupState.state == state))
    assert row is not None
    row.expires_at = utcnow_naive() - timedelta(seconds=1)
    await test_db.commit()

    response = await client.get(
        f"/api/github/setup?installation_id=123&state={state}",
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "GitHub setup state 已过期"


async def test_github_installation_token_cache_reuses_valid_token():
    client = TokenGitHubClient(
        [
            {
                "token": "token-1",
                "expires_at": _github_timestamp(datetime.now(timezone.utc) + timedelta(hours=1)),
            }
        ]
    )

    first = await client.create_installation_token(123)
    second = await client.create_installation_token(123)

    assert first == "token-1"
    assert second == "token-1"
    assert len(client.requests) == 1


async def test_github_installation_token_cache_refreshes_near_expiry():
    client = TokenGitHubClient(
        [
            {
                "token": "token-1",
                "expires_at": _github_timestamp(datetime.now(timezone.utc) + timedelta(minutes=4)),
            },
            {
                "token": "token-2",
                "expires_at": _github_timestamp(datetime.now(timezone.utc) + timedelta(hours=1)),
            },
        ]
    )

    first = await client.create_installation_token(123)
    second = await client.create_installation_token(123)

    assert first == "token-1"
    assert second == "token-2"
    assert len(client.requests) == 2


async def test_github_installation_token_cache_ignores_invalid_expiry():
    client = TokenGitHubClient(
        [
            {"token": "token-1", "expires_at": "not-a-date"},
            {
                "token": "token-2",
                "expires_at": _github_timestamp(datetime.now(timezone.utc) + timedelta(hours=1)),
            },
        ]
    )

    first = await client.create_installation_token(123)
    second = await client.create_installation_token(123)

    assert first == "token-1"
    assert second == "token-2"
    assert len(client.requests) == 2


async def test_github_commit_files_updates_ref_with_single_commit():
    client = TokenGitHubClient(
        [
            {
                "token": "token-1",
                "expires_at": _github_timestamp(datetime.now(timezone.utc) + timedelta(hours=1)),
            },
            {"object": {"sha": "base-commit"}},
            {"tree": {"sha": "base-tree"}},
            {"sha": "blob-1"},
            {"sha": "blob-2"},
            {"sha": "new-tree"},
            {"sha": "new-commit"},
            {"ref": "refs/heads/feature/demo"},
        ]
    )

    commit_sha = await client.commit_files(
        installation_id=123,
        owner="author",
        repo="pawtale-backup",
        branch="feature/demo",
        files=[
            git_sync_service.BackupFile(path="pawtale/project.json", content='{"ok": true}\n'),
            git_sync_service.BackupFile(path="pawtale/documents/001.md", content="# 第一章\n"),
        ],
        message="Sync Pawtale project backup",
    )

    assert commit_sha == "new-commit"
    request_keys = [(request["method"], request["path"]) for request in client.requests]
    assert request_keys == [
        ("POST", "/app/installations/123/access_tokens"),
        ("GET", "/repos/author/pawtale-backup/git/ref/heads/feature/demo"),
        ("GET", "/repos/author/pawtale-backup/git/commits/base-commit"),
        ("POST", "/repos/author/pawtale-backup/git/blobs"),
        ("POST", "/repos/author/pawtale-backup/git/blobs"),
        ("POST", "/repos/author/pawtale-backup/git/trees"),
        ("POST", "/repos/author/pawtale-backup/git/commits"),
        ("PATCH", "/repos/author/pawtale-backup/git/refs/heads/feature/demo"),
    ]
    assert not any(request["method"] == "PUT" for request in client.requests)
    tree_request = client.requests[5]
    assert tree_request["json_body"] == {
        "base_tree": "base-tree",
        "tree": [
            {"path": "pawtale/project.json", "mode": "100644", "type": "blob", "sha": "blob-1"},
            {"path": "pawtale/documents/001.md", "mode": "100644", "type": "blob", "sha": "blob-2"},
        ],
    }
    commit_request = client.requests[6]
    assert commit_request["json_body"] == {
        "message": "Sync Pawtale project backup",
        "tree": "new-tree",
        "parents": ["base-commit"],
    }
    ref_request = client.requests[7]
    assert ref_request["json_body"] == {"sha": "new-commit", "force": False}
