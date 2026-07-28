from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import re
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from fastapi import HTTPException
import httpx
import jwt
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow_naive
from app.config import get_settings
from app.models.git_sync import GitHubInstallation, GitHubSetupState, ProjectGitSync
from app.schemas.projects import (
    GitHubSetupUrlResponse,
    GitSyncInstallationResponse,
    GitSyncRepositoryOption,
    GitSyncStatusResponse,
    ProjectGitSyncConfigRequest,
    ProjectGitSyncConfigResponse,
    ProjectGitSyncRunResponse,
)
from app.services.content_storage import read_content
from app.services.projects.access import load_project


SAFE_PATH_PART_RE = re.compile(r"[^a-zA-Z0-9._-]+")
GITHUB_SETUP_STATE_TTL_MINUTES = 10
GITHUB_INSTALLATION_TOKEN_REFRESH_BUFFER = timedelta(minutes=5)


class GitHubSyncError(RuntimeError):
    pass


@dataclass
class BackupFile:
    path: str
    content: str


@dataclass(frozen=True)
class InstallationToken:
    token: str
    expires_at: datetime


class GitHubAppClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._installation_tokens: dict[int, InstallationToken] = {}

    @property
    def configured(self) -> bool:
        try:
            private_key = self._private_key()
        except OSError:
            private_key = ""
        return bool(self.settings.github_app_id.strip() and self.settings.github_app_slug.strip() and private_key)

    @property
    def install_url(self) -> str | None:
        slug = self.settings.github_app_slug.strip()
        if not slug:
            return None
        return f"{self.settings.github_web_base_url.rstrip('/')}/apps/{slug}/installations/new"

    def _private_key(self) -> str:
        inline = self.settings.github_app_private_key.strip()
        if inline:
            return inline.replace("\\n", "\n")
        key_path = self.settings.github_app_private_key_path.strip()
        if not key_path:
            return ""
        return Path(key_path).expanduser().read_text(encoding="utf-8")

    def _app_jwt(self) -> str:
        if not self.configured:
            raise GitHubSyncError("GitHub App 未配置")
        now = int(time.time())
        payload = {
            "iat": now - 60,
            "exp": now + int(timedelta(minutes=9).total_seconds()),
            "iss": self.settings.github_app_id.strip(),
        }
        try:
            return jwt.encode(payload, self._private_key(), algorithm="RS256")
        except OSError as exc:
            raise GitHubSyncError(f"GitHub App private key 读取失败：{exc}") from exc

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
        if response.status_code == 404 and 404 in expected:
            return None
        if response.status_code not in expected:
            detail = response.text[:300] or response.reason_phrase
            raise GitHubSyncError(f"GitHub API 请求失败：{response.status_code} {detail}")
        if not response.content:
            return None
        return response.json()

    async def get_installation(self, installation_id: int) -> dict[str, Any]:
        data = await self._request(
            "GET",
            f"/app/installations/{installation_id}",
            token=self._app_jwt(),
        )
        return data or {}

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _parse_expires_at(value: Any) -> datetime | None:
        if not isinstance(value, str) or not value.strip():
            return None
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = f"{normalized[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    async def create_installation_token(self, installation_id: int) -> str:
        cached = self._installation_tokens.get(installation_id)
        if cached and cached.expires_at - self._now() > GITHUB_INSTALLATION_TOKEN_REFRESH_BUFFER:
            return cached.token
        self._installation_tokens.pop(installation_id, None)

        data = await self._request(
            "POST",
            f"/app/installations/{installation_id}/access_tokens",
            token=self._app_jwt(),
        )
        token = (data or {}).get("token")
        if not isinstance(token, str) or not token:
            raise GitHubSyncError("GitHub installation token 为空")
        expires_at = self._parse_expires_at((data or {}).get("expires_at"))
        if expires_at and expires_at - self._now() > GITHUB_INSTALLATION_TOKEN_REFRESH_BUFFER:
            self._installation_tokens[installation_id] = InstallationToken(token=token, expires_at=expires_at)
        return token

    async def list_repositories(self, installation_id: int) -> list[dict[str, Any]]:
        token = await self.create_installation_token(installation_id)
        repositories: list[dict[str, Any]] = []
        page = 1
        while True:
            data = await self._request(
                "GET",
                f"/installation/repositories?{urlencode({'per_page': 100, 'page': page})}",
                token=token,
            )
            repos = (data or {}).get("repositories", [])
            if not isinstance(repos, list) or not repos:
                break
            repositories.extend(repo for repo in repos if isinstance(repo, dict))
            if len(repos) < 100:
                break
            page += 1
        return repositories

    async def commit_files(
        self,
        *,
        installation_id: int,
        owner: str,
        repo: str,
        branch: str,
        files: list[BackupFile],
        message: str,
    ) -> str:
        if not files:
            raise GitHubSyncError("GitHub 同步文件为空")

        token = await self.create_installation_token(installation_id)
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


def _repo_option(installation_id: int, repo: dict[str, Any]) -> GitSyncRepositoryOption:
    owner = repo.get("owner") if isinstance(repo.get("owner"), dict) else {}
    owner_login = owner.get("login") if isinstance(owner, dict) else None
    full_name = repo.get("full_name") or ""
    repo_owner, _, repo_name = full_name.partition("/")
    return GitSyncRepositoryOption(
        installation_id=installation_id,
        owner=str(owner_login or repo_owner),
        name=str(repo.get("name") or repo_name),
        full_name=str(full_name),
        private=bool(repo.get("private", True)),
        default_branch=str(repo.get("default_branch") or "main"),
    )


def _installation_response(row: GitHubInstallation) -> GitSyncInstallationResponse:
    return GitSyncInstallationResponse(
        installation_id=row.installation_id,
        account_login=row.account_login,
        account_type=row.account_type,
        repository_selection=row.repository_selection,
    )


def _config_response(row: ProjectGitSync) -> ProjectGitSyncConfigResponse:
    return ProjectGitSyncConfigResponse.model_validate(row)


def _with_query_param(url: str, params: dict[str, str]) -> str:
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{urlencode(params)}"


def _safe_part(value: str) -> str:
    cleaned = SAFE_PATH_PART_RE.sub("-", value.strip()).strip(".-")
    return cleaned or "untitled"


def _join_path(*parts: str) -> str:
    return "/".join(part.strip("/") for part in parts if part and part.strip("/"))


async def _backup_files(db: AsyncSession, project_id: str, user_id: str, base_path: str) -> list[BackupFile]:
    project = await load_project(db, project_id, user_id)
    files: list[BackupFile] = []
    manifest = {
        "format": "pawtale-project-backup-v1",
        "project": {
            "id": project.id,
            "type": project.type,
            "title": project.title,
            "description": project.description,
            "word_count_target": project.word_count_target,
            "daily_word_goal": project.daily_word_goal,
            "updated_at": project.updated_at.isoformat() if project.updated_at else None,
        },
        "groups": [
            {
                "id": group.id,
                "name": group.name,
                "sort_order": group.sort_order,
                "documents": [
                    {
                        "id": doc.id,
                        "name": doc.name,
                        "status": doc.status,
                        "label": doc.label,
                        "synopsis": doc.synopsis,
                        "word_count": doc.word_count,
                        "sort_order": doc.sort_order,
                    }
                    for doc in group.documents
                ],
            }
            for group in project.groups
        ],
        "references": [
            {
                "id": ref.id,
                "type": ref.type,
                "name": ref.name,
                "sort_order": ref.sort_order,
                "metadata_json": ref.metadata_json,
            }
            for ref in project.references
        ],
    }
    files.append(
        BackupFile(
            path=_join_path(base_path, "pawtale-project.json"),
            content=json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        )
    )

    for group_index, group in enumerate(project.groups, start=1):
        group_part = f"{group_index:03d}-{_safe_part(group.name)}"
        for doc_index, doc in enumerate(group.documents, start=1):
            content = await read_content(project.id, doc.id)
            doc_part = f"{doc_index:03d}-{_safe_part(doc.name)}-{doc.id}.md"
            files.append(
                BackupFile(
                    path=_join_path(base_path, "documents", group_part, doc_part),
                    content=content,
                )
            )

    for ref_index, ref in enumerate(project.references, start=1):
        payload = {
            "id": ref.id,
            "type": ref.type,
            "name": ref.name,
            "content": ref.content,
            "metadata_json": ref.metadata_json,
            "sort_order": ref.sort_order,
            "updated_at": ref.updated_at.isoformat() if ref.updated_at else None,
        }
        files.append(
            BackupFile(
                path=_join_path(base_path, "references", f"{ref_index:03d}-{_safe_part(ref.name)}-{ref.id}.json"),
                content=json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            )
        )

    return files


async def _cleanup_setup_states(db: AsyncSession, now: datetime) -> None:
    await db.execute(
        delete(GitHubSetupState).where(
            GitHubSetupState.expires_at < now - timedelta(hours=1)
        )
    )


async def create_setup_url(
    db: AsyncSession,
    user_id: str,
    *,
    client: GitHubAppClient | None = None,
) -> GitHubSetupUrlResponse:
    github = client or GitHubAppClient()
    if not github.configured or not github.install_url:
        raise HTTPException(status_code=503, detail="GitHub App 未配置")

    now = utcnow_naive()
    expires_at = now + timedelta(minutes=GITHUB_SETUP_STATE_TTL_MINUTES)
    await _cleanup_setup_states(db, now)
    setup_state = GitHubSetupState(
        state=secrets.token_urlsafe(32),
        user_id=user_id,
        created_at=now,
        expires_at=expires_at,
    )
    db.add(setup_state)
    await db.commit()
    return GitHubSetupUrlResponse(
        install_url=_with_query_param(github.install_url, {"state": setup_state.state}),
        expires_at=expires_at,
    )


async def _consume_setup_state(
    db: AsyncSession,
    *,
    user_id: str,
    state: str,
) -> GitHubSetupState:
    value = state.strip()
    if not value:
        raise HTTPException(status_code=400, detail="GitHub setup state 缺失")
    row = await db.get(GitHubSetupState, value)
    if not row:
        raise HTTPException(status_code=400, detail="GitHub setup state 无效")
    if row.user_id != user_id:
        raise HTTPException(status_code=403, detail="GitHub setup state 不属于当前用户")
    if row.used_at is not None:
        raise HTTPException(status_code=400, detail="GitHub setup state 已使用")
    if row.expires_at <= utcnow_naive():
        raise HTTPException(status_code=400, detail="GitHub setup state 已过期")
    return row


async def record_installation(
    db: AsyncSession,
    user_id: str,
    installation_id: int,
    *,
    state: str,
    client: GitHubAppClient | None = None,
) -> GitSyncInstallationResponse:
    github = client or GitHubAppClient()
    if not github.configured:
        raise HTTPException(status_code=503, detail="GitHub App 未配置")
    setup_state = await _consume_setup_state(db, user_id=user_id, state=state)

    try:
        installation = await github.get_installation(installation_id)
    except GitHubSyncError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    account = installation.get("account") if isinstance(installation.get("account"), dict) else {}
    result = await db.execute(
        select(GitHubInstallation).where(
            GitHubInstallation.user_id == user_id,
            GitHubInstallation.installation_id == installation_id,
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        row = GitHubInstallation(user_id=user_id, installation_id=installation_id)
        db.add(row)
    row.account_login = account.get("login") if isinstance(account.get("login"), str) else None
    row.account_type = account.get("type") if isinstance(account.get("type"), str) else None
    row.repository_selection = (
        installation.get("repository_selection") if isinstance(installation.get("repository_selection"), str) else None
    )
    setup_state.used_at = utcnow_naive()
    await db.commit()
    await db.refresh(row)
    return _installation_response(row)


async def get_status(
    db: AsyncSession,
    project_id: str,
    user_id: str,
    *,
    client: GitHubAppClient | None = None,
) -> GitSyncStatusResponse:
    await load_project(db, project_id, user_id)
    github = client or GitHubAppClient()
    installs_result = await db.execute(
        select(GitHubInstallation)
        .where(GitHubInstallation.user_id == user_id)
        .order_by(GitHubInstallation.updated_at.desc())
    )
    installations = installs_result.scalars().all()

    config_result = await db.execute(
        select(ProjectGitSync).where(ProjectGitSync.project_id == project_id, ProjectGitSync.user_id == user_id)
    )
    config = config_result.scalar_one_or_none()

    repositories: list[GitSyncRepositoryOption] = []
    if github.configured:
        for installation in installations:
            try:
                repos = await github.list_repositories(installation.installation_id)
            except GitHubSyncError:
                continue
            repositories.extend(_repo_option(installation.installation_id, repo) for repo in repos)

    return GitSyncStatusResponse(
        configured=github.configured,
        install_url=github.install_url,
        installations=[_installation_response(row) for row in installations],
        repositories=repositories,
        project_config=_config_response(config) if config else None,
    )


async def save_config(
    db: AsyncSession,
    project_id: str,
    user_id: str,
    req: ProjectGitSyncConfigRequest,
    *,
    client: GitHubAppClient | None = None,
) -> ProjectGitSyncConfigResponse:
    await load_project(db, project_id, user_id)
    github = client or GitHubAppClient()
    if not github.configured:
        raise HTTPException(status_code=503, detail="GitHub App 未配置")

    install_result = await db.execute(
        select(GitHubInstallation).where(
            GitHubInstallation.user_id == user_id,
            GitHubInstallation.installation_id == req.installation_id,
        )
    )
    if not install_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="GitHub installation 未连接")

    try:
        repos = await github.list_repositories(req.installation_id)
    except GitHubSyncError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    allowed = {
        (option.owner.lower(), option.name.lower())
        for option in (_repo_option(req.installation_id, repo) for repo in repos)
    }
    if (req.repository_owner.lower(), req.repository_name.lower()) not in allowed:
        raise HTTPException(status_code=403, detail="该 GitHub App 未授权目标仓库")

    result = await db.execute(
        select(ProjectGitSync).where(ProjectGitSync.project_id == project_id, ProjectGitSync.user_id == user_id)
    )
    row = result.scalar_one_or_none()
    if not row:
        row = ProjectGitSync(project_id=project_id, user_id=user_id)
        db.add(row)
    row.installation_id = req.installation_id
    row.repository_owner = req.repository_owner
    row.repository_name = req.repository_name
    row.branch = req.branch
    row.base_path = req.base_path
    row.last_error = None
    await db.commit()
    await db.refresh(row)
    return _config_response(row)


async def delete_config(db: AsyncSession, project_id: str, user_id: str) -> None:
    result = await db.execute(
        select(ProjectGitSync).where(ProjectGitSync.project_id == project_id, ProjectGitSync.user_id == user_id)
    )
    row = result.scalar_one_or_none()
    if row:
        await db.delete(row)
        await db.commit()


async def run_sync(
    db: AsyncSession,
    project_id: str,
    user_id: str,
    *,
    client: GitHubAppClient | None = None,
) -> ProjectGitSyncRunResponse:
    await load_project(db, project_id, user_id)
    result = await db.execute(
        select(ProjectGitSync).where(ProjectGitSync.project_id == project_id, ProjectGitSync.user_id == user_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="尚未配置 GitHub 同步")

    github = client or GitHubAppClient()
    if not github.configured:
        raise HTTPException(status_code=503, detail="GitHub App 未配置")

    synced_at = utcnow_naive()
    try:
        files = await _backup_files(db, project_id, user_id, config.base_path)
        commit_sha = await github.commit_files(
            installation_id=config.installation_id,
            owner=config.repository_owner,
            repo=config.repository_name,
            branch=config.branch,
            files=files,
            message="Sync Pawtale project backup",
        )
        config.last_synced_at = synced_at
        config.last_commit_sha = commit_sha
        config.last_error = None
        await db.commit()
        return ProjectGitSyncRunResponse(
            ok=True,
            synced_files=len(files),
            commit_sha=commit_sha,
            synced_at=synced_at,
        )
    except GitHubSyncError as exc:
        config.last_error = str(exc)
        await db.commit()
        return ProjectGitSyncRunResponse(
            ok=False,
            synced_files=0,
            commit_sha=None,
            synced_at=synced_at,
            error=str(exc),
        )
