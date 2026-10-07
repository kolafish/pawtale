from __future__ import annotations

import base64
import hashlib
from datetime import timedelta

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow_naive
from app.config import get_settings
from app.models.git_sync import GitHubUserAuthorization
from app.services.github_client import GitHubAppClient, GitHubSyncError, UserTokens


def _cipher() -> Fernet:
    secret = get_settings().app_secret_key.encode()
    key = hashlib.sha256(b"pawtale.github.user-token\0" + secret).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def _encrypt(token: str) -> str:
    return _cipher().encrypt(token.encode()).decode()


def _decrypt(token: str) -> str:
    try:
        return _cipher().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise GitHubSyncError("GitHub 用户授权无法解密，请重新连接") from exc


def save_tokens(row: GitHubUserAuthorization, data: UserTokens) -> None:
    now = utcnow_naive()
    row.access_token_encrypted = _encrypt(data.access_token)
    refresh = data.refresh_token
    row.refresh_token_encrypted = _encrypt(refresh) if refresh else None
    row.expires_at = now + timedelta(seconds=data.expires_in) if data.expires_in else None
    row.refresh_expires_at = (
        now + timedelta(seconds=data.refresh_token_expires_in)
        if data.refresh_token_expires_in else None
    )


async def user_access_token(db: AsyncSession, user_id: str, github: GitHubAppClient) -> str:
    # Refresh tokens rotate. Serialize refreshes across requests and workers.
    row = await db.scalar(
        select(GitHubUserAuthorization)
        .where(GitHubUserAuthorization.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise HTTPException(status_code=403, detail="请重新连接 GitHub 并授权用户访问")
    if row.expires_at and row.expires_at <= utcnow_naive() + timedelta(minutes=1):
        if not row.refresh_token_encrypted or (
            row.refresh_expires_at and row.refresh_expires_at <= utcnow_naive()
        ):
            raise GitHubSyncError("GitHub 用户授权已过期，请重新连接")
        data = await github.exchange_token(
            grant_type="refresh_token", refresh_token=_decrypt(row.refresh_token_encrypted),
        )
        save_tokens(row, data)
    token = _decrypt(row.access_token_encrypted)
    await db.commit()
    return token
