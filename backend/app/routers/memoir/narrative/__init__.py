from fastapi import APIRouter

from . import (
    book_consistency,
    chapter_structures,
    claims,
    draft_blocks,
    draft_generation,
    entities,
    followup_tasks,
    gaps,
    stories,
)


router = APIRouter(tags=["memoir-narrative"])
router.include_router(book_consistency.router)
router.include_router(entities.router)
router.include_router(claims.router)
router.include_router(stories.router)
router.include_router(chapter_structures.router)
router.include_router(draft_blocks.router)
router.include_router(draft_generation.router)
router.include_router(gaps.router)
router.include_router(followup_tasks.router)
