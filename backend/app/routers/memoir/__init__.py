from fastapi import APIRouter

from . import mobile
from . import voice


router = APIRouter(tags=["mobile-memoir"])
router.include_router(mobile.router, prefix="/api/mobile/memoir")
router.include_router(voice.router, prefix="/api/mobile/memoir")
