import asyncio
import base64
import hashlib
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.clock import utcnow_naive
from app.models.git_sync import GitHubInstallation, GitHubSetupState, GitHubUserAuthorization, ProjectGitSync
from app.services.github_client import GitHubSyncError, UserTokens
from app.services.projects import git_sync
from app.services.projects.github_authorization import save_tokens, user_access_token
from tests.project_helpers import create_project_with_document
from tests.test_project_git_sync import FakeGitHubClient, _create_setup_state, _state_from_install_url


async def _oauth_state(client, installation_id=123):
    state = await _create_setup_state(client)
    response = await client.get(
        f"/api/github/setup?installation_id={installation_id}&state={state}", follow_redirects=False,
    )
    assert response.status_code == 307
    return _state_from_install_url(response.headers["location"])


async def _connect(client):
    state = await _oauth_state(client)
    response = await client.get(f"/api/github/setup?code=valid-code&state={state}", follow_redirects=False)
    assert response.status_code == 307
    return state


async def test_claiming_another_installation_requires_github_user_access(client, test_db, monkeypatch):
    monkeypatch.setattr(git_sync, "GitHubAppClient", FakeGitHubClient)
    state = await _oauth_state(client, installation_id=999)
    assert await test_db.scalar(select(GitHubInstallation)) is None
    response = await client.get(f"/api/github/setup?code=valid-code&state={state}", follow_redirects=False)
    assert response.status_code == 403
    assert await test_db.scalar(select(GitHubInstallation)) is None
    assert await test_db.scalar(select(GitHubUserAuthorization)) is None


async def test_installation_state_cannot_skip_user_authorization(client, test_db, monkeypatch):
    monkeypatch.setattr(git_sync, "GitHubAppClient", FakeGitHubClient)
    state = await _create_setup_state(client)
    response = await client.get(f"/api/github/setup?code=valid-code&state={state}", follow_redirects=False)
    assert response.status_code == 400
    assert await test_db.scalar(select(GitHubInstallation)) is None


async def test_oauth_state_cannot_be_replayed_or_used_by_another_user(client, monkeypatch):
    monkeypatch.setattr(git_sync, "GitHubAppClient", FakeGitHubClient)
    used = await _connect(client)
    assert (await client.get(f"/api/github/setup?code=valid-code&state={used}")).status_code == 400
    pending = await _oauth_state(client)
    await client.post("/api/auth/logout")
    await client.post("/api/auth/register", json={"email": "other-github@example.com", "password": "password123"})
    assert (await client.get(f"/api/github/setup?code=valid-code&state={pending}")).status_code == 403


async def test_existing_unverified_binding_cannot_list_or_write_repositories(client, test_db, monkeypatch):
    fake = FakeGitHubClient()
    monkeypatch.setattr(git_sync, "GitHubAppClient", lambda: fake)
    project_id, _ = await create_project_with_document(client, "旧绑定")
    user_id = (await client.get("/api/auth/me")).json()["id"]
    test_db.add(GitHubInstallation(user_id=user_id, installation_id=123))
    test_db.add(ProjectGitSync(
        project_id=project_id, user_id=user_id, installation_id=123,
        repository_owner="author", repository_name="pawtale-backup",
    ))
    await test_db.commit()
    status = (await client.get(f"/api/projects/{project_id}/git-sync")).json()
    assert status["installations"] == status["repositories"] == []
    assert (await client.post(f"/api/projects/{project_id}/git-sync/run")).status_code == 403
    assert fake.commits == []


async def test_removed_repository_access_blocks_saved_sync_config(client, monkeypatch):
    fake = FakeGitHubClient()
    monkeypatch.setattr(git_sync, "GitHubAppClient", lambda: fake)
    project_id, _ = await create_project_with_document(client, "撤销授权")
    await _connect(client)
    config = {
        "installation_id": 123, "repository_owner": "author", "repository_name": "pawtale-backup",
        "branch": "main", "base_path": "",
    }
    assert (await client.put(f"/api/projects/{project_id}/git-sync", json=config)).status_code == 200

    async def no_access(*args, **kwargs):
        return []

    fake.list_repositories = no_access
    assert (await client.put(f"/api/projects/{project_id}/git-sync", json=config)).status_code == 403
    assert (await client.post(f"/api/projects/{project_id}/git-sync/run")).status_code == 403
    assert fake.commits == []


async def test_user_token_refresh_is_encrypted_and_serialized(client, test_db):
    user_id = (await client.get("/api/auth/me")).json()["id"]
    row = GitHubUserAuthorization(user_id=user_id, github_user_id=42)
    save_tokens(row, UserTokens(access_token="old-access", refresh_token="old-refresh", expires_in=1))
    row.expires_at = utcnow_naive() - timedelta(seconds=1)
    test_db.add(row)
    await test_db.commit()
    requests = []

    async def refresh(**grant):
        requests.append(grant)
        return UserTokens(access_token="new-access", refresh_token="new-refresh", expires_in=3600)

    fake = FakeGitHubClient()
    fake.exchange_token = refresh
    sessions = async_sessionmaker(test_db.bind, expire_on_commit=False)

    async def token():
        async with sessions() as db:
            return await user_access_token(db, user_id, fake)

    assert await asyncio.gather(token(), token()) == ["new-access", "new-access"]
    assert requests == [{"grant_type": "refresh_token", "refresh_token": "old-refresh"}]
    await test_db.refresh(row)
    assert "new-access" not in row.access_token_encrypted
    assert "new-refresh" not in row.refresh_token_encrypted


async def test_expired_refresh_requires_reconnection(client, test_db):
    user_id = (await client.get("/api/auth/me")).json()["id"]
    row = GitHubUserAuthorization(user_id=user_id, github_user_id=42)
    save_tokens(row, UserTokens(access_token="old-access", refresh_token="old-refresh"))
    row.expires_at = row.refresh_expires_at = utcnow_naive() - timedelta(seconds=1)
    test_db.add(row)
    await test_db.commit()
    with pytest.raises(GitHubSyncError, match="重新连接"):
        await user_access_token(test_db, user_id, FakeGitHubClient())


@pytest.mark.parametrize("extra_code", ["", "&code=unsolicited-install-code"])
async def test_authorization_uses_pkce_bound_to_callback_state(client, test_db, monkeypatch, extra_code):
    from urllib.parse import parse_qs, urlparse

    fake = FakeGitHubClient()
    monkeypatch.setattr(git_sync, "GitHubAppClient", lambda: fake)
    state = await _create_setup_state(client)
    response = await client.get(f"/api/github/setup?installation_id=123&state={state}{extra_code}", follow_redirects=False)
    params = parse_qs(urlparse(response.headers["location"]).query)
    row = await test_db.get(GitHubSetupState, params["state"][0])
    expected = base64.urlsafe_b64encode(hashlib.sha256(row.code_verifier.encode()).digest()).decode().rstrip("=")
    assert params["code_challenge"] == [expected]
    assert row.installation_id == 123


async def test_revoked_authorization_keeps_reconnect_available(client, monkeypatch):
    fake = FakeGitHubClient()
    monkeypatch.setattr(git_sync, "GitHubAppClient", lambda: fake)
    project_id, _ = await create_project_with_document(client, "重新授权")
    await _connect(client)

    async def revoked(*args, **kwargs):
        raise GitHubSyncError("GitHub 用户授权已失效，请重新连接")

    fake.list_repositories = revoked
    response = await client.get(f"/api/projects/{project_id}/git-sync")
    assert response.status_code == 200
    assert response.json()["configured"] is True
    assert response.json()["authorization_error"] == "GitHub 用户授权已失效，请重新连接"
    assert response.json()["repositories"] == []
