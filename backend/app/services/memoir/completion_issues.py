from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from hashlib import sha256
import json
import re
import unicodedata

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow_naive
from app.domain.memoir.types import (
    MemoirCompletionIssueChannel,
    MemoirCompletionIssueOutcome,
    MemoirCompletionIssuePriority,
    MemoirCompletionIssueScope,
    MemoirCompletionIssueStatus,
    MemoirCompletionIssueType,
    MemoirVoiceSessionStatus,
)
from app.models.memoir import (
    MemoirBookPlanChapter,
    MemoirBookOutput,
    MemoirCompletionIssue,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.schemas.memoir_completion_issues import (
    MemoirCompletionIssueCollectionResponse,
    MemoirCompletionIssueResponse,
)
from app.services.memoir.chapter_generation import latest_chapter_draft_version
from app.services.memoir import fact_decisions
from app.services.memoir.book_output_state import invalidate_book_output
_PRIORITY_ORDER = {
    MemoirCompletionIssuePriority.HIGH.value: 0,
    MemoirCompletionIssuePriority.MEDIUM.value: 1,
    MemoirCompletionIssuePriority.LOW.value: 2,
}


BLOCKING_BOOK_ISSUE_TYPES = frozenset({
    MemoirCompletionIssueType.CONFLICT.value,
})


def blocking_book_issues(
    issues: Iterable[MemoirCompletionIssue],
    all_chapter_ids: Iterable[str],
) -> list[MemoirCompletionIssue]:
    chapter_ids = set(all_chapter_ids)
    return [
        issue for issue in issues
        if issue.channel == MemoirCompletionIssueChannel.MANUAL_REVIEW.value
        and issue.status != MemoirCompletionIssueStatus.CLOSED.value
        and blocking_book_chapter_ids([issue], chapter_ids) & chapter_ids
    ]


def blocking_book_chapter_ids(
    issues: Iterable[MemoirCompletionIssue],
    all_chapter_ids: Iterable[str],
) -> set[str]:
    blocked: set[str] = set()
    for issue in issues:
        if issue.issue_type not in BLOCKING_BOOK_ISSUE_TYPES:
            continue
        if issue.chapter_id:
            blocked.add(issue.chapter_id)
        elif issue.scope == MemoirCompletionIssueScope.BOOK.value:
            if not issue.related_chapter_ids_json:
                return set(all_chapter_ids)
            blocked.update(issue.related_chapter_ids_json)
    return blocked


async def open_blocking_book_issues(
    db: AsyncSession,
    *,
    project_id: str,
    all_chapter_ids: Iterable[str],
) -> list[MemoirCompletionIssue]:
    issues = (await db.scalars(select(MemoirCompletionIssue).where(
        MemoirCompletionIssue.project_id == project_id,
        MemoirCompletionIssue.channel == MemoirCompletionIssueChannel.MANUAL_REVIEW.value,
        MemoirCompletionIssue.status != MemoirCompletionIssueStatus.CLOSED.value,
        MemoirCompletionIssue.issue_type.in_(BLOCKING_BOOK_ISSUE_TYPES),
    ))).all()
    return sorted(blocking_book_issues(issues, all_chapter_ids), key=_sort_key)


async def open_blocking_book_chapter_ids(
    db: AsyncSession,
    *,
    project_id: str,
    all_chapter_ids: Iterable[str],
) -> set[str]:
    chapter_ids = set(all_chapter_ids)
    issues = await open_blocking_book_issues(db, project_id=project_id, all_chapter_ids=chapter_ids)
    return blocking_book_chapter_ids(issues, chapter_ids) & chapter_ids


def _issue_fingerprint(
    *,
    scope: MemoirCompletionIssueScope,
    chapter_id: str | None,
    issue_type: MemoirCompletionIssueType,
    related_material_ids: list[str],
    related_scope_ids: list[str] | None = None,
) -> str:
    payload = {
        "scope": scope.value,
        "chapter_id": chapter_id,
        "issue_type": issue_type.value,
        "material_ids": sorted(set(related_material_ids)),
        "related_scope_ids": sorted(set(related_scope_ids or [])),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(encoded.encode("utf-8")).hexdigest()


def _disambiguate_issue_values(values: list[dict]) -> list[dict]:
    result: list[dict] = []
    seen_candidates: set[tuple[str, str]] = set()
    occurrences: dict[str, int] = {}
    for value in values:
        base_fingerprint = value["fingerprint"]
        candidate_key = (
            base_fingerprint,
            " ".join(str(value["question"]).split()),
        )
        if candidate_key in seen_candidates:
            continue
        seen_candidates.add(candidate_key)
        occurrence = occurrences.get(base_fingerprint, 0)
        occurrences[base_fingerprint] = occurrence + 1
        if occurrence:
            value = {
                **value,
                "fingerprint": sha256(
                    f"{base_fingerprint}:{occurrence}".encode("utf-8")
                ).hexdigest(),
            }
        result.append(value)
    return result


def stable_book_issue_fingerprint(candidate: dict) -> str:
    """Identify a book question without depending on model output order."""
    base = _issue_fingerprint(
        scope=MemoirCompletionIssueScope.BOOK,
        chapter_id=None,
        issue_type=MemoirCompletionIssueType(candidate["issue_type"]),
        related_material_ids=[],
        related_scope_ids=candidate["chapter_ids"],
    )
    # A fact decision concerns a passage in the manuscript, not the wording
    # chosen by a later review of that passage. The quote is checked against
    # the cited chapter when a review is normalized.
    evidence = (
        _normalized_evidence(candidate.get("evidence_quote"))
        if candidate["issue_type"] in BLOCKING_BOOK_ISSUE_TYPES
        else ""
    )
    identity = f"evidence:{evidence}" if len(evidence) >= 7 else (
        " ".join(candidate["question"].split())
    )
    return sha256(f"{base}:{identity}".encode("utf-8")).hexdigest()


def _normalized_evidence(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return "".join(unicodedata.normalize("NFKC", value).split()).strip(
        "“”‘’\"'，。！？；：,.!?;:"
    )


def _quoted_evidence(text: str) -> set[str]:
    return {
        quote
        for raw in re.findall(r"[‘“'\"]([^‘’“”'\"]+)[’”'\"]", text)
        if (quote := _normalized_evidence(raw)) and len(quote) >= 7
    }


def _same_cited_fact(issue: MemoirCompletionIssue, value: dict) -> bool:
    """Migrate an old question only when both sides cite the same specific text."""
    if (
        issue.issue_type != value["issue_type"]
        or issue.channel != MemoirCompletionIssueChannel.MANUAL_REVIEW.value
        or sorted(issue.related_chapter_ids_json or [])
        != sorted(value["related_chapter_ids_json"])
    ):
        return False
    evidence = _normalized_evidence(value.get("evidence_quote"))
    if not evidence:
        return False
    return any(
        quote in evidence or evidence in quote
        for quote in _quoted_evidence(issue.question)
    )


async def _cited_fact_removed(
    db: AsyncSession,
    *,
    project_id: str,
    issue: MemoirCompletionIssue,
) -> bool:
    """Only retire a decision when its explicit quote is gone from every cited chapter."""
    quotes = {quote for quote in _quoted_evidence(issue.question) if len(quote) >= 7}
    if not quotes or not issue.related_chapter_ids_json:
        return False
    for chapter_id in issue.related_chapter_ids_json:
        latest = await latest_chapter_draft_version(
            db, project_id=project_id, chapter_id=chapter_id,
        )
        if latest is None:
            return False
        content = _normalized_evidence(latest.content)
        if any(quote in content for quote in quotes):
            return False
    return True


def book_review_candidate_fingerprints(
    candidates: list[dict],
) -> list[tuple[str, dict]]:
    """Return the persisted issue fingerprint paired with each valid candidate."""
    values: list[tuple[dict, dict]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        try:
            issue_type = MemoirCompletionIssueType(candidate.get("issue_type"))
            MemoirCompletionIssuePriority(candidate.get("priority"))
            MemoirCompletionIssueChannel(candidate.get("channel"))
        except ValueError:
            continue
        if issue_type == MemoirCompletionIssueType.PRIVACY:
            continue
        question = str(candidate.get("question") or "").strip()
        reason = str(candidate.get("reason") or "").strip()
        if not question or not reason:
            continue
        material_ids = [
            str(value).strip()
            for value in candidate.get("material_ids", [])
            if str(value).strip()
        ]
        chapter_ids = [
            str(value).strip()
            for value in candidate.get("chapter_ids", [])
            if str(value).strip()
        ]
        values.append(
            (
                {
                    "fingerprint": _issue_fingerprint(
                        scope=MemoirCompletionIssueScope.BOOK,
                        chapter_id=None,
                        issue_type=issue_type,
                        related_material_ids=material_ids,
                        related_scope_ids=chapter_ids,
                    ),
                    "question": question,
                },
                candidate,
            )
        )

    result: list[tuple[str, dict]] = []
    seen_candidates: set[tuple[str, str]] = set()
    occurrences: dict[str, int] = {}
    for value, candidate in values:
        base_fingerprint = value["fingerprint"]
        candidate_key = (
            base_fingerprint,
            " ".join(str(value["question"]).split()),
        )
        if candidate_key in seen_candidates:
            continue
        seen_candidates.add(candidate_key)
        stored_fingerprint = candidate.get("fingerprint")
        if (
            isinstance(stored_fingerprint, str)
            and len(stored_fingerprint) == 64
            and all(char in "0123456789abcdef" for char in stored_fingerprint)
        ):
            fingerprint = stored_fingerprint
        else:
            # Reviews persisted before stable fingerprints retain their existing
            # issue identities until they are regenerated.
            occurrence = occurrences.get(base_fingerprint, 0)
            occurrences[base_fingerprint] = occurrence + 1
            fingerprint = (
                sha256(f"{base_fingerprint}:{occurrence}".encode("utf-8")).hexdigest()
                if occurrence
                else base_fingerprint
            )
        result.append((fingerprint, candidate))
    # Two independent questions must not collide just because the model cited
    # the same passage. Disambiguate them by question, independent of order.
    counts: dict[str, int] = {}
    for fingerprint, _ in result:
        counts[fingerprint] = counts.get(fingerprint, 0) + 1
    return [
        (
            sha256(
                f"{fingerprint}:{' '.join(candidate['question'].split())}".encode("utf-8")
            ).hexdigest(),
            candidate,
        )
        if counts[fingerprint] > 1 else (fingerprint, candidate)
        for fingerprint, candidate in result
    ]


def _response(
    issue: MemoirCompletionIssue,
    *,
    active_session_id: str | None = None,
    blocks_book: bool = False,
) -> MemoirCompletionIssueResponse:
    return MemoirCompletionIssueResponse(
        id=issue.id,
        project_id=issue.project_id,
        scope=issue.scope,
        chapter_id=issue.chapter_id,
        issue_type=issue.issue_type,
        channel=issue.channel,
        question=issue.question,
        reason=issue.reason,
        priority=issue.priority,
        related_material_ids=(
            [] if issue.scope == MemoirCompletionIssueScope.BOOK.value
            else list(issue.related_material_ids_json)
        ),
        related_chapter_ids=list(issue.related_chapter_ids_json),
        input_signature=issue.input_signature,
        status=issue.status,
        blocks_book=blocks_book,
        active_session_id=active_session_id,
        outcome=issue.outcome or None,
        outcome_note=issue.outcome_note,
        answered_material_id=issue.answered_material_id,
        started_at=issue.started_at,
        closed_at=issue.closed_at,
        created_at=issue.created_at,
        updated_at=issue.updated_at,
    )


def _sort_key(issue: MemoirCompletionIssue) -> tuple[int, datetime, str]:
    return (
        _PRIORITY_ORDER.get(issue.priority, 99),
        issue.created_at,
        issue.id,
    )


async def sync_chapter_digest_issues(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    input_signature: str,
    candidates: list[dict],
) -> list[MemoirCompletionIssue]:
    values: list[dict] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        try:
            issue_type = MemoirCompletionIssueType(candidate.get("issue_type"))
            priority = MemoirCompletionIssuePriority(candidate.get("priority"))
            channel = MemoirCompletionIssueChannel(candidate.get("channel"))
        except ValueError:
            continue
        if issue_type == MemoirCompletionIssueType.PRIVACY:
            continue
        question = str(candidate.get("question") or "").strip()
        reason = str(candidate.get("reason") or "").strip()
        if not question or not reason:
            continue
        material_ids = [
            str(value).strip()
            for value in candidate.get("material_ids", [])
            if str(value).strip()
        ]
        section_ids = [
            str(value).strip()
            for value in candidate.get("section_ids", [])
            if str(value).strip()
        ]
        values.append(
            {
                "project_id": project_id,
                "scope": MemoirCompletionIssueScope.CHAPTER.value,
                "chapter_id": chapter_id,
                "issue_type": issue_type.value,
                "channel": channel.value,
                "question": question,
                "reason": reason,
                "priority": priority.value,
                "fingerprint": _issue_fingerprint(
                    scope=MemoirCompletionIssueScope.CHAPTER,
                    chapter_id=chapter_id,
                    issue_type=issue_type,
                    related_material_ids=material_ids,
                    related_scope_ids=section_ids,
                ),
                "related_material_ids_json": material_ids,
                "input_signature": input_signature,
            }
        )
    values = _disambiguate_issue_values(values)
    existing = list(
        (
            await db.scalars(
                select(MemoirCompletionIssue).where(
                    MemoirCompletionIssue.project_id == project_id,
                    MemoirCompletionIssue.chapter_id == chapter_id,
                    MemoirCompletionIssue.scope
                    == MemoirCompletionIssueScope.CHAPTER.value,
                ).order_by(MemoirCompletionIssue.id).with_for_update()
                .execution_options(populate_existing=True)
            )
        ).all()
    )
    by_fingerprint = {item.fingerprint: item for item in existing}
    current: list[MemoirCompletionIssue] = []
    fingerprints = {value["fingerprint"] for value in values}
    for value in values:
        issue = by_fingerprint.get(value["fingerprint"])
        if issue is None:
            issue = MemoirCompletionIssue(**value)
            db.add(issue)
        elif issue.status != MemoirCompletionIssueStatus.CLOSED.value:
            for field, field_value in value.items():
                if field not in {"project_id", "fingerprint"}:
                    setattr(issue, field, field_value)
            db.add(issue)
        current.append(issue)
    now = utcnow_naive()
    for issue in existing:
        if (
            issue.fingerprint not in fingerprints
            and issue.status != MemoirCompletionIssueStatus.CLOSED.value
        ):
            issue.status = MemoirCompletionIssueStatus.CLOSED.value
            issue.outcome = MemoirCompletionIssueOutcome.SUPERSEDED.value
            issue.outcome_note = "章节内容变化后，该问题已不再是当前问题"
            issue.closed_at = now
            db.add(issue)
    await db.flush()
    return sorted(current, key=_sort_key)


async def sync_book_review_issues(
    db: AsyncSession,
    *,
    project_id: str,
    input_signature: str,
    candidates: list[dict],
) -> list[MemoirCompletionIssue]:
    values: list[dict] = []
    for fingerprint, candidate in book_review_candidate_fingerprints(candidates):
        issue_type = MemoirCompletionIssueType(candidate.get("issue_type"))
        priority = MemoirCompletionIssuePriority(candidate.get("priority"))
        channel = MemoirCompletionIssueChannel(candidate.get("channel"))
        question = str(candidate.get("question") or "").strip()
        reason = str(candidate.get("reason") or "").strip()
        chapter_ids = [
            str(value).strip()
            for value in candidate.get("chapter_ids", [])
            if str(value).strip()
        ]
        values.append(
            {
                "project_id": project_id,
                "scope": MemoirCompletionIssueScope.BOOK.value,
                "chapter_id": None,
                "issue_type": issue_type.value,
                "channel": channel.value,
                "question": question,
                "reason": reason,
                "priority": priority.value,
                "fingerprint": fingerprint,
                "related_material_ids_json": [],
                "related_chapter_ids_json": chapter_ids,
                "resolution": str(candidate.get("resolution") or "").strip(),
                "input_signature": input_signature,
                "evidence_quote": str(candidate.get("evidence_quote") or "").strip(),
            }
        )
    existing = list(
        (
            await db.scalars(
                select(MemoirCompletionIssue).where(
                    MemoirCompletionIssue.project_id == project_id,
                    MemoirCompletionIssue.scope
                    == MemoirCompletionIssueScope.BOOK.value,
                ).order_by(MemoirCompletionIssue.id).with_for_update()
                .execution_options(populate_existing=True)
            )
        ).all()
    )
    by_fingerprint = {item.fingerprint: item for item in existing}
    fingerprints = {value["fingerprint"] for value in values}
    current: list[MemoirCompletionIssue] = []
    migrated_ids: set[str] = set()
    available = [
        item for item in existing
        if item.fingerprint not in fingerprints
    ]
    fact_matches: dict[str, list[MemoirCompletionIssue]] = {}
    for value in values:
        if value["issue_type"] not in BLOCKING_BOOK_ISSUE_TYPES:
            continue
        matching = [item for item in available if _same_cited_fact(item, value)]
        active = [
            item for item in matching
            if item.status != MemoirCompletionIssueStatus.CLOSED.value
        ]
        decided = [
            item for item in matching
            if item.status == MemoirCompletionIssueStatus.CLOSED.value
            and item.outcome != MemoirCompletionIssueOutcome.SUPERSEDED.value
        ]
        fact_matches[value["fingerprint"]] = active or decided or matching
    match_counts: dict[str, int] = {}
    for matches in fact_matches.values():
        for item in matches:
            match_counts[item.id] = match_counts.get(item.id, 0) + 1
    for value in values:
        issue = by_fingerprint.get(value["fingerprint"])
        if issue is None:
            legacy_matches = [
                item for item in existing
                if item.id not in migrated_ids
                and item.input_signature == input_signature
                and item.issue_type == value["issue_type"]
                and " ".join(item.question.split()) == " ".join(value["question"].split())
                and item.fingerprint not in fingerprints
                and (
                    not item.related_chapter_ids_json
                    or sorted(item.related_chapter_ids_json)
                    == sorted(value["related_chapter_ids_json"])
                )
            ]
            issue = legacy_matches[0] if len(legacy_matches) == 1 else None
            if issue is None:
                matches = fact_matches.get(value["fingerprint"], [])
                if len(matches) == 1 and match_counts[matches[0].id] == 1:
                    issue = matches[0]
            if issue is not None:
                issue.fingerprint = value["fingerprint"]
                migrated_ids.add(issue.id)
        if issue is None:
            issue = MemoirCompletionIssue(**{
                key: field_value for key, field_value in value.items()
                if key != "evidence_quote"
            })
            db.add(issue)
        elif (
            issue.status != MemoirCompletionIssueStatus.CLOSED.value
            or issue.outcome == MemoirCompletionIssueOutcome.SUPERSEDED.value
        ):
            if issue.outcome == MemoirCompletionIssueOutcome.SUPERSEDED.value:
                issue.status = MemoirCompletionIssueStatus.OPEN.value
                issue.outcome = ""
                issue.outcome_note = ""
                issue.closed_at = None
                issue.started_at = None
            for field, field_value in value.items():
                if field not in {"project_id", "fingerprint", "evidence_quote"}:
                    setattr(issue, field, field_value)
            db.add(issue)
        elif not fact_decisions.is_confirmed_fact(issue):
            issue.related_chapter_ids_json = value["related_chapter_ids_json"]
            issue.related_material_ids_json = []
            issue.resolution = value["resolution"]
            issue.input_signature = input_signature
            db.add(issue)
        current.append(issue)
    now = utcnow_naive()
    for issue in existing:
        if (
            issue.fingerprint not in fingerprints
            and issue.id not in migrated_ids
            and issue.status != MemoirCompletionIssueStatus.CLOSED.value
        ):
            if (
                issue.channel == MemoirCompletionIssueChannel.MANUAL_REVIEW.value
                and issue.issue_type in BLOCKING_BOOK_ISSUE_TYPES
            ):
                # Model omission alone does not settle a fact conflict.
                # Only the deterministic disappearance of its quoted source
                # from the current chapter draft can retire it automatically.
                if not await _cited_fact_removed(
                    db, project_id=project_id, issue=issue,
                ):
                    issue.input_signature = input_signature
                    db.add(issue)
                    current.append(issue)
                    continue
            issue.status = MemoirCompletionIssueStatus.CLOSED.value
            issue.outcome = MemoirCompletionIssueOutcome.SUPERSEDED.value
            issue.outcome_note = "全书内容变化后，该问题已不再是当前问题"
            issue.closed_at = now
            db.add(issue)
    await db.flush()
    return sorted(current, key=_sort_key)


async def list_issues(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str | None = None,
    include_closed: bool = False,
) -> list[MemoirCompletionIssue]:
    filters = [MemoirCompletionIssue.project_id == project_id]
    if chapter_id is not None:
        filters.append(MemoirCompletionIssue.chapter_id == chapter_id)
    if not include_closed:
        filters.append(
            MemoirCompletionIssue.status
            != MemoirCompletionIssueStatus.CLOSED.value
        )
        # Legacy privacy questions stay in history, outside the private book's
        # active queue. Reading must not rewrite the user's recorded decision.
        filters.append(
            MemoirCompletionIssue.issue_type != MemoirCompletionIssueType.PRIVACY.value
        )
    issues = list(
        (
            await db.scalars(
                select(MemoirCompletionIssue).where(*filters)
            )
        ).all()
    )
    return sorted(issues, key=_sort_key)


async def collection_response(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str | None = None,
    include_closed: bool = False,
    issues: list[MemoirCompletionIssue] | None = None,
) -> MemoirCompletionIssueCollectionResponse:
    issue_rows = (
        issues
        if issues is not None
        else await list_issues(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
            include_closed=include_closed,
        )
    )
    active_sessions: dict[str, str] = {}
    blocking_ids: set[str] = set()
    if issue_rows:
        chapter_ids = (await db.scalars(select(MemoirBookPlanChapter.id).where(
            MemoirBookPlanChapter.project_id == project_id,
            MemoirBookPlanChapter.include_in_book.is_(True),
        ))).all()
        blocking_ids = {issue.id for issue in blocking_book_issues(issue_rows, chapter_ids)}
        rows = await db.execute(
            select(
                MemoirVoiceSession.current_completion_issue_id,
                MemoirVoiceSession.id,
            )
            .where(
                MemoirVoiceSession.project_id == project_id,
                MemoirVoiceSession.current_completion_issue_id.in_(
                    [issue.id for issue in issue_rows]
                ),
                MemoirVoiceSession.status.not_in(
                    {
                        MemoirVoiceSessionStatus.STOPPED.value,
                        MemoirVoiceSessionStatus.FAILED.value,
                    }
                ),
            )
            .order_by(
                MemoirVoiceSession.updated_at.desc(),
                MemoirVoiceSession.id.desc(),
            )
        )
        for issue_id, session_id in rows:
            active_sessions.setdefault(issue_id, session_id)
    return MemoirCompletionIssueCollectionResponse(
        project_id=project_id,
        issues=[
            _response(
                issue,
                active_session_id=active_sessions.get(issue.id),
                blocks_book=issue.id in blocking_ids,
            )
            for issue in issue_rows
        ],
    )


async def get_issue(
    db: AsyncSession,
    *,
    project_id: str,
    issue_id: str,
    lock: bool = False,
) -> MemoirCompletionIssue:
    query = select(MemoirCompletionIssue).where(
        MemoirCompletionIssue.project_id == project_id,
        MemoirCompletionIssue.id == issue_id,
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    issue = await db.scalar(query)
    if issue is None:
        raise HTTPException(status_code=404, detail="补全问题不存在")
    return issue


async def start_issue(
    db: AsyncSession,
    *,
    project_id: str,
    issue_id: str,
) -> MemoirCompletionIssue:
    issue = await get_issue(
        db,
        project_id=project_id,
        issue_id=issue_id,
        lock=True,
    )
    if issue.status == MemoirCompletionIssueStatus.CLOSED.value:
        raise HTTPException(status_code=409, detail="补全问题已经结束")
    if issue.status == MemoirCompletionIssueStatus.OPEN.value:
        issue.status = MemoirCompletionIssueStatus.IN_PROGRESS.value
        issue.started_at = utcnow_naive()
        db.add(issue)
        await db.flush()
    return issue


async def close_issue(
    db: AsyncSession,
    *,
    project_id: str,
    issue_id: str,
    outcome: MemoirCompletionIssueOutcome,
    note: str,
    answered_material_id: str | None,
) -> MemoirCompletionIssue:
    # Serialize answers with chapter writes/assembly so an in-flight generation
    # either sees this decision or is invalidated by it after its save.
    from app.services.memoir.sections import lock_project_chapters
    await lock_project_chapters(db, project_id=project_id)
    issue = await db.scalar(select(MemoirCompletionIssue).where(
        MemoirCompletionIssue.project_id == project_id,
        MemoirCompletionIssue.id == issue_id,
    ).with_for_update().execution_options(populate_existing=True))
    if issue is None:
        raise HTTPException(status_code=404, detail="补全问题不存在")
    if issue.status == MemoirCompletionIssueStatus.CLOSED.value:
        if (
            issue.outcome == outcome.value
            and issue.outcome_note == note.strip()
            and issue.answered_material_id == answered_material_id
        ):
            return issue
        raise HTTPException(status_code=409, detail="该问题已保存其他回答，请刷新后核对")
    if (
        outcome == MemoirCompletionIssueOutcome.CONFIRMED
        and issue.issue_type == MemoirCompletionIssueType.CONFLICT.value
        and not fact_decisions.has_explicit_answer(note)
    ):
        raise HTTPException(status_code=422, detail="请说明需要采用的事实")
    if outcome == MemoirCompletionIssueOutcome.ANSWERED:
        material = await db.scalar(
            select(MemoirVoiceUtterance)
            .join(
                MemoirVoiceSession,
                MemoirVoiceSession.id == MemoirVoiceUtterance.voice_session_id,
            )
            .where(
                MemoirVoiceSession.project_id == project_id,
                MemoirVoiceUtterance.id == answered_material_id,
            )
        )
        if material is None:
            raise HTTPException(status_code=404, detail="回答素材不存在")
    issue.status = MemoirCompletionIssueStatus.CLOSED.value
    issue.outcome = outcome.value
    issue.outcome_note = note.strip()
    issue.answered_material_id = answered_material_id
    issue.closed_at = utcnow_naive()
    if fact_decisions.is_confirmed_fact(issue):
        # Keep stable source anchors, including for book-level decisions. They
        # survive later moves/merges and do not turn an answer into a recording.
        await fact_decisions.bind_sources(db, issue)
        output = await db.scalar(select(MemoirBookOutput).where(MemoirBookOutput.project_id == project_id))
        if output is not None:
            invalidate_book_output(output)
            db.add(output)
    db.add(issue)
    from app.services.memoir.automatic_book_draft import schedule_book_draft
    await schedule_book_draft(db, project_id=project_id, source="decision", settled=True)
    await db.flush()
    return issue


def issue_response(issue: MemoirCompletionIssue) -> MemoirCompletionIssueResponse:
    return _response(issue)
