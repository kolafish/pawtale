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
from app.models.users import User
from app.schemas.auth import AuthLoginRequest, AuthRegisterRequest, AuthResponse, UserResponse


router = APIRouter(prefix="/api/auth", tags=["auth"])


def _validate_email(email: str) -> str:
    normalized = normalize_email(email)
    if "@" not in normalized or "." not in normalized.rsplit("@", 1)[-1]:
        raise HTTPException(status_code=400, detail="邮箱格式不正确")
    return normalized


def _validate_password(password: str) -> None:
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="密码至少需要 8 个字符")


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
    return AuthResponse(user=UserResponse.model_validate(user))


@router.post("/login", response_model=AuthResponse)
async def login(
    req: AuthLoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    email = normalize_email(req.email)
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="邮箱或密码不正确")
    set_session_cookie(response, user.id)
    return AuthResponse(user=UserResponse.model_validate(user))


@router.post("/logout", status_code=204)
async def logout(response: Response):
    clear_session_cookie(response)


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)):
    return UserResponse.model_validate(current_user)
