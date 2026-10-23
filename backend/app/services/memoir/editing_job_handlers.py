from __future__ import annotations

from app.domain.memoir.types import MemoirEditingJobType
from app.services.memoir import book_generation_jobs
from app.services.memoir import automatic_book_draft
from app.services.memoir import book_audio_jobs
from app.services.memoir import book_review_jobs
from app.services.memoir import book_revision_jobs
from app.services.memoir import chapter_preparation_jobs
from app.services.memoir import material_offline_asr_jobs
from app.services.memoir import material_image_generation
from app.services.memoir import narrator_profile
from app.services.memoir import story_tree_growth
from app.services.memoir import retrieval_source_index
from app.services.memoir import section_regeneration
from app.services.memoir import section_revision_jobs
from app.services.memoir import voice_transcript_correction
from app.services.memoir import share_video_candidates
from app.services.memoir.editing_jobs import register_editing_job_handler
from app.services.memoir.narrative import framework_matching


def register_memoir_editing_job_handlers() -> None:
    register_editing_job_handler(
        MemoirEditingJobType.GENERATE_BOOK_AUDIO_CHUNK,
        book_audio_jobs.run_book_audio_chunk_job,
    )
    register_editing_job_handler(MemoirEditingJobType.UPDATE_BOOK_DRAFT, automatic_book_draft.run_book_draft_job)
    register_editing_job_handler(
        MemoirEditingJobType.CORRECT_TRANSCRIPT,
        voice_transcript_correction.run_transcript_correction_job,
    )
    register_editing_job_handler(
        MemoirEditingJobType.EVALUATE_SHARE_VIDEO_CANDIDATE,
        share_video_candidates.run_share_video_candidate_job,
    )
    register_editing_job_handler(
        MemoirEditingJobType.INDEX_RETRIEVAL_SOURCE,
        retrieval_source_index.run_source_indexing_job,
    )
    register_editing_job_handler(
        MemoirEditingJobType.INDEX_RETRIEVAL_SEMANTICS,
        retrieval_source_index.run_semantic_indexing_job,
    )
    register_editing_job_handler(
        MemoirEditingJobType.REPROCESS_MATERIAL_OFFLINE_ASR,
        material_offline_asr_jobs.run_material_offline_asr_job,
    )
    register_editing_job_handler(
        MemoirEditingJobType.GENERATE_MATERIAL_IMAGE,
        material_image_generation.run_material_image_generation_job,
    )
    register_editing_job_handler(
        MemoirEditingJobType.REGENERATE_CHAPTER_SECTIONS,
        section_regeneration.run_chapter_section_regeneration_job,
    )
    register_editing_job_handler(
        MemoirEditingJobType.ORGANIZE_MATERIAL,
        story_tree_growth.run_story_tree_growth_job,
    )
    register_editing_job_handler(
        MemoirEditingJobType.FILL_NARRATOR_PROFILE,
        narrator_profile.run_narrator_profile_job,
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
        MemoirEditingJobType.GENERATE_BOOK_REVIEW,
        book_review_jobs.run_book_review_job,
    )
    register_editing_job_handler(
        MemoirEditingJobType.GENERATE_BOOK_REVISION_PROPOSAL,
        book_revision_jobs.run_book_revision_job,
    )
    register_editing_job_handler(
        MemoirEditingJobType.GENERATE_CHAPTER_SECTION_REVISION,
        section_revision_jobs.run_section_revision_job,
    )
    register_editing_job_handler(
        MemoirEditingJobType.PREPARE_CHAPTER,
        chapter_preparation_jobs.run_chapter_preparation_job,
    )
