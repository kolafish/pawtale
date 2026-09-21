import hashlib
import re
import secrets

from fastapi import APIRouter, Cookie, Depends, File, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    clear_session_cookie,
    check_wechat_login_rate_limit,
    get_current_user,
    hash_password,
    is_admin_user,
    issue_session,
    normalize_email,
    revoke_session,
    SESSION_COOKIE_NAME,
    verify_password,
)
from app.config import is_production_environment
from app.database import get_db
from app.models.users import AuthIdentity, User
from app.schemas.auth import (
    AuthLoginRequest,
    AuthRegisterRequest,
    AuthResponse,
    AuthSetCredentialsRequest,
    AuthUpdateProfileRequest,
    UserResponse,
    WeChatBindRequest,
    WeChatLoginRequest,
)
from app.services import user_avatars
from app.services.wechat_auth import WeChatAuthError, WeChatSession, exchange_wechat_login_code


router = APIRouter(prefix="/api/auth", tags=["auth"])
WECHAT_PROVIDERS = {
    "laoyu": "wechat_miniprogram",
    "xiaoyu": "wechat_miniprogram_xiaoyu",
}
WECHAT_PLACEHOLDER_DISPLAY_NAME = "微信用户"
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _require_development_account_auth() -> None:
    if is_production_environment():
        raise HTTPException(status_code=404, detail="接口不存在")


def _validate_email(email: str) -> str:
    normalized = normalize_email(email)
    if not EMAIL_PATTERN.fullmatch(normalized):
        raise HTTPException(status_code=400, detail="邮箱格式不正确")
    return normalized


def _validate_password(password: str) -> None:
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="密码至少需要 8 个字符")


def _provisional_wechat_email(provider: str, openid: str) -> str:
    digest = hashlib.sha256(f"{provider}:{openid}".encode("utf-8")).hexdigest()[:24]
    return f"wechat_{digest}@wechat.local"


def _display_name_from_email(email: str) -> str:
    return email.split("@", 1)[0].strip() or "作者"


async def _exchange_wechat_code(code: str, miniprogram: str) -> WeChatSession:
    normalized = code.strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="缺少微信登录 code")
    try:
        return await exchange_wechat_login_code(normalized, miniprogram)
    except WeChatAuthError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


async def _find_wechat_identity(
    db: AsyncSession,
    provider: str,
    openid: str,
) -> AuthIdentity | None:
    return await db.scalar(
        select(AuthIdentity).where(
            AuthIdentity.provider == provider,
            AuthIdentity.provider_user_id == openid,
        )
    )


async def _lock_wechat_identity(db: AsyncSession, provider: str, session: WeChatSession) -> None:
    identity_key = session.unionid or f"{provider}:{session.openid}"
    lock_key = int.from_bytes(
        hashlib.sha256(identity_key.encode("utf-8")).digest()[:8], "big", signed=True
    )
    await db.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key})


async def _wechat_union_user_id(
    db: AsyncSession,
    unionid: str | None,
) -> str | None:
    if not unionid:
        return None
    user_ids = set(
        (
            await db.scalars(
                select(AuthIdentity.user_id).where(
                    AuthIdentity.provider.in_(WECHAT_PROVIDERS.values()),
                    AuthIdentity.provider_union_id == unionid,
                )
            )
        ).all()
    )
    if len(user_ids) > 1:
        raise HTTPException(status_code=409, detail="微信身份关联冲突")
    return next(iter(user_ids), None)


def _auth_response(user: User) -> AuthResponse:
    user_response = UserResponse.model_validate(user)
    if is_admin_user(user):
        user_response.role = "admin"
    return AuthResponse(
        user=user_response,
        needs_credentials=not user.password_login_enabled,
    )


def _user_response(user: User) -> UserResponse:
    response = UserResponse.model_validate(user)
    if is_admin_user(user):
        response.role = "admin"
    return response


@router.post("/register", response_model=AuthResponse, status_code=201)
async def register(
    req: AuthRegisterRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    _require_development_account_auth()
    email = _validate_email(req.email)
    _validate_password(req.password)

    existing = await db.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="邮箱已注册")

    user = User(
        email=email,
        password_hash=hash_password(req.password),
        display_name=(req.display_name or email.split("@", 1)[0]).strip() or "作者",
    )
    db.add(user)
    await db.flush()
    await issue_session(db, response, user.id)
    await db.refresh(user)
    return _auth_response(user)


@router.post("/login", response_model=AuthResponse)
async def login(
    req: AuthLoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    _require_development_account_auth()
    email = normalize_email(req.email)
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if (
        not user
        or not user.password_login_enabled
        or not verify_password(req.password, user.password_hash)
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="邮箱或密码不正确")
    await issue_session(db, response, user.id)
    return _auth_response(user)


@router.post("/wechat-login", response_model=AuthResponse)
async def wechat_login(
    req: WeChatLoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    provider = WECHAT_PROVIDERS[req.miniprogram]
    await check_wechat_login_rate_limit(db, request.client.host if request.client else "unknown")
    session = await _exchange_wechat_code(req.code, req.miniprogram)
    await _lock_wechat_identity(db, provider, session)
    identity = await _find_wechat_identity(db, provider, session.openid)
    union_user_id = await _wechat_union_user_id(db, session.unionid)
    if identity:
        if union_user_id and union_user_id != identity.user_id:
            raise HTTPException(status_code=409, detail="微信身份关联冲突")
        user = await db.get(User, identity.user_id)
        if user is None:
            raise HTTPException(status_code=409, detail="微信身份关联的用户不存在")
        if session.unionid and identity.provider_union_id != session.unionid:
            identity.provider_union_id = session.unionid
    else:
        user = await db.get(User, union_user_id) if union_user_id else None
        if union_user_id and user is None:
            raise HTTPException(status_code=409, detail="微信身份关联的用户不存在")
        if user is None:
            user = User(
                email=_provisional_wechat_email(provider, session.openid),
                password_hash=hash_password(secrets.token_urlsafe(32)),
                password_login_enabled=False,
                display_name=WECHAT_PLACEHOLDER_DISPLAY_NAME,
            )
        identity = AuthIdentity(
            user=user,
            provider=provider,
            provider_user_id=session.openid,
            provider_union_id=session.unionid,
            profile_json={},
        )
        db.add_all([user, identity])

    await db.flush()
    await issue_session(db, response, user.id)
    await db.refresh(user)
    return _auth_response(user)


@router.post("/wechat-bind", response_model=AuthResponse)
async def wechat_bind(
    req: WeChatBindRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_development_account_auth()
    provider = WECHAT_PROVIDERS[req.miniprogram]
    session = await _exchange_wechat_code(req.code, req.miniprogram)
    identity = await _find_wechat_identity(db, provider, session.openid)
    if identity and identity.user_id != current_user.id:
        raise HTTPException(status_code=409, detail="该微信已绑定其他账号")
    union_user_id = await _wechat_union_user_id(db, session.unionid)
    if union_user_id and union_user_id != current_user.id:
        raise HTTPException(status_code=409, detail="该微信已绑定其他账号")
    if identity is None:
        db.add(
            AuthIdentity(
                user_id=current_user.id,
                provider=provider,
                provider_user_id=session.openid,
                provider_union_id=session.unionid,
                profile_json={},
            )
        )
    elif session.unionid and identity.provider_union_id != session.unionid:
        identity.provider_union_id = session.unionid

    await db.commit()
    await db.refresh(current_user)
    return _auth_response(current_user)


@router.post("/set-credentials", response_model=AuthResponse)
async def set_credentials(
    req: AuthSetCredentialsRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_development_account_auth()
    if current_user.password_login_enabled:
        raise HTTPException(status_code=409, detail="账号已设置登录凭据")

    email = _validate_email(req.email)
    _validate_password(req.password)
    existing = await db.scalar(select(User).where(User.email == email, User.id != current_user.id))
    if existing:
        raise HTTPException(status_code=409, detail="邮箱已注册")

    current_user.email = email
    current_user.password_hash = hash_password(req.password)
    current_user.password_login_enabled = True
    display_name = (req.display_name or "").strip()
    if display_name:
        current_user.display_name = display_name
    elif current_user.display_name == WECHAT_PLACEHOLDER_DISPLAY_NAME:
        current_user.display_name = _display_name_from_email(email)

    await db.commit()
    await db.refresh(current_user)
    return _auth_response(current_user)


@router.post("/logout", status_code=204)
async def logout(
    response: Response,
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    db: AsyncSession = Depends(get_db),
):
    await revoke_session(db, session_cookie)
    clear_session_cookie(response)


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)):
    return _user_response(current_user)


@router.patch("/me", response_model=UserResponse)
async def update_me(
    req: AuthUpdateProfileRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    current_user.display_name = req.display_name
    await db.commit()
    await db.refresh(current_user)
    return _user_response(current_user)


@router.post("/me/avatar", response_model=UserResponse)
async def update_me_avatar(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user = await user_avatars.update_user_avatar(
        db,
        user=current_user,
        upload=file,
    )
    return _user_response(user)


@router.get("/avatars/{user_id}/{filename}")
async def get_avatar(user_id: str, filename: str):
    path = user_avatars.avatar_file_path(user_id, filename)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="头像不存在")
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
