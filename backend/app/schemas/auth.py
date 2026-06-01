from datetime import datetime

from pydantic import BaseModel


class UserResponse(BaseModel):
    id: str
    email: str
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


class AuthResponse(BaseModel):
    user: UserResponse
