from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta

from fastapi import Cookie, Depends, HTTPException, Request, Response, status
from sqlalchemy import case, delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings, is_production_environment
from app.database import get_db
from app.clock import utcnow_naive
from app.models.users import AuthLoginAttempt, AuthSession, User
from app.services.ai_access import set_request_ai_model_scope
from app.services.ai_usage_records import set_ai_usage_context


SESSION_COOKIE_NAME = "pawtale_session"
PASSWORD_ITERATIONS = 390_000
ADMIN_ROLE = "admin"
SESSION_LIFETIME = timedelta(days=30)
WECHAT_LOGIN_WINDOW = timedelta(minutes=15)
WECHAT_LOGIN_ATTEMPT_LIMIT = 120
# The name a WeChat account carries until its owner picks one.
WECHAT_PLACEHOLDER_DISPLAY_NAME = "微信用户"


def normalize_email(email: str) -> str:
    return email.strip().lower()


def is_admin_user(user: User) -> bool:
    return user.role == ADMIN_ROLE


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PASSWORD_ITERATIONS,
    ).hex()
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt}${derived}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, iterations_raw, salt, expected = password_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_raw)
    except ValueError:
        return False

    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    ).hex()
    return hmac.compare_digest(derived, expected)


def session_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def session_user_id(db: AsyncSession, token: str) -> str | None:
    if not token or len(token) > 128:
        return None
    session = await db.get(AuthSession, session_token_hash(token))
    if not session or session.expires_at <= utcnow_naive():
        return None
    return session.user_id


async def issue_session(db: AsyncSession, response: Response, user_id: str) -> None:
    token = secrets.token_urlsafe(32)
    await db.execute(delete(AuthSession).where(AuthSession.expires_at <= utcnow_naive()))
    db.add(AuthSession(
        token_hash=session_token_hash(token),
        user_id=user_id,
        expires_at=utcnow_naive() + SESSION_LIFETIME,
    ))
    await db.commit()
    set_session_cookie(response, token)


async def check_wechat_login_rate_limit(db: AsyncSession, client_ip: str) -> None:
    key_hash = hmac.new(
        get_settings().app_secret_key.encode("utf-8"),
        f"wechat-login:{client_ip}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    now = utcnow_naive()
    expired_window = AuthLoginAttempt.window_start <= now - WECHAT_LOGIN_WINDOW
    statement = insert(AuthLoginAttempt).values(key_hash=key_hash, window_start=now, attempts=1)
    statement = statement.on_conflict_do_update(
        index_elements=[AuthLoginAttempt.key_hash],
        set_={
            "window_start": case((expired_window, now), else_=AuthLoginAttempt.window_start),
            "attempts": case((expired_window, 1), else_=AuthLoginAttempt.attempts + 1),
        },
    ).returning(AuthLoginAttempt.attempts)
    attempts = await db.scalar(statement)
    if secrets.randbelow(100) == 0:
        await db.execute(
            delete(AuthLoginAttempt).where(AuthLoginAttempt.window_start < now - timedelta(days=7))
        )
    await db.commit()
    if attempts is not None and attempts > WECHAT_LOGIN_ATTEMPT_LIMIT:
        raise HTTPException(status_code=429, detail="登录尝试过于频繁，请稍后再试")


async def revoke_session(db: AsyncSession, token: str | None) -> None:
    if token and len(token) <= 128:
        await db.execute(delete(AuthSession).where(AuthSession.token_hash == session_token_hash(token)))
        await db.commit()


def set_session_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=int(SESSION_LIFETIME.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=is_production_environment(settings),
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=SESSION_COOKIE_NAME, httponly=True, samesite="lax")


async def get_current_user(
    request: Request,
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not session_cookie:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录")
    user_id = await session_user_id(db, session_cookie)
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录状态已失效")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")
    set_request_ai_model_scope(is_admin=is_admin_user(user))
    set_ai_usage_context(
        user_id=user.id,
        operation=request.url.path,
        project_id=request.path_params.get("project_id"),
    )
    return user


async def get_optional_user_id(
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    db: AsyncSession = Depends(get_db),
) -> str | None:
    """The signed-in user on public pages, which also serve visitors."""
    return await session_user_id(db, session_cookie) if session_cookie else None


async def get_current_admin_user(current_user: User = Depends(get_current_user)) -> User:
    if not is_admin_user(current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return current_user
