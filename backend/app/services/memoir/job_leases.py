from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TypeVar
from uuid import uuid4


@dataclass(frozen=True)
class JobLease:
    job_id: str
    token: str


class JobLeaseLostError(RuntimeError):
    pass


T = TypeVar("T")


def new_job_lease(job_id: str) -> JobLease:
    return JobLease(job_id=job_id, token=str(uuid4()))


def lease_deadline(now: datetime, lease_seconds: int) -> datetime:
    return now + timedelta(seconds=max(1, lease_seconds))


async def _lease_heartbeat(
    renew: Callable[[], Awaitable[bool]],
    *,
    stop_event: asyncio.Event,
    lease_lost_event: asyncio.Event,
    interval_seconds: float,
) -> None:
    interval = max(0.01, interval_seconds)
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
            return
        except asyncio.TimeoutError:
            try:
                renewed = await renew()
            except Exception:
                renewed = False
            if not renewed:
                lease_lost_event.set()
                return


async def run_with_lease_heartbeat(
    work: Callable[[], Awaitable[T]],
    renew: Callable[[], Awaitable[bool]],
    *,
    interval_seconds: float,
) -> T:
    stop_event = asyncio.Event()
    lease_lost_event = asyncio.Event()
    heartbeat_task = asyncio.create_task(
        _lease_heartbeat(
            renew,
            stop_event=stop_event,
            lease_lost_event=lease_lost_event,
            interval_seconds=interval_seconds,
        )
    )
    work_task = asyncio.create_task(work())
    lease_lost_task = asyncio.create_task(lease_lost_event.wait())
    try:
        done, _pending = await asyncio.wait(
            {work_task, lease_lost_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if work_task in done:
            return await work_task
        work_task.cancel()
        await asyncio.gather(work_task, return_exceptions=True)
        raise JobLeaseLostError("job lease lost while work was running")
    finally:
        stop_event.set()
        lease_lost_task.cancel()
        if not work_task.done():
            work_task.cancel()
        await asyncio.gather(heartbeat_task, lease_lost_task, work_task, return_exceptions=True)
