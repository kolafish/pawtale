from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.narrative_types import (
    MemoirNarrativeActorType,
    MemoirNarrativeDecisionAction,
    MemoirNarrativeDecisionTargetType,
    MemoirNarrativeGapClaimRole,
    MemoirNarrativeGapDetectedBy,
    MemoirNarrativeGapRelationRole,
    MemoirNarrativeGapScopeType,
    MemoirNarrativeGapStatus,
)
from app.models.memoir import MemoirBookPlanChapter
from app.models.memoir_narrative import (
    MemoirClaim,
    MemoirNarrativeGap,
    MemoirNarrativeGapChapter,
    MemoirNarrativeGapClaim,
    MemoirNarrativeGapStory,
    MemoirStory,
)
from app.schemas.memoir_narrative import (
    MemoirNarrativeGapCandidate,
    MemoirNarrativeGapResponse,
)
from app.services.memoir.narrative import decision_log


TERMINAL_GAP_STATUSES = {
    MemoirNarrativeGapStatus.RESOLVED,
    MemoirNarrativeGapStatus.ACCEPTED_INCOMPLETE,
    MemoirNarrativeGapStatus.NOT_APPLICABLE,
    MemoirNarrativeGapStatus.DISMISSED,
}
AUTO_RULE_RESOLUTION_NOTE = "系统规则复查：触发条件已不再成立。"
SEMANTIC_AUDIT_RESOLUTION_NOTE = "系统语义复查：本轮未再检测到该问题。"


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def gap_fingerprint(candidate: MemoirNarrativeGapCandidate) -> str:
    """Build an issue identity from stable domain coordinates, never AI prose."""
    payload = {
        "scope_type": candidate.scope_type.value,
        "primary_story_id": candidate.primary_story_id or "",
        "primary_chapter_id": candidate.primary_chapter_id or "",
        "gap_type": candidate.gap_type.value,
        "target_slot": candidate.target_slot,
        "story_ids": sorted(candidate.story_roles),
        "chapter_ids": sorted(candidate.chapter_roles),
    }
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return sha256(encoded.encode("utf-8")).hexdigest()


def _gap_state(gap: MemoirNarrativeGap) -> dict[str, object]:
    return {
        "status": gap.status,
        "resolution_note": gap.resolution_note,
        "resolved_by": gap.resolved_by,
        "resolved_by_user_id": gap.resolved_by_user_id,
        "resolved_at": gap.resolved_at.isoformat() if gap.resolved_at else None,
    }


def _was_auto_resolved_rule_gap(gap: MemoirNarrativeGap) -> bool:
    return bool(
        gap.detected_by == MemoirNarrativeGapDetectedBy.RULES.value
        and gap.status == MemoirNarrativeGapStatus.RESOLVED.value
        and gap.resolved_by == MemoirNarrativeActorType.SYSTEM.value
        and gap.resolution_note == AUTO_RULE_RESOLUTION_NOTE
    )


async def _reopen_auto_resolved_rule_gap(
    db: AsyncSession,
    *,
    gap: MemoirNarrativeGap,
) -> None:
    before = _gap_state(gap)
    next_detection_count = gap.detection_count + 1
    gap.status = MemoirNarrativeGapStatus.OPEN.value
    gap.resolution_note = ""
    gap.resolved_by = None
    gap.resolved_by_user_id = None
    gap.resolved_at = None
    await decision_log.record_decision(
        db,
        project_id=gap.project_id,
        target_type=MemoirNarrativeDecisionTargetType.GAP,
        target_id=gap.id,
        action=MemoirNarrativeDecisionAction.NEEDS_REVIEW,
        actor_type=MemoirNarrativeActorType.SYSTEM,
        actor_user_id=None,
        before_state=before,
        after_state=_gap_state(gap),
        reason="系统规则再次检测到该问题",
        idempotency_key=f"rule-gap-reopened:{gap.id}:{next_detection_count}",
    )


async def _validate_candidate_relations(
    db: AsyncSession,
    *,
    project_id: str,
    candidate: MemoirNarrativeGapCandidate,
) -> None:
    story_ids = set(candidate.story_roles)
    if candidate.primary_story_id:
        story_ids.add(candidate.primary_story_id)
    claim_ids = set(candidate.claim_roles)
    chapter_ids = set(candidate.chapter_roles)
    if candidate.primary_chapter_id:
        chapter_ids.add(candidate.primary_chapter_id)

    if story_ids:
        count = await db.scalar(
            select(func.count(MemoirStory.id)).where(
                MemoirStory.project_id == project_id,
                MemoirStory.id.in_(story_ids),
            )
        )
        if count != len(story_ids):
            raise ValueError("gap Story relations must belong to the project")
    if claim_ids:
        count = await db.scalar(
            select(func.count(MemoirClaim.id)).where(
                MemoirClaim.project_id == project_id,
                MemoirClaim.id.in_(claim_ids),
            )
        )
        if count != len(claim_ids):
            raise ValueError("gap Claim relations must belong to the project")
    if chapter_ids:
        count = await db.scalar(
            select(func.count(MemoirBookPlanChapter.id)).where(
                MemoirBookPlanChapter.project_id == project_id,
                MemoirBookPlanChapter.id.in_(chapter_ids),
            )
        )
        if count != len(chapter_ids):
            raise ValueError("gap chapter relations must belong to the project")


async def _sync_relations(
    db: AsyncSession,
    *,
    gap: MemoirNarrativeGap,
    candidate: MemoirNarrativeGapCandidate,
) -> None:
    claim_roles = dict(candidate.claim_roles)
    story_roles = dict(candidate.story_roles)
    chapter_roles = dict(candidate.chapter_roles)
    if candidate.primary_story_id:
        story_roles[candidate.primary_story_id] = MemoirNarrativeGapRelationRole.PRIMARY
    if candidate.primary_chapter_id:
        chapter_roles[candidate.primary_chapter_id] = MemoirNarrativeGapRelationRole.PRIMARY

    await db.execute(
        delete(MemoirNarrativeGapClaim).where(
            MemoirNarrativeGapClaim.project_id == gap.project_id,
            MemoirNarrativeGapClaim.gap_id == gap.id,
        )
    )
    await db.execute(
        delete(MemoirNarrativeGapStory).where(
            MemoirNarrativeGapStory.project_id == gap.project_id,
            MemoirNarrativeGapStory.gap_id == gap.id,
        )
    )
    await db.execute(
        delete(MemoirNarrativeGapChapter).where(
            MemoirNarrativeGapChapter.project_id == gap.project_id,
            MemoirNarrativeGapChapter.gap_id == gap.id,
        )
    )
    db.add_all(
        [
            MemoirNarrativeGapClaim(
                project_id=gap.project_id,
                gap_id=gap.id,
                claim_id=claim_id,
                role=role.value,
            )
            for claim_id, role in sorted(claim_roles.items())
        ]
        + [
            MemoirNarrativeGapStory(
                project_id=gap.project_id,
                gap_id=gap.id,
                story_id=story_id,
                role=role.value,
            )
            for story_id, role in sorted(story_roles.items())
        ]
        + [
            MemoirNarrativeGapChapter(
                project_id=gap.project_id,
                gap_id=gap.id,
                chapter_id=chapter_id,
                role=role.value,
            )
            for chapter_id, role in sorted(chapter_roles.items())
        ]
    )


async def upsert_gap(
    db: AsyncSession,
    *,
    project_id: str,
    candidate: MemoirNarrativeGapCandidate,
) -> tuple[MemoirNarrativeGap, bool]:
    await _validate_candidate_relations(db, project_id=project_id, candidate=candidate)
    fingerprint = gap_fingerprint(candidate)
    gap = await db.scalar(
        select(MemoirNarrativeGap).where(
            MemoirNarrativeGap.project_id == project_id,
            MemoirNarrativeGap.fingerprint == fingerprint,
        )
    )
    created = gap is None
    now = _utcnow()
    if gap is None:
        gap = MemoirNarrativeGap(
            project_id=project_id,
            scope_type=candidate.scope_type.value,
            primary_story_id=candidate.primary_story_id,
            primary_chapter_id=candidate.primary_chapter_id,
            gap_type=candidate.gap_type.value,
            severity=candidate.severity.value,
            target_slot=candidate.target_slot,
            title=candidate.title,
            description=candidate.description,
            fingerprint=fingerprint,
            status=MemoirNarrativeGapStatus.OPEN.value,
            detected_by=candidate.detected_by.value,
            detection_count=1,
            first_detected_at=now,
            last_detected_at=now,
        )
        try:
            async with db.begin_nested():
                db.add(gap)
                await db.flush()
        except IntegrityError:
            gap = await db.scalar(
                select(MemoirNarrativeGap).where(
                    MemoirNarrativeGap.project_id == project_id,
                    MemoirNarrativeGap.fingerprint == fingerprint,
                )
            )
            if gap is None:
                raise
            created = False
    if not created:
        if _was_auto_resolved_rule_gap(gap):
            await _reopen_auto_resolved_rule_gap(db, gap=gap)
        gap.severity = candidate.severity.value
        gap.title = candidate.title
        gap.description = candidate.description
        gap.detected_by = candidate.detected_by.value
        gap.detection_count += 1
        gap.last_detected_at = now
        db.add(gap)
    await _sync_relations(db, gap=gap, candidate=candidate)
    await db.flush()
    return gap, created


async def upsert_gaps(
    db: AsyncSession,
    *,
    project_id: str,
    candidates: list[MemoirNarrativeGapCandidate],
) -> tuple[list[MemoirNarrativeGap], int]:
    gaps: list[MemoirNarrativeGap] = []
    created_count = 0
    seen_fingerprints: set[str] = set()
    for candidate in candidates:
        fingerprint = gap_fingerprint(candidate)
        if fingerprint in seen_fingerprints:
            continue
        seen_fingerprints.add(fingerprint)
        gap, created = await upsert_gap(db, project_id=project_id, candidate=candidate)
        gaps.append(gap)
        created_count += int(created)
    return gaps, created_count


async def reconcile_project_rule_gaps(
    db: AsyncSession,
    *,
    project_id: str,
    complete_candidates: list[MemoirNarrativeGapCandidate],
) -> int:
    """Resolve obsolete rule Gaps after a complete project-wide deterministic audit."""
    active_fingerprints = {
        gap_fingerprint(candidate)
        for candidate in complete_candidates
        if candidate.detected_by == MemoirNarrativeGapDetectedBy.RULES
    }
    open_rule_gaps = (
        await db.scalars(
            select(MemoirNarrativeGap).where(
                MemoirNarrativeGap.project_id == project_id,
                MemoirNarrativeGap.detected_by == MemoirNarrativeGapDetectedBy.RULES.value,
                MemoirNarrativeGap.status == MemoirNarrativeGapStatus.OPEN.value,
            )
        )
    ).all()
    resolved_count = 0
    for gap in open_rule_gaps:
        if gap.fingerprint in active_fingerprints:
            continue
        await resolve_gap_by_system(
            db,
            project_id=project_id,
            gap_id=gap.id,
            idempotency_key=f"rule-gap-obsolete:{gap.id}:{gap.detection_count}",
            note=AUTO_RULE_RESOLUTION_NOTE,
        )
        resolved_count += 1
    return resolved_count


async def reconcile_chapter_rule_gaps(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    complete_candidates: list[MemoirNarrativeGapCandidate],
) -> int:
    """Resolve obsolete deterministic gaps after a complete chapter audit."""
    active_fingerprints = {
        gap_fingerprint(candidate)
        for candidate in complete_candidates
        if candidate.detected_by == MemoirNarrativeGapDetectedBy.RULES
    }
    open_rule_gaps = (
        await db.scalars(
            select(MemoirNarrativeGap).where(
                MemoirNarrativeGap.project_id == project_id,
                MemoirNarrativeGap.detected_by == MemoirNarrativeGapDetectedBy.RULES.value,
                MemoirNarrativeGap.status == MemoirNarrativeGapStatus.OPEN.value,
                MemoirNarrativeGap.scope_type.in_(
                    [
                        MemoirNarrativeGapScopeType.STORY.value,
                        MemoirNarrativeGapScopeType.CHAPTER.value,
                    ]
                ),
                (
                    (MemoirNarrativeGap.primary_chapter_id == chapter_id)
                    | MemoirNarrativeGap.chapters.any(chapter_id=chapter_id)
                ),
            )
        )
    ).all()
    resolved_count = 0
    for gap in open_rule_gaps:
        if gap.fingerprint in active_fingerprints:
            continue
        await resolve_gap_by_system(
            db,
            project_id=project_id,
            gap_id=gap.id,
            idempotency_key=f"chapter-rule-gap-obsolete:{chapter_id}:{gap.id}:{gap.detection_count}",
            note=AUTO_RULE_RESOLUTION_NOTE,
        )
        resolved_count += 1
    return resolved_count


async def reconcile_chapter_semantic_gaps(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    complete_candidates: list[MemoirNarrativeGapCandidate],
) -> int:
    active_fingerprints = {
        gap_fingerprint(candidate)
        for candidate in complete_candidates
        if candidate.detected_by == MemoirNarrativeGapDetectedBy.AI
        and candidate.target_slot.startswith("semantic:")
    }
    open_semantic_gaps = (
        await db.scalars(
            select(MemoirNarrativeGap).where(
                MemoirNarrativeGap.project_id == project_id,
                MemoirNarrativeGap.detected_by
                == MemoirNarrativeGapDetectedBy.AI.value,
                MemoirNarrativeGap.status == MemoirNarrativeGapStatus.OPEN.value,
                MemoirNarrativeGap.target_slot.like("semantic:%"),
                (
                    (MemoirNarrativeGap.primary_chapter_id == chapter_id)
                    | MemoirNarrativeGap.chapters.any(chapter_id=chapter_id)
                ),
            )
        )
    ).all()
    resolved_count = 0
    for gap in open_semantic_gaps:
        if gap.fingerprint in active_fingerprints:
            continue
        await resolve_gap_by_system(
            db,
            project_id=project_id,
            gap_id=gap.id,
            idempotency_key=(
                f"chapter-semantic-gap-obsolete:{chapter_id}:"
                f"{gap.id}:{gap.detection_count}"
            ),
            note=SEMANTIC_AUDIT_RESOLUTION_NOTE,
        )
        resolved_count += 1
    return resolved_count


def _with_relations():
    return (
        selectinload(MemoirNarrativeGap.claims),
        selectinload(MemoirNarrativeGap.stories),
        selectinload(MemoirNarrativeGap.chapters),
    )


async def get_gap(
    db: AsyncSession,
    *,
    project_id: str,
    gap_id: str,
) -> MemoirNarrativeGap | None:
    return await db.scalar(
        select(MemoirNarrativeGap)
        .options(*_with_relations())
        .where(
            MemoirNarrativeGap.project_id == project_id,
            MemoirNarrativeGap.id == gap_id,
        )
    )


async def list_gaps(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str | None = None,
    status: MemoirNarrativeGapStatus | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[MemoirNarrativeGap], int]:
    filters = [MemoirNarrativeGap.project_id == project_id]
    if chapter_id:
        filters.append(
            MemoirNarrativeGap.id.in_(
                select(MemoirNarrativeGapChapter.gap_id).where(
                    MemoirNarrativeGapChapter.project_id == project_id,
                    MemoirNarrativeGapChapter.chapter_id == chapter_id,
                )
            )
        )
    if status:
        filters.append(MemoirNarrativeGap.status == status.value)
    total = await db.scalar(select(func.count(MemoirNarrativeGap.id)).where(*filters)) or 0
    items = list(
        (
            await db.scalars(
                select(MemoirNarrativeGap)
                .options(*_with_relations())
                .where(*filters)
                .order_by(
                    MemoirNarrativeGap.status,
                    MemoirNarrativeGap.severity,
                    MemoirNarrativeGap.last_detected_at.desc(),
                )
                .offset(offset)
                .limit(limit)
            )
        ).unique().all()
    )
    return items, int(total)


async def gap_response(
    db: AsyncSession,
    *,
    gap: MemoirNarrativeGap,
) -> MemoirNarrativeGapResponse:
    if "claims" not in gap.__dict__:
        loaded = await get_gap(db, project_id=gap.project_id, gap_id=gap.id)
        if loaded is None:
            raise ValueError("gap does not belong to the project")
        gap = loaded
    return MemoirNarrativeGapResponse.model_validate(gap)


async def resolve_gap(
    db: AsyncSession,
    *,
    project_id: str,
    gap_id: str,
    status: MemoirNarrativeGapStatus,
    actor_user_id: str,
    idempotency_key: str,
    note: str,
) -> MemoirNarrativeGap:
    if status not in TERMINAL_GAP_STATUSES:
        raise ValueError("gap resolution requires a terminal status")
    if status == MemoirNarrativeGapStatus.DISMISSED and not note.strip():
        raise ValueError("dismissing a gap requires a reason")
    action = (
        MemoirNarrativeDecisionAction.ACCEPT_INCOMPLETE
        if status == MemoirNarrativeGapStatus.ACCEPTED_INCOMPLETE
        else MemoirNarrativeDecisionAction.RESOLVE
    )
    existing_decision = await decision_log.existing_decision(
        db,
        project_id=project_id,
        idempotency_key=idempotency_key,
        target_type=MemoirNarrativeDecisionTargetType.GAP,
        target_id=gap_id,
        action=action,
        actor_type=MemoirNarrativeActorType.USER,
        actor_user_id=actor_user_id,
    )
    gap = await get_gap(db, project_id=project_id, gap_id=gap_id)
    if gap is None:
        raise ValueError("gap does not belong to the project")
    if existing_decision is not None:
        if existing_decision.after_state_json.get("status") != status.value:
            raise ValueError("idempotency key was already used for another gap resolution")
        return gap
    if gap.status != MemoirNarrativeGapStatus.OPEN.value:
        if gap.status == status.value:
            raise ValueError("gap was already resolved with a different idempotency key")
        raise ValueError("resolved gap cannot transition to another resolution")

    before = _gap_state(gap)
    gap.status = status.value
    gap.resolution_note = note.strip()
    gap.resolved_by = MemoirNarrativeActorType.USER.value
    gap.resolved_by_user_id = actor_user_id
    gap.resolved_at = _utcnow()
    db.add(gap)
    await decision_log.record_decision(
        db,
        project_id=project_id,
        target_type=MemoirNarrativeDecisionTargetType.GAP,
        target_id=gap.id,
        action=action,
        actor_type=MemoirNarrativeActorType.USER,
        actor_user_id=actor_user_id,
        before_state=before,
        after_state=_gap_state(gap),
        reason=note.strip(),
        idempotency_key=idempotency_key,
    )
    from app.services.memoir.narrative import followup_tasks

    await followup_tasks.cancel_active_tasks_for_gap(
        db,
        project_id=project_id,
        gap_id=gap.id,
        reason=f"gap resolved as {status.value}",
        decision_identity=idempotency_key,
    )
    await db.flush()
    return gap


async def resolve_gap_by_system(
    db: AsyncSession,
    *,
    project_id: str,
    gap_id: str,
    idempotency_key: str,
    note: str,
) -> MemoirNarrativeGap:
    action = MemoirNarrativeDecisionAction.RESOLVE
    existing = await decision_log.existing_decision(
        db,
        project_id=project_id,
        idempotency_key=idempotency_key,
        target_type=MemoirNarrativeDecisionTargetType.GAP,
        target_id=gap_id,
        action=action,
        actor_type=MemoirNarrativeActorType.SYSTEM,
        actor_user_id=None,
    )
    gap = await get_gap(db, project_id=project_id, gap_id=gap_id)
    if gap is None:
        raise ValueError("gap does not belong to the project")
    if existing is not None:
        return gap
    if gap.status in {status.value for status in TERMINAL_GAP_STATUSES}:
        return gap

    before = _gap_state(gap)
    gap.status = MemoirNarrativeGapStatus.RESOLVED.value
    gap.resolution_note = note.strip()
    gap.resolved_by = MemoirNarrativeActorType.SYSTEM.value
    gap.resolved_by_user_id = None
    gap.resolved_at = _utcnow()
    db.add(gap)
    await decision_log.record_decision(
        db,
        project_id=project_id,
        target_type=MemoirNarrativeDecisionTargetType.GAP,
        target_id=gap.id,
        action=action,
        actor_type=MemoirNarrativeActorType.SYSTEM,
        actor_user_id=None,
        before_state=before,
        after_state=_gap_state(gap),
        reason=note.strip(),
        idempotency_key=idempotency_key,
    )
    from app.services.memoir.narrative import followup_tasks

    await followup_tasks.cancel_active_tasks_for_gap(
        db,
        project_id=project_id,
        gap_id=gap.id,
        reason="gap was resolved by new narrative evidence",
        decision_identity=idempotency_key,
    )
    await db.flush()
    return gap
