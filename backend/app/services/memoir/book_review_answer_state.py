from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import (
    MemoirCompletionIssueChannel,
    MemoirCompletionIssueOutcome,
    MemoirCompletionIssueScope,
    MemoirCompletionIssueStatus,
)
from app.models.memoir import (
    MemoirBookOutput,
    MemoirBookOutputVersion,
    MemoirBookReview,
    MemoirCompletionIssue,
)
from app.services.memoir import completion_issues
from app.services.memoir.semantic_state import semantic_json_signature


BOOK_REVIEW_ANSWER_APPLICATION_KEY = "book_review_answer_application"


@dataclass(frozen=True)
class BookReviewAnswer:
    issue_id: str
    fingerprint: str
    question: str
    reason: str
    answer: str
    chapter_ids: tuple[str, ...]
    resolution: str


@dataclass(frozen=True)
class BookReviewAnswerContext:
    review: MemoirBookReview
    answers: tuple[BookReviewAnswer, ...]
    unresolved_count: int
    answers_signature: str

    @property
    def related_chapter_ids(self) -> frozenset[str]:
        return frozenset(
            chapter_id
            for answer in self.answers
            for chapter_id in answer.chapter_ids
        )


def _candidate_map(review: MemoirBookReview) -> dict[str, dict]:
    review_json = review.review_json if isinstance(review.review_json, dict) else {}
    candidates = review_json.get("candidate_issues")
    return dict(
        completion_issues.book_review_candidate_fingerprints(
            candidates if isinstance(candidates, list) else []
        )
    )


def _answers_signature(answers: list[BookReviewAnswer]) -> str:
    if not answers:
        return ""
    return semantic_json_signature(
        [
            {
                "issue_id": answer.issue_id,
                "fingerprint": answer.fingerprint,
                "question": answer.question,
                "answer": answer.answer,
                "chapter_ids": list(answer.chapter_ids),
                "resolution": answer.resolution,
            }
            for answer in answers
        ]
    )


async def load_answer_context(
    db: AsyncSession,
    *,
    review: MemoirBookReview,
) -> BookReviewAnswerContext:
    issues = list(
        (
            await db.scalars(
                select(MemoirCompletionIssue)
                .where(
                    MemoirCompletionIssue.project_id == review.project_id,
                    MemoirCompletionIssue.scope
                    == MemoirCompletionIssueScope.BOOK.value,
                    MemoirCompletionIssue.channel
                    == MemoirCompletionIssueChannel.MANUAL_REVIEW.value,
                    MemoirCompletionIssue.input_signature
                    == review.input_signature,
                )
                .order_by(
                    MemoirCompletionIssue.created_at,
                    MemoirCompletionIssue.id,
                )
            )
        ).all()
    )
    candidate_by_fingerprint = _candidate_map(review)
    answers: list[BookReviewAnswer] = []
    unresolved_count = 0
    for issue in issues:
        if issue.status != MemoirCompletionIssueStatus.CLOSED.value:
            unresolved_count += 1
            continue
        if (
            issue.outcome != MemoirCompletionIssueOutcome.CONFIRMED.value
            or not issue.outcome_note.strip()
        ):
            continue
        candidate = candidate_by_fingerprint.get(issue.fingerprint)
        if not candidate:
            continue
        chapter_ids = tuple(
            dict.fromkeys(
                str(value).strip()
                for value in candidate.get("chapter_ids", [])
                if str(value).strip()
            )
        )
        if not chapter_ids:
            continue
        answers.append(
            BookReviewAnswer(
                issue_id=issue.id,
                fingerprint=issue.fingerprint,
                question=issue.question.strip(),
                reason=issue.reason.strip(),
                answer=issue.outcome_note.strip(),
                chapter_ids=chapter_ids,
                resolution=str(candidate.get("resolution") or "").strip(),
            )
        )
    return BookReviewAnswerContext(
        review=review,
        answers=tuple(answers),
        unresolved_count=unresolved_count,
        answers_signature=_answers_signature(answers),
    )


def application_marker(output: MemoirBookOutput | None) -> dict[str, object]:
    if output is None or not isinstance(output.metadata_json, dict):
        return {}
    marker = output.metadata_json.get(BOOK_REVIEW_ANSWER_APPLICATION_KEY)
    return marker if isinstance(marker, dict) else {}


def marker_matches_context(
    *,
    output: MemoirBookOutput | None,
    version: MemoirBookOutputVersion | None,
    context: BookReviewAnswerContext,
) -> bool:
    marker = application_marker(output)
    return bool(
        version is not None
        and context.answers
        and marker.get("review_input_signature") == context.review.input_signature
        and marker.get("answers_signature") == context.answers_signature
        and marker.get("output_version_id") == version.id
    )
