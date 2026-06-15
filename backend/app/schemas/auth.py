from datetime import datetime

from pydantic import BaseModel


class UserResponse(BaseModel):
    id: str
    email: str
    password_login_enabled: bool
    display_name: str
    avatar_url: str | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class AuthRegisterRequest(BaseModel):
    email: str
    password: str
    display_name: str | None = None


class AuthLoginRequest(BaseModel):
    email: str
    password: str


class WeChatLoginRequest(BaseModel):
    code: str
    display_name: str | None = None
    avatar_url: str | None = None


class WeChatBindRequest(BaseModel):
    code: str


class AuthSetCredentialsRequest(BaseModel):
    email: str
    password: str
    display_name: str | None = None


class AuthResponse(BaseModel):
    user: UserResponse
    needs_credentials: bool = False
