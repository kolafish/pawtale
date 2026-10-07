from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.config import Settings
from app.services import github_client
from app.services.github_client import GitHubAppClient, GitHubSyncError


@pytest.fixture
def github(monkeypatch):
    settings = Settings(
        _env_file=None, github_app_slug="pawtale", github_app_client_id="client-id",
        github_app_client_secret="client-secret",
        github_app_callback_url="https://example.test/api/github/setup",
    )
    monkeypatch.setattr(github_client, "get_settings", lambda: settings)
    return GitHubAppClient()


def _transport(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(github_client.httpx, "AsyncClient", lambda **kwargs: original(
        **kwargs, transport=httpx.MockTransport(handler),
    ))


async def test_oauth_code_exchange_keeps_secret_and_verifier_on_server(github, monkeypatch):
    url = github.authorize_url(state="nonce", challenge="challenge")
    assert parse_qs(urlparse(url).query) == {
        "client_id": ["client-id"], "redirect_uri": ["https://example.test/api/github/setup"],
        "state": ["nonce"], "code_challenge": ["challenge"], "code_challenge_method": ["S256"],
    }

    def handler(request):
        assert str(request.url) == "https://github.com/login/oauth/access_token"
        assert request.method == "POST"
        assert parse_qs(request.content.decode()) == {
            "client_id": ["client-id"], "client_secret": ["client-secret"],
            "redirect_uri": ["https://example.test/api/github/setup"],
            "code": ["callback-code"], "code_verifier": ["verifier"],
        }
        return httpx.Response(200, json={
            "access_token": "secret-access", "refresh_token": "secret-refresh",
            "expires_in": 28800, "refresh_token_expires_in": 15897600,
        })

    _transport(monkeypatch, handler)
    tokens = await github.exchange_code("callback-code", "verifier")
    assert tokens.access_token == "secret-access"
    assert tokens.expires_in == 28800
    assert "secret-access" not in repr(tokens)
    assert "secret-refresh" not in repr(tokens)


@pytest.mark.parametrize("payload", [
    {"error": "bad_verification_code", "error_description": "private provider detail"},
    {"access_token": "secret-access", "expires_in": "invalid"},
])
async def test_invalid_oauth_response_exposes_no_provider_secrets(github, monkeypatch, payload):
    _transport(monkeypatch, lambda request: httpx.Response(200, json=payload))
    with pytest.raises(GitHubSyncError) as error:
        await github.exchange_code("code", "verifier")
    assert str(error.value) == "GitHub 用户授权失败，请重新连接"


async def test_repository_list_uses_user_access_and_paginates(github, monkeypatch):
    pages = []

    def handler(request):
        assert request.headers["Authorization"] == "Bearer user-token"
        assert request.url.path == "/user/installations/123/repositories"
        pages.append(request.url.params["page"])
        count = 100 if len(pages) == 1 else 1
        return httpx.Response(200, json={"repositories": [{"id": i} for i in range(count)]})

    _transport(monkeypatch, handler)
    assert len(await github.list_repositories(123, token="user-token")) == 101
    assert pages == ["1", "2"]


async def test_revoked_user_token_is_not_replaced_with_app_credentials(github, monkeypatch):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        assert request.headers["Authorization"] == "Bearer revoked-token"
        return httpx.Response(401, json={"message": "Bad credentials"})

    _transport(monkeypatch, handler)
    with pytest.raises(GitHubSyncError, match="重新连接"):
        await github.list_repositories(123, token="revoked-token")
    assert calls == ["/user/installations/123/repositories"]
