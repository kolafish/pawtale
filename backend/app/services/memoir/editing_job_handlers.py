from __future__ import annotations

from app.domain.memoir.types import MemoirEditingJobType
from app.services.memoir import book_generation_jobs
from app.services.memoir import book_quality_audit
from app.services.memoir import material_offline_asr_jobs
from app.services.memoir import source_cards
from app.services.memoir import source_auto_assignment
from app.services.memoir import voice_transcript_correction
from app.services.memoir.editing_jobs import register_editing_job_handler
from app.services.memoir.narrative import book_consistency_audit
from app.services.memoir.narrative import completion_refresh
from app.services.memoir.narrative import draft_generation_jobs
from app.services.memoir.narrative import framework_matching
from app.services.memoir.narrative import ingestion_jobs as narrative_ingestion
from app.services.memoir.narrative import interview_policy_outcomes
from app.services.memoir.narrative import interview_policy_runtime


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
        MemoirEditingJobType.EVALUATE_INTERVIEW_POLICY_SHADOW,
        interview_policy_runtime.run_shadow_policy_evaluation_job,
    )
    register_editing_job_handler(
        MemoirEditingJobType.EVALUATE_INTERVIEW_POLICY_OUTCOME,
        interview_policy_outcomes.run_policy_outcome_evaluation_job,
    )
    register_editing_job_handler(
        MemoirEditingJobType.EVALUATE_TOPIC_MAP_CLAIMS,
        framework_matching.run_framework_matching_job,
    )
    register_editing_job_handler(
        MemoirEditingJobType.REFRESH_CHAPTER_COMPLETION,
        completion_refresh.run_chapter_completion_refresh_job,
    )
    register_editing_job_handler(
        MemoirEditingJobType.GENERATE_DRAFT_BLOCKS,
        draft_generation_jobs.run_draft_block_generation_job,
    )
    register_editing_job_handler(
        MemoirEditingJobType.AUDIT_BOOK_CONSISTENCY,
        book_consistency_audit.run_book_consistency_audit_job,
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
        MemoirEditingJobType.QUALITY_AUDIT,
        book_quality_audit.run_book_quality_audit_job,
    )
    register_editing_job_handler(
        MemoirEditingJobType.GENERATE_BOOK_EDITORIAL_COPY,
        book_generation_jobs.run_book_editorial_copy_generation_job,
    )
