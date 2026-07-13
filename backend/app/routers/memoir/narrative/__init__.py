from fastapi import APIRouter

from . import chapter_structures, claims, entities, followup_tasks, gaps, stories


router = APIRouter(tags=["memoir-narrative"])
router.include_router(entities.router)
router.include_router(claims.router)
router.include_router(stories.router)
router.include_router(chapter_structures.router)
router.include_router(gaps.router)
router.include_router(followup_tasks.router)
