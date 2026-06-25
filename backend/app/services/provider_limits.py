from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TypeVar


T = TypeVar("T")


class ProviderConcurrencyLimiter:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._semaphores: dict[tuple[str, int], asyncio.Semaphore] = {}

    async def run(self, key: str, limit: int, operation: Callable[[], Awaitable[T]]) -> T:
        if limit <= 0:
            return await operation()

        semaphore = await self._semaphore(key, limit)
        async with semaphore:
            return await operation()

    @asynccontextmanager
    async def slot(self, key: str, limit: int) -> AsyncIterator[None]:
        if limit <= 0:
            yield
            return

        semaphore = await self._semaphore(key, limit)
        async with semaphore:
            yield

    async def _semaphore(self, key: str, limit: int) -> asyncio.Semaphore:
        async with self._lock:
            semaphore_key = (key, limit)
            semaphore = self._semaphores.get(semaphore_key)
            if semaphore is None:
                semaphore = asyncio.Semaphore(limit)
                self._semaphores[semaphore_key] = semaphore
            return semaphore


provider_concurrency_limiter = ProviderConcurrencyLimiter()
