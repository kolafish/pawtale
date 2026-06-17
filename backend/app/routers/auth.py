import hashlib
import secrets

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    clear_session_cookie,
    get_current_user,
    hash_password,
    normalize_email,
    set_session_cookie,
    verify_password,
)
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
from app.services.wechat_auth import WeChatAuthError, WeChatSession, exchange_wechat_login_code


router = APIRouter(prefix="/api/auth", tags=["auth"])
WECHAT_PROVIDER = "wechat_miniprogram"
WECHAT_PLACEHOLDER_DISPLAY_NAME = "微信用户"


def _validate_email(email: str) -> str:
    normalized = normalize_email(email)
    if "@" not in normalized or "." not in normalized.rsplit("@", 1)[-1]:
        raise HTTPException(status_code=400, detail="邮箱格式不正确")
    return normalized


def _validate_password(password: str) -> None:
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="密码至少需要 8 个字符")


def _provisional_wechat_email(openid: str) -> str:
    digest = hashlib.sha256(openid.encode("utf-8")).hexdigest()[:24]
    return f"wechat_{digest}@wechat.local"


def _display_name_from_wechat(req: WeChatLoginRequest) -> str:
    display_name = (req.display_name or "").strip()
    return display_name or WECHAT_PLACEHOLDER_DISPLAY_NAME


def _display_name_from_email(email: str) -> str:
    return email.split("@", 1)[0].strip() or "作者"


async def _exchange_wechat_code(code: str) -> WeChatSession:
    normalized = code.strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="缺少微信登录 code")
    try:
        return await exchange_wechat_login_code(normalized)
    except WeChatAuthError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


async def _find_wechat_identity(db: AsyncSession, openid: str) -> AuthIdentity | None:
    return await db.scalar(
        select(AuthIdentity).where(
            AuthIdentity.provider == WECHAT_PROVIDER,
            AuthIdentity.provider_user_id == openid,
        )
    )


def _auth_response(user: User) -> AuthResponse:
    return AuthResponse(
        user=UserResponse.model_validate(user),
        needs_credentials=not user.password_login_enabled,
    )


@router.post("/register", response_model=AuthResponse, status_code=201)
async def register(
    req: AuthRegisterRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
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
    await db.commit()
    await db.refresh(user)
    set_session_cookie(response, user.id)
    return _auth_response(user)


@router.post("/login", response_model=AuthResponse)
async def login(
    req: AuthLoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    email = normalize_email(req.email)
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if (
        not user
        or not user.password_login_enabled
        or not verify_password(req.password, user.password_hash)
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="邮箱或密码不正确")
    set_session_cookie(response, user.id)
    return _auth_response(user)


@router.post("/wechat-login", response_model=AuthResponse)
async def wechat_login(
    req: WeChatLoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    session = await _exchange_wechat_code(req.code)
    identity = await _find_wechat_identity(db, session.openid)
    if identity:
        user = await db.get(User, identity.user_id)
        if user is None:
            raise HTTPException(status_code=409, detail="微信身份关联的用户不存在")
        if session.unionid and identity.provider_union_id != session.unionid:
            identity.provider_union_id = session.unionid
        if req.avatar_url and not user.avatar_url:
            user.avatar_url = req.avatar_url
        if req.display_name and user.display_name == WECHAT_PLACEHOLDER_DISPLAY_NAME:
            user.display_name = req.display_name.strip() or user.display_name
    else:
        user = User(
            email=_provisional_wechat_email(session.openid),
            password_hash=hash_password(secrets.token_urlsafe(32)),
            password_login_enabled=False,
            display_name=_display_name_from_wechat(req),
            avatar_url=req.avatar_url,
        )
        identity = AuthIdentity(
            user=user,
            provider=WECHAT_PROVIDER,
            provider_user_id=session.openid,
            provider_union_id=session.unionid,
            profile_json={
                "display_name": req.display_name,
                "avatar_url": req.avatar_url,
            },
        )
        db.add_all([user, identity])

    await db.commit()
    await db.refresh(user)
    set_session_cookie(response, user.id)
    return _auth_response(user)


@router.post("/wechat-bind", response_model=AuthResponse)
async def wechat_bind(
    req: WeChatBindRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session = await _exchange_wechat_code(req.code)
    identity = await _find_wechat_identity(db, session.openid)
    if identity and identity.user_id != current_user.id:
        raise HTTPException(status_code=409, detail="该微信已绑定其他账号")
    if identity is None:
        db.add(
            AuthIdentity(
                user_id=current_user.id,
                provider=WECHAT_PROVIDER,
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
async def logout(response: Response):
    clear_session_cookie(response)


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)):
    return UserResponse.model_validate(current_user)


@router.patch("/me", response_model=UserResponse)
async def update_me(
    req: AuthUpdateProfileRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    current_user.display_name = req.display_name
    await db.commit()
    await db.refresh(current_user)
    return UserResponse.model_validate(current_user)
