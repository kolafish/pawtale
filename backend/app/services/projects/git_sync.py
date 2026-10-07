from __future__ import annotations

from datetime import datetime, timedelta
import base64
import hashlib
import json
import re
import secrets
from typing import Any
from urllib.parse import urlencode

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.clock import utcnow_naive
from app.models.git_sync import GitHubInstallation, GitHubSetupState, GitHubUserAuthorization, ProjectGitSync
from app.schemas.projects import (
    GitHubSetupUrlResponse,
    GitSyncInstallationResponse,
    GitSyncRepositoryOption,
    GitSyncStatusResponse,
    ProjectGitSyncConfigRequest,
    ProjectGitSyncConfigResponse,
    ProjectGitSyncRunResponse,
)
from app.services.github_client import BackupFile, GitHubAppClient, GitHubSyncError
from app.services.content_storage import read_content
from app.services.projects.access import load_project
from app.services.projects.github_authorization import save_tokens, user_access_token


SAFE_PATH_PART_RE = re.compile(r"[^a-zA-Z0-9._-]+")
GITHUB_SETUP_STATE_TTL_MINUTES = 10


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
    row = await db.scalar(
        select(GitHubSetupState).where(GitHubSetupState.state == value)
        .with_for_update().execution_options(populate_existing=True)
    )
    if not row:
        raise HTTPException(status_code=400, detail="GitHub setup state 无效")
    if row.user_id != user_id:
        raise HTTPException(status_code=403, detail="GitHub setup state 不属于当前用户")
    if row.used_at is not None:
        raise HTTPException(status_code=400, detail="GitHub setup state 已使用")
    if row.expires_at <= utcnow_naive():
        raise HTTPException(status_code=400, detail="GitHub setup state 已过期")
    row.used_at = utcnow_naive()
    return row


async def handle_setup_callback(
    db: AsyncSession, user_id: str, *, state: str,
    installation_id: int | None = None, code: str | None = None,
    client: GitHubAppClient | None = None,
) -> str:
    github = client or GitHubAppClient()
    if not github.configured:
        raise HTTPException(status_code=503, detail="GitHub App 用户授权未配置")
    setup_state = await _consume_setup_state(db, user_id=user_id, state=state)
    if setup_state.code_verifier is not None:
        if not code or setup_state.installation_id is None:
            raise HTTPException(status_code=400, detail="GitHub 授权步骤无效，请重新连接")
        await _record_installation(db, user_id, setup_state=setup_state, code=code, github=github)
        return _with_query_param(get_settings().github_app_setup_redirect_url, {"github_installation": "connected"})
    if installation_id is None:
        raise HTTPException(status_code=400, detail="GitHub 授权步骤无效，请重新连接")
    # Installation callbacks can include an unsolicited OAuth code. Always start
    # our own PKCE-bound authorization before accepting a user token.
    verifier = secrets.token_urlsafe(48)
    oauth_state = GitHubSetupState(
        state=secrets.token_urlsafe(32), user_id=user_id,
        expires_at=utcnow_naive() + timedelta(minutes=GITHUB_SETUP_STATE_TTL_MINUTES),
        installation_id=installation_id, code_verifier=verifier,
    )
    db.add(oauth_state)
    await db.commit()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return github.authorize_url(state=oauth_state.state, challenge=challenge)


async def _record_installation(
    db: AsyncSession, user_id: str, *, setup_state: GitHubSetupState,
    code: str, github: GitHubAppClient,
) -> None:
    installation_id = setup_state.installation_id

    try:
        tokens = await github.exchange_code(code, setup_state.code_verifier)
        identity = await github.get_user(tokens.access_token)
        installations = await github.list_installations(tokens.access_token)
    except GitHubSyncError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    installation = next((item for item in installations if item.get("id") == installation_id), None)
    if installation is None:
        raise HTTPException(status_code=403, detail="当前 GitHub 用户无权访问该安装")
    github_user_id = identity.get("id")
    if not isinstance(github_user_id, int) or github_user_id <= 0:
        raise HTTPException(status_code=502, detail="GitHub 用户身份无效")
    authorization = await db.scalar(
        select(GitHubUserAuthorization).where(GitHubUserAuthorization.user_id == user_id).with_for_update()
    )
    if authorization is None:
        authorization = GitHubUserAuthorization(user_id=user_id)
        db.add(authorization)
    elif authorization.github_user_id != github_user_id:
        # A different GitHub account must explicitly reconnect its installations.
        await db.execute(delete(GitHubInstallation).where(GitHubInstallation.user_id == user_id))
    authorization.github_user_id = github_user_id
    save_tokens(authorization, tokens)

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
    await db.commit()


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
    authorization_error = None
    authorization = await db.get(GitHubUserAuthorization, user_id)
    if authorization is None:
        if installations:
            authorization_error = "请重新连接 GitHub 并授权用户访问"
        installations = []
    if github.configured and installations:
        try:
            token = await user_access_token(db, user_id, github)
            for installation in installations:
                repos = await github.list_repositories(installation.installation_id, token=token)
                repositories.extend(_repo_option(installation.installation_id, repo) for repo in repos)
        except GitHubSyncError as exc:
            authorization_error = str(exc)

    return GitSyncStatusResponse(
        authorization_error=authorization_error,
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
        token = await user_access_token(db, user_id, github)
        repos = await github.list_repositories(req.installation_id, token=token)
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
        token = await user_access_token(db, user_id, github)
        installation = await db.scalar(select(GitHubInstallation).where(
            GitHubInstallation.user_id == user_id,
            GitHubInstallation.installation_id == config.installation_id,
        ))
        if installation is None:
            raise HTTPException(status_code=403, detail="请重新连接目标 GitHub 安装")
        repositories = await github.list_repositories(config.installation_id, token=token)
        if not any(
            str(repo.get("full_name", "")).lower() == f"{config.repository_owner}/{config.repository_name}".lower()
            for repo in repositories
        ):
            raise HTTPException(status_code=403, detail="当前 GitHub 用户无权访问目标仓库")
        files = await _backup_files(db, project_id, user_id, config.base_path)
        commit_sha = await github.commit_files(
            token=token,
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
