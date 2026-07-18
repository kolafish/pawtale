from fastapi import APIRouter

from . import (
    book_completion,
    book_output,
)


router = APIRouter(tags=["memoir-narrative"])
router.include_router(book_completion.router)
router.include_router(book_output.router)
