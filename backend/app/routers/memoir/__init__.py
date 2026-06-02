from fastapi import APIRouter

from . import mobile


router = APIRouter(tags=["mobile-memoir"])
router.include_router(mobile.router, prefix="/api/mobile/memoir")
