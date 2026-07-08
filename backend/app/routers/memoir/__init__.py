from fastapi import APIRouter

from . import book
from . import debug_speaker
from . import interview_editor
from . import mobile
from . import voice
from . import voice_gateway


router = APIRouter(tags=["mobile-memoir"])
router.include_router(interview_editor.router, prefix="/api/memoir")
router.include_router(book.router, prefix="/api/mobile/memoir")
router.include_router(mobile.router, prefix="/api/mobile/memoir")
router.include_router(voice.router, prefix="/api/mobile/memoir")
router.include_router(voice_gateway.router, prefix="/api/mobile/memoir")
router.include_router(debug_speaker.router, prefix="/api/mobile/memoir")
