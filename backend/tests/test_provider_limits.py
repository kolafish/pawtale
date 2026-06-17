import asyncio

import pytest

from app.services.provider_limits import ProviderConcurrencyLimiter


@pytest.mark.asyncio
async def test_provider_concurrency_limiter_caps_parallel_operations():
    limiter = ProviderConcurrencyLimiter()
    release = asyncio.Event()
    two_started = asyncio.Event()
    active = 0
    max_active = 0
    started = 0

    async def operation() -> str:
        nonlocal active, max_active, started
        active += 1
        started += 1
        max_active = max(max_active, active)
        if started == 2:
            two_started.set()
        try:
            await release.wait()
            return "ok"
        finally:
            active -= 1

    tasks = [asyncio.create_task(limiter.run("ai_text", 2, operation)) for _ in range(5)]
    await asyncio.wait_for(two_started.wait(), timeout=1)
    await asyncio.sleep(0.01)

    assert started == 2
    assert max_active == 2

    release.set()
    assert await asyncio.gather(*tasks) == ["ok"] * 5


@pytest.mark.asyncio
async def test_provider_concurrency_limiter_treats_zero_as_unlimited():
    limiter = ProviderConcurrencyLimiter()
    release = asyncio.Event()
    all_started = asyncio.Event()
    active = 0
    max_active = 0
    started = 0

    async def operation() -> str:
        nonlocal active, max_active, started
        active += 1
        started += 1
        max_active = max(max_active, active)
        if started == 5:
            all_started.set()
        try:
            await release.wait()
            return "ok"
        finally:
            active -= 1

    tasks = [asyncio.create_task(limiter.run("tts", 0, operation)) for _ in range(5)]
    await asyncio.wait_for(all_started.wait(), timeout=1)

    assert max_active == 5

    release.set()
    assert await asyncio.gather(*tasks) == ["ok"] * 5
