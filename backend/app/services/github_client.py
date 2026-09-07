from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.config import get_settings


class GitHubSyncError(RuntimeError):
    pass


@dataclass(frozen=True)
class BackupFile:
    path: str
    content: str


class UserTokens(BaseModel):
    access_token: str = Field(min_length=1, repr=False)
    refresh_token: str | None = Field(default=None, min_length=1, repr=False)
    expires_in: int | None = Field(default=None, gt=0)
    refresh_token_expires_in: int | None = Field(default=None, gt=0)


class GitHubAppClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    @property
    def configured(self) -> bool:
        return bool(
            self.settings.github_app_slug.strip()
            and self.settings.github_app_client_id.strip()
            and self.settings.github_app_client_secret.strip()
            and self.settings.github_app_callback_url.strip()
        )

    @property
    def install_url(self) -> str | None:
        slug = self.settings.github_app_slug.strip()
        return f"{self.settings.github_web_base_url.rstrip('/')}/apps/{slug}/installations/new" if slug else None

    def authorize_url(self, *, state: str, challenge: str) -> str:
        params = urlencode({
            "client_id": self.settings.github_app_client_id,
            "redirect_uri": self.settings.github_app_callback_url,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        })
        return f"{self.settings.github_web_base_url.rstrip('/')}/login/oauth/authorize?{params}"

    async def exchange_token(self, **grant: str) -> UserTokens:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{self.settings.github_web_base_url.rstrip('/')}/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={
                    "client_id": self.settings.github_app_client_id,
                    "client_secret": self.settings.github_app_client_secret,
                    **grant,
                },
            )
        if response.status_code != 200:
            raise GitHubSyncError("GitHub 用户授权失败，请重新连接")
        try:
            return UserTokens.model_validate_json(response.content)
        except ValidationError:
            raise GitHubSyncError("GitHub 用户授权失败，请重新连接") from None

    async def exchange_code(self, code: str, verifier: str) -> UserTokens:
        return await self.exchange_token(
            code=code,
            code_verifier=verifier,
            redirect_uri=self.settings.github_app_callback_url,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        token: str,
        json_body: dict[str, Any] | None = None,
        expected_status: set[int] | None = None,
    ) -> dict[str, Any] | None:
        expected = expected_status or {200, 201}
        url = f"{self.settings.github_api_base_url.rstrip('/')}{path}"
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.request(method, url, headers=headers, json=json_body)
        if response.status_code == 401:
            raise GitHubSyncError("GitHub 用户授权已失效，请重新连接")
        if response.status_code == 404 and 404 in expected:
            return None
        if response.status_code not in expected:
            detail = response.text[:300] or response.reason_phrase
            raise GitHubSyncError(f"GitHub API 请求失败：{response.status_code} {detail}")
        if not response.content:
            return None
        return response.json()

    async def get_user(self, token: str) -> dict[str, Any]:
        return await self._request("GET", "/user", token=token) or {}

    async def _list(self, path: str, key: str, token: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page = 1
        while True:
            data = await self._request(
                "GET", f"{path}?{urlencode({'per_page': 100, 'page': page})}", token=token,
            )
            batch = (data or {}).get(key, [])
            if not isinstance(batch, list):
                raise GitHubSyncError("GitHub 授权列表格式异常")
            items.extend(item for item in batch if isinstance(item, dict))
            if len(batch) < 100:
                return items
            page += 1

    async def list_installations(self, token: str) -> list[dict[str, Any]]:
        return await self._list("/user/installations", "installations", token)

    async def list_repositories(self, installation_id: int, *, token: str) -> list[dict[str, Any]]:
        return await self._list(f"/user/installations/{installation_id}/repositories", "repositories", token)

    async def commit_files(
        self,
        *,
        token: str,
        owner: str,
        repo: str,
        branch: str,
        files: list[BackupFile],
        message: str,
    ) -> str:
        if not files:
            raise GitHubSyncError("GitHub 同步文件为空")

        repo_path = f"/repos/{quote(owner, safe='')}/{quote(repo, safe='')}"
        ref_path = quote(f"heads/{branch}", safe="/")
        ref_data = await self._request(
            "GET",
            f"{repo_path}/git/ref/{ref_path}",
            token=token,
        )
        ref_object = (ref_data or {}).get("object")
        base_commit_sha = ref_object.get("sha") if isinstance(ref_object, dict) else None
        if not isinstance(base_commit_sha, str) or not base_commit_sha:
            raise GitHubSyncError("GitHub branch ref 缺少 commit sha")

        commit_data = await self._request(
            "GET",
            f"{repo_path}/git/commits/{quote(base_commit_sha, safe='')}",
            token=token,
        )
        commit_tree = (commit_data or {}).get("tree")
        base_tree_sha = commit_tree.get("sha") if isinstance(commit_tree, dict) else None
        if not isinstance(base_tree_sha, str) or not base_tree_sha:
            raise GitHubSyncError("GitHub base commit 缺少 tree sha")

        tree_entries: list[dict[str, str]] = []
        for file in files:
            blob_data = await self._request(
                "POST",
                f"{repo_path}/git/blobs",
                token=token,
                json_body={"content": file.content, "encoding": "utf-8"},
                expected_status={201},
            )
            blob_sha = (blob_data or {}).get("sha")
            if not isinstance(blob_sha, str) or not blob_sha:
                raise GitHubSyncError(f"GitHub blob 创建失败：{file.path}")
            tree_entries.append(
                {
                    "path": file.path,
                    "mode": "100644",
                    "type": "blob",
                    "sha": blob_sha,
                }
            )

        tree_data = await self._request(
            "POST",
            f"{repo_path}/git/trees",
            token=token,
            json_body={"base_tree": base_tree_sha, "tree": tree_entries},
            expected_status={201},
        )
        tree_sha = (tree_data or {}).get("sha")
        if not isinstance(tree_sha, str) or not tree_sha:
            raise GitHubSyncError("GitHub tree 创建失败")

        new_commit_data = await self._request(
            "POST",
            f"{repo_path}/git/commits",
            token=token,
            json_body={"message": message, "tree": tree_sha, "parents": [base_commit_sha]},
            expected_status={201},
        )
        new_commit_sha = (new_commit_data or {}).get("sha")
        if not isinstance(new_commit_sha, str) or not new_commit_sha:
            raise GitHubSyncError("GitHub commit 创建失败")

        await self._request(
            "PATCH",
            f"{repo_path}/git/refs/{ref_path}",
            token=token,
            json_body={"sha": new_commit_sha, "force": False},
            expected_status={200},
        )
        return new_commit_sha
