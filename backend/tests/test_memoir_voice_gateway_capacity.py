import pytest

from app.config import Settings
from app.services.memoir.voice_gateway_capacity import (
    VOICE_GATEWAY_CAPACITY_REACHED,
    VOICE_GATEWAY_USER_SESSION_EXISTS,
    VoiceGatewayCapacityError,
    VoiceGatewayCapacityLimits,
    VoiceGatewayCapacityManager,
)


async def test_voice_gateway_capacity_rejects_global_limit_until_release():
    manager = VoiceGatewayCapacityManager(
        VoiceGatewayCapacityLimits(max_active_sessions=1, max_active_sessions_per_user=1)
    )
    lease = await manager.acquire("user-1", "session-1")

    with pytest.raises(VoiceGatewayCapacityError) as exc_info:
        await manager.acquire("user-2", "session-2")

    assert exc_info.value.code == VOICE_GATEWAY_CAPACITY_REACHED

    await lease.release()
    replacement = await manager.acquire("user-2", "session-2")
    snapshot = await manager.snapshot()

    assert snapshot.active_sessions == 1
    assert snapshot.active_users == 1
    await replacement.release()


async def test_voice_gateway_capacity_rejects_second_session_for_same_user():
    manager = VoiceGatewayCapacityManager(
        VoiceGatewayCapacityLimits(max_active_sessions=10, max_active_sessions_per_user=1)
    )
    lease = await manager.acquire("user-1", "session-1")

    with pytest.raises(VoiceGatewayCapacityError) as exc_info:
        await manager.acquire("user-1", "session-2")

    assert exc_info.value.code == VOICE_GATEWAY_USER_SESSION_EXISTS
    await lease.release()


async def test_voice_gateway_capacity_rejects_duplicate_session_id():
    manager = VoiceGatewayCapacityManager(
        VoiceGatewayCapacityLimits(max_active_sessions=10, max_active_sessions_per_user=2)
    )
    lease = await manager.acquire("user-1", "session-1")

    with pytest.raises(VoiceGatewayCapacityError) as exc_info:
        await manager.acquire("user-1", "session-1")

    assert exc_info.value.code == VOICE_GATEWAY_USER_SESSION_EXISTS
    await lease.release()


async def test_voice_gateway_capacity_release_is_idempotent():
    manager = VoiceGatewayCapacityManager(
        VoiceGatewayCapacityLimits(max_active_sessions=1, max_active_sessions_per_user=1)
    )
    lease = await manager.acquire("user-1", "session-1")

    await lease.release()
    await lease.release()
    snapshot = await manager.snapshot()

    assert snapshot.active_sessions == 0
    assert snapshot.active_users == 0


async def test_voice_gateway_capacity_zero_limits_mean_unlimited():
    manager = VoiceGatewayCapacityManager(
        VoiceGatewayCapacityLimits(max_active_sessions=0, max_active_sessions_per_user=0)
    )

    leases = [
        await manager.acquire("user-1", "session-1"),
        await manager.acquire("user-1", "session-2"),
        await manager.acquire("user-2", "session-3"),
    ]
    snapshot = await manager.snapshot()

    assert snapshot.active_sessions == 3
    assert snapshot.active_users == 2
    for lease in leases:
        await lease.release()


def test_voice_gateway_capacity_limits_read_settings():
    settings = Settings(
        voice_gateway_max_active_sessions=12,
        voice_gateway_max_active_sessions_per_user=2,
    )

    limits = VoiceGatewayCapacityLimits.from_settings(settings)

    assert limits.max_active_sessions == 12
    assert limits.max_active_sessions_per_user == 2
