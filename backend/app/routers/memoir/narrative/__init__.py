from fastapi import APIRouter

from . import claims, entities


router = APIRouter(tags=["memoir-narrative"])
router.include_router(entities.router)
router.include_router(claims.router)
