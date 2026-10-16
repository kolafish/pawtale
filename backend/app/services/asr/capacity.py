from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.config import Settings, get_settings


VOICE_GATEWAY_CAPACITY_REACHED = "voice_gateway_capacity_reached"
VOICE_GATEWAY_USER_SESSION_EXISTS = "voice_gateway_user_session_exists"


@dataclass(frozen=True)
class VoiceGatewayCapacityLimits:
    max_active_sessions: int = 10
    max_active_sessions_per_user: int = 1

    @classmethod
    def from_settings(cls, settings: Settings) -> "VoiceGatewayCapacityLimits":
        return cls(
            max_active_sessions=settings.voice_gateway_max_active_sessions,
            max_active_sessions_per_user=settings.voice_gateway_max_active_sessions_per_user,
        )


@dataclass(frozen=True)
class VoiceGatewayCapacitySnapshot:
    active_sessions: int
    active_users: int
    max_active_sessions: int
    max_active_sessions_per_user: int


class VoiceGatewayCapacityError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class VoiceGatewayCapacityLease:
    def __init__(
        self,
        *,
        manager: "VoiceGatewayCapacityManager",
        user_id: str,
        voice_session_id: str,
    ) -> None:
        self._manager = manager
        self.user_id = user_id
        self.voice_session_id = voice_session_id
        self._released = False

    async def release(self) -> None:
        if self._released:
            return
        self._released = True
        await self._manager.release(self.user_id, self.voice_session_id)


class VoiceGatewayCapacityManager:
    def __init__(self, limits: VoiceGatewayCapacityLimits) -> None:
        self.limits = limits
        self._lock = asyncio.Lock()
        self._active_session_users: dict[str, str] = {}
        self._active_user_sessions: dict[str, set[str]] = {}

    async def acquire(self, user_id: str, voice_session_id: str) -> VoiceGatewayCapacityLease:
        async with self._lock:
            if voice_session_id in self._active_session_users:
                raise VoiceGatewayCapacityError(VOICE_GATEWAY_USER_SESSION_EXISTS)

            max_active_sessions = self.limits.max_active_sessions
            if max_active_sessions > 0 and len(self._active_session_users) >= max_active_sessions:
                raise VoiceGatewayCapacityError(VOICE_GATEWAY_CAPACITY_REACHED)

            user_sessions = self._active_user_sessions.setdefault(user_id, set())
            max_per_user = self.limits.max_active_sessions_per_user
            if max_per_user > 0 and len(user_sessions) >= max_per_user:
                raise VoiceGatewayCapacityError(VOICE_GATEWAY_USER_SESSION_EXISTS)

            self._active_session_users[voice_session_id] = user_id
            user_sessions.add(voice_session_id)

        return VoiceGatewayCapacityLease(
            manager=self,
            user_id=user_id,
            voice_session_id=voice_session_id,
        )

    async def release(self, user_id: str, voice_session_id: str) -> None:
        async with self._lock:
            if self._active_session_users.get(voice_session_id) != user_id:
                return
            self._active_session_users.pop(voice_session_id, None)
            user_sessions = self._active_user_sessions.get(user_id)
            if user_sessions is None:
                return
            user_sessions.discard(voice_session_id)
            if not user_sessions:
                self._active_user_sessions.pop(user_id, None)

    async def snapshot(self) -> VoiceGatewayCapacitySnapshot:
        async with self._lock:
            return VoiceGatewayCapacitySnapshot(
                active_sessions=len(self._active_session_users),
                active_users=len(self._active_user_sessions),
                max_active_sessions=self.limits.max_active_sessions,
                max_active_sessions_per_user=self.limits.max_active_sessions_per_user,
            )

    async def has_active_session(self, session_ids: list[str]) -> bool:
        async with self._lock:
            return any(session_id in self._active_session_users for session_id in session_ids)


voice_gateway_capacity_manager = VoiceGatewayCapacityManager(
    VoiceGatewayCapacityLimits.from_settings(get_settings())
)
