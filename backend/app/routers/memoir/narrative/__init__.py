from fastapi import APIRouter

from . import (
    book_completion,
    book_consistency,
    book_output,
    claims,
    completion_items,
    entities,
    stories,
)


router = APIRouter(tags=["memoir-narrative"])
router.include_router(book_completion.router)
router.include_router(book_consistency.router)
router.include_router(entities.router)
router.include_router(claims.router)
router.include_router(stories.router)
router.include_router(book_output.router)
router.include_router(completion_items.router)
