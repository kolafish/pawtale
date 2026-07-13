from fastapi import APIRouter

from . import claims, entities, stories


router = APIRouter(tags=["memoir-narrative"])
router.include_router(entities.router)
router.include_router(claims.router)
router.include_router(stories.router)
