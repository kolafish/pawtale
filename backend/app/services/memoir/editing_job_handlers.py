from __future__ import annotations

from app.domain.memoir.types import MemoirEditingJobType
from app.services.memoir import book_generation_jobs
from app.services.memoir import chapter_preparation_jobs
from app.services.memoir import material_offline_asr_jobs
from app.services.memoir import material_organization
from app.services.memoir import section_regeneration
from app.services.memoir import voice_transcript_correction
from app.services.memoir.editing_jobs import register_editing_job_handler
from app.services.memoir.narrative import interview_policy_outcomes
from app.services.memoir.narrative import interview_policy_runtime
from app.services.memoir.narrative import framework_matching


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
        MemoirEditingJobType.REGENERATE_CHAPTER_SECTIONS,
        section_regeneration.run_chapter_section_regeneration_job,
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
        MemoirEditingJobType.ORGANIZE_MATERIAL,
        material_organization.run_material_organization_job,
    )
    register_editing_job_handler(
        MemoirEditingJobType.UPDATE_TOPIC_MAP,
        framework_matching.run_framework_matching_job,
    )
    register_editing_job_handler(
        MemoirEditingJobType.GENERATE_BOOK_EDITORIAL_COPY,
        book_generation_jobs.run_book_editorial_copy_generation_job,
    )
    register_editing_job_handler(
        MemoirEditingJobType.PREPARE_CHAPTER,
        chapter_preparation_jobs.run_chapter_preparation_job,
    )
