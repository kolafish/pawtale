from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import (
    MemoirCompletionIssueChannel,
    MemoirCompletionIssueOutcome,
    MemoirCompletionIssuePriority,
    MemoirCompletionIssueScope,
    MemoirCompletionIssueStatus,
    MemoirCompletionIssueType,
)
from app.models.memoir import (
    MemoirCompletionIssue,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.schemas.memoir_completion_issues import (
    MemoirCompletionIssueCollectionResponse,
    MemoirCompletionIssueResponse,
)
_PRIORITY_ORDER = {
    MemoirCompletionIssuePriority.HIGH.value: 0,
    MemoirCompletionIssuePriority.MEDIUM.value: 1,
    MemoirCompletionIssuePriority.LOW.value: 2,
}


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _issue_fingerprint(
    *,
    scope: MemoirCompletionIssueScope,
    section_id: str | None,
    chapter_id: str | None,
    issue_type: MemoirCompletionIssueType,
    related_material_ids: list[str],
    related_scope_ids: list[str] | None = None,
) -> str:
    payload = {
        "scope": scope.value,
        "section_id": section_id,
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


def _response(issue: MemoirCompletionIssue) -> MemoirCompletionIssueResponse:
    return MemoirCompletionIssueResponse(
        id=issue.id,
        project_id=issue.project_id,
        scope=issue.scope,
        section_id=issue.section_id,
        chapter_id=issue.chapter_id,
        issue_type=issue.issue_type,
        channel=issue.channel,
        question=issue.question,
        reason=issue.reason,
        priority=issue.priority,
        related_material_ids=list(issue.related_material_ids_json),
        input_signature=issue.input_signature,
        status=issue.status,
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
                "section_id": None,
                "chapter_id": chapter_id,
                "issue_type": issue_type.value,
                "channel": channel.value,
                "question": question,
                "reason": reason,
                "priority": priority.value,
                "fingerprint": _issue_fingerprint(
                    scope=MemoirCompletionIssueScope.CHAPTER,
                    section_id=None,
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
                    MemoirCompletionIssue.section_id.is_(None),
                    MemoirCompletionIssue.scope
                    == MemoirCompletionIssueScope.CHAPTER.value,
                )
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
    now = _utcnow()
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
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        try:
            issue_type = MemoirCompletionIssueType(candidate.get("issue_type"))
            priority = MemoirCompletionIssuePriority(candidate.get("priority"))
            channel = MemoirCompletionIssueChannel(candidate.get("channel"))
        except ValueError:
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
            {
                "project_id": project_id,
                "scope": MemoirCompletionIssueScope.BOOK.value,
                "section_id": None,
                "chapter_id": None,
                "issue_type": issue_type.value,
                "channel": channel.value,
                "question": question,
                "reason": reason,
                "priority": priority.value,
                "fingerprint": _issue_fingerprint(
                    scope=MemoirCompletionIssueScope.BOOK,
                    section_id=None,
                    chapter_id=None,
                    issue_type=issue_type,
                    related_material_ids=material_ids,
                    related_scope_ids=chapter_ids,
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
                    MemoirCompletionIssue.scope
                    == MemoirCompletionIssueScope.BOOK.value,
                )
            )
        ).all()
    )
    by_fingerprint = {item.fingerprint: item for item in existing}
    fingerprints = {value["fingerprint"] for value in values}
    current: list[MemoirCompletionIssue] = []
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
    now = _utcnow()
    for issue in existing:
        if (
            issue.fingerprint not in fingerprints
            and issue.status != MemoirCompletionIssueStatus.CLOSED.value
        ):
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
) -> MemoirCompletionIssueCollectionResponse:
    issues = await list_issues(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        include_closed=include_closed,
    )
    current = next(
        (
            issue
            for issue in issues
            if issue.status != MemoirCompletionIssueStatus.CLOSED.value
        ),
        None,
    )
    return MemoirCompletionIssueCollectionResponse(
        project_id=project_id,
        current_issue=_response(current) if current is not None else None,
        issues=[_response(issue) for issue in issues],
    )


async def get_issue(
    db: AsyncSession,
    *,
    project_id: str,
    issue_id: str,
) -> MemoirCompletionIssue:
    issue = await db.scalar(
        select(MemoirCompletionIssue).where(
            MemoirCompletionIssue.project_id == project_id,
            MemoirCompletionIssue.id == issue_id,
        )
    )
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
    )
    if issue.status == MemoirCompletionIssueStatus.CLOSED.value:
        raise HTTPException(status_code=409, detail="补全问题已经结束")
    if issue.status == MemoirCompletionIssueStatus.OPEN.value:
        issue.status = MemoirCompletionIssueStatus.IN_PROGRESS.value
        issue.started_at = _utcnow()
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
    issue = await get_issue(
        db,
        project_id=project_id,
        issue_id=issue_id,
    )
    if issue.status == MemoirCompletionIssueStatus.CLOSED.value:
        if issue.outcome == outcome.value:
            return issue
        raise HTTPException(status_code=409, detail="补全问题已经结束")
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
    issue.closed_at = _utcnow()
    db.add(issue)
    await db.flush()
    return issue


def issue_response(issue: MemoirCompletionIssue) -> MemoirCompletionIssueResponse:
    return _response(issue)
