from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.narrative_types import (
    MemoirChapterContractStatus,
    MemoirClaimPublicationStatus,
    MemoirClaimEvidenceRole,
    MemoirClaimVerificationStatus,
    MemoirFollowupTaskClaimRole,
    MemoirFollowupTaskStatus,
    MemoirNarrativeActorType,
    MemoirNarrativeGapClaimRole,
    MemoirNarrativeGapSeverity,
    MemoirNarrativeGapStatus,
    MemoirNarrativeGapType,
    MemoirStoryLifecycleStatus,
    MemoirStoryPublicationStatus,
    MemoirStorySlotStatus,
    MemoirStoryVerificationStatus,
)
from app.domain.memoir.types import MemoirBookPlanChapterStatus, MemoirSessionGoal
from app.models.memoir import MemoirBookPlanChapter, MemoirVoiceSession
from app.models.memoir_narrative import (
    MemoirChapterContract,
    MemoirChapterNode,
    MemoirChapterNodeStory,
    MemoirClaim,
    MemoirFollowupTask,
    MemoirFollowupTaskClaim,
    MemoirNarrativeGap,
    MemoirStory,
    MemoirStorySlot,
    MemoirStorySlotClaim,
)
from app.schemas.memoir_narrative.story_slots import story_slot_schema
from app.services.memoir.narrative import followup_tasks


FOLLOWUP_PLANNER_VERSION = "1"
NON_INTERVIEW_GAP_TYPES = {MemoirNarrativeGapType.REPETITION}
TERMINAL_SLOT_STATUSES = {
    MemoirStorySlotStatus.SUFFICIENT.value,
    MemoirStorySlotStatus.ACCEPTED_INCOMPLETE.value,
    MemoirStorySlotStatus.NOT_APPLICABLE.value,
}
SLOT_LABELS = {
    "background": "当时的背景",
    "trigger": "事情的起因",
    "people": "在场的人",
    "difficulty": "遇到的困难",
    "actions": "当时采取的行动",
    "turning_point": "事情的转折",
    "outcome": "最后的结果",
    "reflection": "现在回想时的感受",
    "meeting": "最初怎么认识",
    "person_portrait": "这个人的样子和性格",
    "shared_experience": "共同经历",
    "relationship_change": "关系后来怎么变化",
    "representative_scene": "最能代表这段关系的一幕",
    "entry_reason": "为什么进入这个人生阶段",
    "responsibilities": "当时承担的责任",
    "representative_events": "最有代表性的一件事",
    "ability_and_character": "那段经历形成的能力和性格",
    "transition": "后来如何进入下一阶段",
    "theme_definition": "这件事对你的含义",
    "supporting_cases": "能说明这个主题的具体事情",
    "contrast_or_failure": "一次对照或挫折",
    "continuity": "这件事后来的影响",
}


@dataclass(frozen=True)
class FollowupGenerationResult:
    created_task_ids: tuple[str, ...]
    updated_task_ids: tuple[str, ...]
    skipped_gap_ids: tuple[str, ...]


@dataclass(frozen=True)
class FollowupPromptContext:
    task_id: str
    question: str
    prompt_text: str


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _gap_story_id(gap: MemoirNarrativeGap) -> str | None:
    if gap.primary_story_id:
        return gap.primary_story_id
    primary = next((relation.story_id for relation in gap.stories if relation.role == "primary"), None)
    return primary or next((relation.story_id for relation in gap.stories), None)


def _gap_chapter_id(gap: MemoirNarrativeGap) -> str | None:
    if gap.primary_chapter_id:
        return gap.primary_chapter_id
    primary = next((relation.chapter_id for relation in gap.chapters if relation.role == "primary"), None)
    return primary or next((relation.chapter_id for relation in gap.chapters), None)


def _story_slot_key(gap: MemoirNarrativeGap, story: MemoirStory | None) -> str:
    if story is None or gap.gap_type != MemoirNarrativeGapType.MISSING_SLOT.value:
        return ""
    schema = story_slot_schema(story.story_type, story.slot_schema_version)
    return gap.target_slot if gap.target_slot in schema.slot_keys else ""


def _task_question(gap: MemoirNarrativeGap, story: MemoirStory | None, slot_key: str) -> str:
    story_prefix = f"关于“{story.title}”，" if story else ""
    if slot_key:
        return f"{story_prefix}{SLOT_LABELS.get(slot_key, slot_key)}，你还记得哪些具体情况？"
    if gap.gap_type == MemoirNarrativeGapType.CHRONOLOGY.value:
        return f"{story_prefix}这些事情大概是什么先后顺序？"
    if gap.gap_type == MemoirNarrativeGapType.RELATIONSHIP.value:
        return f"{story_prefix}这里提到的人彼此是什么关系？"
    if gap.gap_type == MemoirNarrativeGapType.CAUSALITY.value:
        return f"{story_prefix}是什么原因让事情后来这样发展？"
    if gap.gap_type == MemoirNarrativeGapType.CONTRADICTION.value:
        return f"{story_prefix}这两种说法里，哪一种更接近当时的实际情况？"
    if gap.gap_type == MemoirNarrativeGapType.UNCLEAR_REFERENCE.value:
        return f"{story_prefix}这里说的那个人或那件事，具体指的是谁或什么？"
    if gap.gap_type == MemoirNarrativeGapType.UNSUPPORTED_FACT.value:
        return f"{story_prefix}这件事你是亲身经历的，还是后来听谁讲的？"
    return f"{story_prefix}{gap.title}，你愿意再讲一点具体情况吗？"


def _task_priority(
    gap: MemoirNarrativeGap,
    *,
    story_required: bool,
    chapter_order: int | None,
) -> int:
    severity = {
        MemoirNarrativeGapSeverity.HIGH.value: 3000,
        MemoirNarrativeGapSeverity.MEDIUM.value: 2000,
        MemoirNarrativeGapSeverity.LOW.value: 1000,
    }.get(gap.severity, 0)
    required_bonus = 400 if story_required else 0
    chapter_bonus = max(0, 200 - min(chapter_order or 0, 200))
    return severity + required_bonus + chapter_bonus


def _safe_claim(claim: MemoirClaim) -> bool:
    return (
        claim.superseded_by_claim_id is None
        and claim.verification_status != MemoirClaimVerificationStatus.REJECTED.value
        and claim.publication_status == MemoirClaimPublicationStatus.ELIGIBLE.value
    )


def _known_context_summary(story: MemoirStory | None, claims: list[MemoirClaim]) -> str:
    parts: list[str] = []
    if story and story.summary.strip():
        parts.append(story.summary.strip())
    parts.extend(claim.statement.strip() for claim in claims if claim.statement.strip())
    return "；".join(dict.fromkeys(parts))[:2000]


async def _required_story_ids(db: AsyncSession, *, project_id: str) -> set[str]:
    contracts = (
        await db.execute(
            select(
                MemoirChapterContract.id,
                MemoirChapterContract.chapter_id,
                MemoirChapterContract.status,
                MemoirChapterContract.version,
            )
            .where(
                MemoirChapterContract.project_id == project_id,
                MemoirChapterContract.status.in_(
                    [
                        MemoirChapterContractStatus.CONFIRMED.value,
                        MemoirChapterContractStatus.DRAFT.value,
                    ]
                ),
            )
            .order_by(
                MemoirChapterContract.chapter_id,
                MemoirChapterContract.version.desc(),
                (
                    MemoirChapterContract.status
                    == MemoirChapterContractStatus.CONFIRMED.value
                ).desc(),
            )
        )
    ).all()
    current_contract_ids: dict[str, str] = {}
    for contract_id, chapter_id, _, _ in contracts:
        current_contract_ids.setdefault(chapter_id, contract_id)
    if not current_contract_ids:
        return set()
    return set(
        (
            await db.scalars(
                select(MemoirChapterNodeStory.story_id)
                .join(
                    MemoirChapterNode,
                    MemoirChapterNode.id == MemoirChapterNodeStory.node_id,
                )
                .where(
                    MemoirChapterNodeStory.project_id == project_id,
                    MemoirChapterNode.project_id == project_id,
                    MemoirChapterNode.contract_id.in_(tuple(current_contract_ids.values())),
                    or_(
                        MemoirChapterNodeStory.required.is_(True),
                        MemoirChapterNode.required.is_(True),
                    ),
                )
            )
        ).all()
    )


async def _sync_task_claims(
    db: AsyncSession,
    *,
    task: MemoirFollowupTask,
    claim_roles: dict[str, MemoirFollowupTaskClaimRole],
) -> None:
    await db.execute(
        delete(MemoirFollowupTaskClaim).where(
            MemoirFollowupTaskClaim.project_id == task.project_id,
            MemoirFollowupTaskClaim.task_id == task.id,
        )
    )
    db.add_all(
        MemoirFollowupTaskClaim(
            project_id=task.project_id,
            task_id=task.id,
            claim_id=claim_id,
            context_role=role.value,
        )
        for claim_id, role in sorted(claim_roles.items())
    )


async def generate_followup_tasks(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str | None = None,
) -> FollowupGenerationResult:
    gap_filters = [
        MemoirNarrativeGap.project_id == project_id,
        MemoirNarrativeGap.status == MemoirNarrativeGapStatus.OPEN.value,
    ]
    if chapter_id:
        gap_filters.append(
            or_(
                MemoirNarrativeGap.primary_chapter_id == chapter_id,
                MemoirNarrativeGap.chapters.any(chapter_id=chapter_id),
            )
        )
    gaps = list(
        (
            await db.scalars(
                select(MemoirNarrativeGap)
                .options(
                    selectinload(MemoirNarrativeGap.claims),
                    selectinload(MemoirNarrativeGap.stories),
                    selectinload(MemoirNarrativeGap.chapters),
                )
                .where(*gap_filters)
                .order_by(MemoirNarrativeGap.created_at, MemoirNarrativeGap.id)
            )
        ).unique().all()
    )
    story_ids = {_gap_story_id(gap) for gap in gaps} - {None}
    chapter_ids = {_gap_chapter_id(gap) for gap in gaps} - {None}
    stories = {
        story.id: story
        for story in (
            await db.scalars(
                select(MemoirStory).where(
                    MemoirStory.project_id == project_id,
                    MemoirStory.id.in_(story_ids),
                )
            )
        ).all()
    } if story_ids else {}
    chapters = {
        chapter.id: chapter
        for chapter in (
            await db.scalars(
                select(MemoirBookPlanChapter).where(
                    MemoirBookPlanChapter.project_id == project_id,
                    MemoirBookPlanChapter.id.in_(chapter_ids),
                    MemoirBookPlanChapter.status
                    == MemoirBookPlanChapterStatus.ACTIVE.value,
                )
            )
        ).all()
    } if chapter_ids else {}
    required_story_ids = await _required_story_ids(db, project_id=project_id)

    slot_claim_ids: dict[tuple[str, str], set[str]] = {}
    if story_ids:
        rows = (
            await db.execute(
                select(
                    MemoirStorySlot.story_id,
                    MemoirStorySlot.slot_key,
                    MemoirStorySlot.schema_version,
                    MemoirStorySlotClaim.claim_id,
                )
                .join(
                    MemoirStorySlotClaim,
                    MemoirStorySlotClaim.story_slot_id == MemoirStorySlot.id,
                )
                .where(
                    MemoirStorySlot.project_id == project_id,
                    MemoirStorySlot.story_id.in_(story_ids),
                    MemoirStorySlotClaim.project_id == project_id,
                    MemoirStorySlotClaim.evidence_role
                    == MemoirClaimEvidenceRole.SUPPORTS.value,
                )
            )
        ).all()
        for row in rows:
            story = stories.get(row.story_id)
            if story is None or row.schema_version != story.slot_schema_version:
                continue
            slot_claim_ids.setdefault((row.story_id, row.slot_key), set()).add(row.claim_id)

    all_claim_ids = {
        relation.claim_id
        for gap in gaps
        for relation in gap.claims
    }
    all_claim_ids.update(
        claim_id
        for claim_ids in slot_claim_ids.values()
        for claim_id in claim_ids
    )
    claims = {
        claim.id: claim
        for claim in (
            await db.scalars(
                select(MemoirClaim).where(
                    MemoirClaim.project_id == project_id,
                    MemoirClaim.id.in_(all_claim_ids),
                )
            )
        ).all()
    } if all_claim_ids else {}
    existing_tasks = {
        task.gap_id: task
        for task in (
            await db.scalars(
                select(MemoirFollowupTask).where(
                    MemoirFollowupTask.project_id == project_id,
                    MemoirFollowupTask.gap_id.in_([gap.id for gap in gaps]),
                )
            )
        ).all()
    } if gaps else {}

    created: list[str] = []
    updated: list[str] = []
    skipped: list[str] = []
    for gap in gaps:
        gap_type = MemoirNarrativeGapType(gap.gap_type)
        story_id = _gap_story_id(gap)
        story = stories.get(story_id or "")
        slot_key = _story_slot_key(gap, story)
        claim_roles: dict[str, MemoirFollowupTaskClaimRole] = {}
        for relation in gap.claims:
            claim = claims.get(relation.claim_id)
            if claim is None or not _safe_claim(claim):
                continue
            claim_roles[claim.id] = (
                MemoirFollowupTaskClaimRole.EXPECTED_EVIDENCE
                if relation.role == MemoirNarrativeGapClaimRole.UNSUPPORTED_CANDIDATE.value
                else MemoirFollowupTaskClaimRole.KNOWN
            )
        if story_id and slot_key:
            for claim_id in slot_claim_ids.get((story_id, slot_key), set()):
                claim = claims.get(claim_id)
                if claim is not None and _safe_claim(claim):
                    claim_roles.setdefault(claim_id, MemoirFollowupTaskClaimRole.DO_NOT_REPEAT)

        gap_chapter_id = _gap_chapter_id(gap)
        chapter = chapters.get(gap_chapter_id or "")
        actionable = gap_type not in NON_INTERVIEW_GAP_TYPES and (
            bool(story and slot_key)
            or bool(claim_roles)
            or (story is not None and gap_type != MemoirNarrativeGapType.MISSING_SLOT)
        )
        if gap_chapter_id and chapter is None:
            actionable = False
        if story is not None and (
            story.lifecycle_status != MemoirStoryLifecycleStatus.ACTIVE.value
            or story.verification_status == MemoirStoryVerificationStatus.REJECTED.value
            or story.publication_status != MemoirStoryPublicationStatus.ELIGIBLE.value
        ):
            actionable = False
        existing = existing_tasks.get(gap.id)
        if not actionable:
            skipped.append(gap.id)
            if existing and existing.status in {
                status.value for status in followup_tasks.ACTIVE_TASK_STATUSES
            }:
                await followup_tasks.transition_task(
                    db,
                    project_id=project_id,
                    task_id=existing.id,
                    status=MemoirFollowupTaskStatus.CANCELLED,
                    actor_type=MemoirNarrativeActorType.SYSTEM,
                    actor_user_id=None,
                    idempotency_key=f"planner-cancel:{FOLLOWUP_PLANNER_VERSION}:{gap.id}",
                    reason="gap is no longer eligible for interview follow-up",
                )
            continue
        if existing and existing.status in {
            status.value for status in followup_tasks.TERMINAL_TASK_STATUSES
        }:
            skipped.append(gap.id)
            continue

        related_claims = [claims[claim_id] for claim_id in claim_roles if claim_id in claims]
        values = {
            "chapter_id": chapter.id if chapter else None,
            "story_id": story.id if story else None,
            "target_slot": slot_key,
            "question": _task_question(gap, story, slot_key),
            "purpose": gap.description.strip() or gap.title.strip(),
            "known_context_summary": _known_context_summary(story, related_claims),
            "priority": _task_priority(
                gap,
                story_required=bool(story and story.id in required_story_ids),
                chapter_order=chapter.sort_order if chapter else None,
            ),
        }
        if existing is None:
            task = MemoirFollowupTask(
                project_id=project_id,
                gap_id=gap.id,
                status=MemoirFollowupTaskStatus.PENDING.value,
                created_by=MemoirNarrativeActorType.SYSTEM.value,
                **values,
            )
            db.add(task)
            await db.flush()
            created.append(task.id)
        elif existing.status == MemoirFollowupTaskStatus.IN_PROGRESS.value:
            skipped.append(gap.id)
            continue
        else:
            task = existing
            for key, value in values.items():
                setattr(task, key, value)
            task.updated_at = _utcnow()
            db.add(task)
            updated.append(task.id)
        await _sync_task_claims(db, task=task, claim_roles=claim_roles)
    await db.flush()
    return FollowupGenerationResult(tuple(created), tuple(updated), tuple(skipped))


async def _release_due_deferred_tasks(db: AsyncSession, *, project_id: str) -> None:
    now = _utcnow()
    tasks = list(
        (
            await db.scalars(
                select(MemoirFollowupTask).where(
                    MemoirFollowupTask.project_id == project_id,
                    MemoirFollowupTask.status == MemoirFollowupTaskStatus.DEFERRED.value,
                    MemoirFollowupTask.defer_until.is_not(None),
                    MemoirFollowupTask.defer_until <= now,
                )
            )
        ).all()
    )
    for task in tasks:
        await followup_tasks.transition_task(
            db,
            project_id=project_id,
            task_id=task.id,
            status=MemoirFollowupTaskStatus.PENDING,
            actor_type=MemoirNarrativeActorType.SYSTEM,
            actor_user_id=None,
            idempotency_key=f"followup-defer-expired:{task.id}:{task.defer_until.isoformat()}",
            reason="follow-up deferral period ended",
        )


async def _task_is_selectable(
    db: AsyncSession,
    *,
    task: MemoirFollowupTask,
) -> bool:
    gap = await db.scalar(
        select(MemoirNarrativeGap).where(
            MemoirNarrativeGap.project_id == task.project_id,
            MemoirNarrativeGap.id == task.gap_id,
            MemoirNarrativeGap.status == MemoirNarrativeGapStatus.OPEN.value,
        )
    )
    if gap is None:
        return False
    if task.story_id:
        story = await db.scalar(
            select(MemoirStory).where(
                MemoirStory.project_id == task.project_id,
                MemoirStory.id == task.story_id,
                MemoirStory.lifecycle_status == MemoirStoryLifecycleStatus.ACTIVE.value,
                MemoirStory.publication_status == MemoirStoryPublicationStatus.ELIGIBLE.value,
                MemoirStory.verification_status != MemoirStoryVerificationStatus.REJECTED.value,
            )
        )
        if story is None:
            return False
        if task.target_slot:
            slot_status = await db.scalar(
                select(MemoirStorySlot.status).where(
                    MemoirStorySlot.project_id == task.project_id,
                    MemoirStorySlot.story_id == task.story_id,
                    MemoirStorySlot.schema_version == story.slot_schema_version,
                    MemoirStorySlot.slot_key == task.target_slot,
                )
            )
            if slot_status in TERMINAL_SLOT_STATUSES:
                return False
    claim_ids = [relation.claim_id for relation in task.claims]
    if claim_ids:
        private_claim_exists = await db.scalar(
            select(MemoirClaim.id).where(
                MemoirClaim.project_id == task.project_id,
                MemoirClaim.id.in_(claim_ids),
                MemoirClaim.publication_status != MemoirClaimPublicationStatus.ELIGIBLE.value,
            ).limit(1)
        )
        if private_claim_exists:
            return False
    return True


async def select_next_task(
    db: AsyncSession,
    *,
    project_id: str,
    preferred_chapter_id: str | None = None,
    avoid_story_id: str | None = None,
) -> MemoirFollowupTask | None:
    await _release_due_deferred_tasks(db, project_id=project_id)
    candidates = list(
        (
            await db.scalars(
                select(MemoirFollowupTask)
                .options(*followup_tasks.task_load_options())
                .where(
                    MemoirFollowupTask.project_id == project_id,
                    MemoirFollowupTask.status == MemoirFollowupTaskStatus.PENDING.value,
                )
                .order_by(
                    MemoirFollowupTask.priority.desc(),
                    MemoirFollowupTask.created_at,
                    MemoirFollowupTask.id,
                )
            )
        ).unique().all()
    )
    eligible = [task for task in candidates if await _task_is_selectable(db, task=task)]
    eligible.sort(
        key=lambda task: (
            task.chapter_id != preferred_chapter_id if preferred_chapter_id else False,
            task.story_id == avoid_story_id if avoid_story_id else False,
            -task.priority,
            task.created_at,
            task.id,
        )
    )
    return eligible[0] if eligible else None


async def start_task_for_session(
    db: AsyncSession,
    *,
    task: MemoirFollowupTask,
    voice_session: MemoirVoiceSession,
    actor_user_id: str,
) -> MemoirFollowupTask:
    if task.project_id != voice_session.project_id:
        raise ValueError("follow-up task and voice session must belong to the same project")
    if voice_session.session_goal != MemoirSessionGoal.BOOK_COMPLETION.value:
        raise ValueError("follow-up task requires a book_completion voice session")
    if task.status == MemoirFollowupTaskStatus.DEFERRED.value:
        task = await followup_tasks.transition_task(
            db,
            project_id=task.project_id,
            task_id=task.id,
            status=MemoirFollowupTaskStatus.PENDING,
            actor_type=MemoirNarrativeActorType.USER,
            actor_user_id=actor_user_id,
            idempotency_key=f"followup-resume-for-session:{voice_session.id}:{task.id}",
            reason="user selected the deferred follow-up task",
        )
    if task.status != MemoirFollowupTaskStatus.PENDING.value:
        raise ValueError("follow-up task is not available")
    if not await _task_is_selectable(db, task=task):
        raise ValueError("follow-up task is no longer eligible")
    task = await followup_tasks.transition_task(
        db,
        project_id=task.project_id,
        task_id=task.id,
        status=MemoirFollowupTaskStatus.IN_PROGRESS,
        actor_type=MemoirNarrativeActorType.USER,
        actor_user_id=actor_user_id,
        idempotency_key=f"followup-start:{voice_session.id}:{task.id}",
        reason="follow-up task assigned to voice session",
        assigned_session_id=voice_session.id,
    )
    voice_session.current_followup_task_id = task.id
    db.add(voice_session)
    await db.flush()
    return task


async def release_session_task(
    db: AsyncSession,
    *,
    voice_session: MemoirVoiceSession,
    reason: str,
) -> MemoirFollowupTask | None:
    if not voice_session.current_followup_task_id:
        return None
    task = await followup_tasks.get_task(
        db,
        project_id=voice_session.project_id,
        task_id=voice_session.current_followup_task_id,
    )
    if task is None or task.status != MemoirFollowupTaskStatus.IN_PROGRESS.value:
        return task
    return await followup_tasks.transition_task(
        db,
        project_id=task.project_id,
        task_id=task.id,
        status=MemoirFollowupTaskStatus.DEFERRED,
        actor_type=MemoirNarrativeActorType.SYSTEM,
        actor_user_id=None,
        idempotency_key=f"followup-session-ended:{voice_session.id}:{task.id}",
        reason=reason,
        defer_until=_utcnow(),
    )


async def prompt_context_for_session(
    db: AsyncSession,
    *,
    voice_session: MemoirVoiceSession,
) -> FollowupPromptContext | None:
    if (
        voice_session.session_goal != MemoirSessionGoal.BOOK_COMPLETION.value
        or not voice_session.current_followup_task_id
    ):
        return None
    task = await followup_tasks.get_task(
        db,
        project_id=voice_session.project_id,
        task_id=voice_session.current_followup_task_id,
    )
    if (
        task is None
        or task.status != MemoirFollowupTaskStatus.IN_PROGRESS.value
        or task.assigned_session_id != voice_session.id
        or not await _task_is_selectable(db, task=task)
    ):
        return None
    claim_ids = [relation.claim_id for relation in task.claims]
    claims = list(
        (
            await db.scalars(
                select(MemoirClaim).where(
                    MemoirClaim.project_id == task.project_id,
                    MemoirClaim.id.in_(claim_ids),
                    MemoirClaim.superseded_by_claim_id.is_(None),
                    MemoirClaim.verification_status != MemoirClaimVerificationStatus.REJECTED.value,
                    MemoirClaim.publication_status == MemoirClaimPublicationStatus.ELIGIBLE.value,
                )
            )
        ).all()
    ) if claim_ids else []
    claims_by_id = {claim.id: claim for claim in claims}
    known = [
        claims_by_id[relation.claim_id].statement
        for relation in task.claims
        if relation.context_role == MemoirFollowupTaskClaimRole.KNOWN.value
        and relation.claim_id in claims_by_id
    ]
    do_not_repeat = [
        claims_by_id[relation.claim_id].statement
        for relation in task.claims
        if relation.context_role == MemoirFollowupTaskClaimRole.DO_NOT_REPEAT.value
        and relation.claim_id in claims_by_id
    ]
    lines = [
        f"当前补访任务 ID：{task.id}",
        f"本轮唯一目标：{task.purpose}",
        f"本轮具体问题：{task.question}",
    ]
    if known:
        lines.append("可承接的已知事实：" + "；".join(dict.fromkeys(known)))
    if do_not_repeat:
        lines.append("已经充分问过、不要重复追问：" + "；".join(dict.fromkeys(do_not_repeat)))
    lines.append("只能围绕这一项补访；一次只问一个问题，不得编造未给出的事实。")
    return FollowupPromptContext(
        task_id=task.id,
        question=task.question,
        prompt_text="\n".join(lines),
    )
