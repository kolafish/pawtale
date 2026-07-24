from datetime import datetime

from pydantic import BaseModel, field_validator


class UserResponse(BaseModel):
    id: str
    email: str
    password_login_enabled: bool
    role: str = "user"
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


class WeChatBindRequest(BaseModel):
    code: str


class AuthSetCredentialsRequest(BaseModel):
    email: str
    password: str
    display_name: str | None = None


class AuthUpdateProfileRequest(BaseModel):
    display_name: str

    @field_validator("display_name")
    @classmethod
    def _valid_display_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("昵称不能为空")
        if len(normalized) > 40:
            raise ValueError("昵称不能超过 40 个字")
        return normalized


class AuthResponse(BaseModel):
    user: UserResponse
    needs_credentials: bool = False
