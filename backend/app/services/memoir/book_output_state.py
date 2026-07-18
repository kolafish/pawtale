from app.domain.memoir.types import MemoirOutputStatus
from app.models.memoir import MemoirBookOutput


def invalidate_book_output(output: MemoirBookOutput) -> None:
    """Mark the assembled book stale after any chapter or structure change."""
    output.status = MemoirOutputStatus.DRAFT.value
