from __future__ import annotations

from app.domain.memoir.types import MemoirEditingJobType
from app.services.memoir import book_auto_revision
from app.services.memoir import book_generation_jobs
from app.services.memoir import book_quality_audit
from app.services.memoir import material_offline_asr_jobs
from app.services.memoir.narrative import ingestion_jobs as narrative_ingestion
from app.services.memoir.narrative import gap_audit
from app.services.memoir import source_cards
from app.services.memoir import source_auto_assignment
from app.services.memoir import voice_transcript_correction
from app.services.memoir.editing_jobs import register_editing_job_handler


def register_memoir_editing_job_handlers() -> None:
    register_editing_job_handler(
        MemoirEditingJobType.CORRECT_TRANSCRIPT,
        voice_transcript_correction.run_transcript_correction_job,
    )
    register_editing_job_handler(
        MemoirEditingJobType.REPROCESS_MATERIAL_OFFLINE_ASR,
        material_offline_asr_jobs.run_material_offline_asr_job,
    )
    register_editing_job_handler(
        MemoirEditingJobType.INGEST_NARRATIVE_STATE,
        narrative_ingestion.run_narrative_ingestion_job,
    )
    register_editing_job_handler(
        MemoirEditingJobType.RECOMPUTE_CHAPTER_STRUCTURE_AUDIT,
        gap_audit.run_chapter_gap_audit_job,
    )
    register_editing_job_handler(
        MemoirEditingJobType.ROUTE_SOURCES,
        source_auto_assignment.run_source_routing_job,
    )
    register_editing_job_handler(
        MemoirEditingJobType.GENERATE_SOURCE_CARD,
        source_cards.run_source_card_generation_job,
    )
    register_editing_job_handler(
        MemoirEditingJobType.REVISE_CHAPTER,
        book_auto_revision.run_chapter_revision_job,
    )
    register_editing_job_handler(
        MemoirEditingJobType.QUALITY_AUDIT,
        book_quality_audit.run_book_quality_audit_job,
    )
    register_editing_job_handler(
        MemoirEditingJobType.GENERATE_CHAPTER_DRAFT,
        book_generation_jobs.run_chapter_draft_generation_job,
    )
    register_editing_job_handler(
        MemoirEditingJobType.GENERATE_BOOK_OUTPUT,
        book_generation_jobs.run_book_output_generation_job,
    )
    register_editing_job_handler(
        MemoirEditingJobType.GENERATE_BOOK_EDITORIAL_COPY,
        book_generation_jobs.run_book_editorial_copy_generation_job,
    )
