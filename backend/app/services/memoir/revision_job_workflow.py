from __future__ import annotations

from typing import Awaitable, Callable

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import MemoirEditingJobStatus
from app.models.memoir import MemoirEditingJob
from app.services.memoir import book_revision_generation, editing_jobs
from app.services.memoir.editing_jobs import EditingJobResult


ProgressCallback = Callable[[dict[str, object]], Awaitable[None]]
RevisionJobOperation = Callable[
    [dict[str, object], ProgressCallback],
    Awaitable[dict[str, object]],
]


async def run(
    db: AsyncSession,
    job: MemoirEditingJob,
    operation: RevisionJobOperation,
) -> EditingJobResult:
    progress = dict(job.result_json or {})
    update = editing_jobs.editing_job_progress_callback(
        db,
        job=job,
        progress=progress,
    )
    try:
        result = await operation(dict(job.payload_json or {}), update)
        return EditingJobResult(
            result_json={**progress, **result, "stage": "completed"},
        )
    except editing_jobs.EditingJobProgressLost:
        return EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            result_json={**progress, "stage": "cancelled", "cancelled": True},
        )
    except book_revision_generation.BookRevisionError as exc:
        return EditingJobResult(
            status=MemoirEditingJobStatus.FAILED,
            result_json={
                **progress,
                "stage": "failed",
                "generation": exc.generation,
            },
            error_message=str(exc),
        )
    except (HTTPException, ValueError) as exc:
        return EditingJobResult(
            status=MemoirEditingJobStatus.FAILED,
            result_json={**progress, "stage": "failed"},
            error_message=(
                str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
            ),
        )
