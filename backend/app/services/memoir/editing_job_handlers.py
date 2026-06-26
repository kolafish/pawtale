from __future__ import annotations

from app.domain.memoir.types import MemoirEditingJobType
from app.services.memoir import book_auto_revision
from app.services.memoir import book_quality_audit
from app.services.memoir import source_auto_assignment
from app.services.memoir.editing_jobs import register_editing_job_handler


def register_memoir_editing_job_handlers() -> None:
    register_editing_job_handler(
        MemoirEditingJobType.ROUTE_SOURCES,
        source_auto_assignment.run_source_routing_job,
    )
    register_editing_job_handler(
        MemoirEditingJobType.REVISE_CHAPTER,
        book_auto_revision.run_chapter_revision_job,
    )
    register_editing_job_handler(
        MemoirEditingJobType.QUALITY_AUDIT,
        book_quality_audit.run_book_quality_audit_job,
    )
