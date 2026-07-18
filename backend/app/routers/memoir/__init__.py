from fastapi import APIRouter

from . import book
from . import book_reviews
from . import chapter_pipeline
from . import completion_issues
from . import interview_editor
from . import material_editor
from . import mobile
from . import narrative
from . import sections
from . import section_digests
from . import speaker_transcription
from . import transcript_cleanup_editor
from . import voice
from . import voice_gateway


router = APIRouter(tags=["mobile-memoir"])
router.include_router(interview_editor.router, prefix="/api/memoir")
router.include_router(material_editor.router, prefix="/api/memoir")
router.include_router(chapter_pipeline.router, prefix="/api/memoir")
router.include_router(completion_issues.router, prefix="/api/memoir")
router.include_router(transcript_cleanup_editor.router, prefix="/api/memoir")
router.include_router(narrative.router, prefix="/api/memoir")
router.include_router(sections.router, prefix="/api/memoir")
router.include_router(section_digests.router, prefix="/api/memoir")
router.include_router(book.router, prefix="/api/mobile/memoir")
router.include_router(book_reviews.router, prefix="/api/memoir")
router.include_router(mobile.router, prefix="/api/mobile/memoir")
router.include_router(voice.router, prefix="/api/mobile/memoir")
router.include_router(voice_gateway.router, prefix="/api/mobile/memoir")
router.include_router(speaker_transcription.router, prefix="/api/mobile/memoir")
