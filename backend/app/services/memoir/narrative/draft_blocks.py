from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import (
    MemoirDraftBlockEditorialStatus,
    MemoirDraftBlockFreshnessStatus,
    MemoirDraftBlockRole,
    MemoirNarrativeActorType,
    MemoirNarrativeDecisionAction,
    MemoirNarrativeDecisionTargetType,
)
from app.models.memoir import MemoirBookPlanChapter
from app.models.memoir_narrative import (
    MemoirChapterNode,
    MemoirClaim,
    MemoirDraftBlock,
    MemoirDraftBlockClaim,
    MemoirDraftBlockStory,
    MemoirDraftBlockVersion,
    MemoirStoryClaim,
    MemoirStoryRevision,
)
from app.schemas.memoir_narrative import (
    MemoirDraftBlockClaimSource,
    MemoirDraftBlockStorySource,
)
from app.services.memoir.narrative import decision_log, draft_block_queries


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _block_state(block: MemoirDraftBlock) -> dict[str, object]:
    return {
        "current_version_id": block.current_version_id,
        "editorial_status": block.editorial_status,
        "freshness_status": block.freshness_status,
        "locked": block.locked,
    }


async def _validate_block_node(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    node_id: str,
) -> None:
    node_exists = await db.scalar(
        select(MemoirChapterNode.id).where(
            MemoirChapterNode.project_id == project_id,
            MemoirChapterNode.chapter_id == chapter_id,
            MemoirChapterNode.id == node_id,
        )
    )
    if node_exists is None:
        raise ValueError("draft block node does not belong to the project chapter")


async def _validate_sources(
    db: AsyncSession,
    *,
    project_id: str,
    block_role: MemoirDraftBlockRole,
    claims: Sequence[MemoirDraftBlockClaimSource],
    stories: Sequence[MemoirDraftBlockStorySource],
) -> None:
    claim_ids = [item.claim_id for item in claims]
    story_ids = [item.story_id for item in stories]
    if len(set(claim_ids)) != len(claim_ids):
        raise ValueError("draft block claim sources must be unique")
    if len(set(story_ids)) != len(story_ids):
        raise ValueError("draft block story sources must be unique")
    if block_role == MemoirDraftBlockRole.FACTUAL and (not claim_ids or not story_ids):
        raise ValueError("factual draft block requires direct story and claim sources")

    if claim_ids:
        existing_claim_ids = set(
            (
                await db.scalars(
                    select(MemoirClaim.id).where(
                        MemoirClaim.project_id == project_id,
                        MemoirClaim.id.in_(claim_ids),
                    )
                )
            ).all()
        )
        if existing_claim_ids != set(claim_ids):
            raise ValueError("draft block claims must belong to the same project")
    if stories:
        revision_pairs = set(
            (
                await db.execute(
                    select(MemoirStoryRevision.story_id, MemoirStoryRevision.id).where(
                        MemoirStoryRevision.project_id == project_id,
                        MemoirStoryRevision.story_id.in_(story_ids),
                        MemoirStoryRevision.id.in_(
                            [item.story_revision_id for item in stories]
                        ),
                    )
                )
            ).all()
        )
        requested_pairs = {
            (item.story_id, item.story_revision_id) for item in stories
        }
        if revision_pairs != requested_pairs:
            raise ValueError(
                "draft block story revisions must belong to their project stories"
            )
    if claim_ids and story_ids:
        linked_claim_ids = set(
            (
                await db.scalars(
                    select(MemoirStoryClaim.claim_id).where(
                        MemoirStoryClaim.project_id == project_id,
                        MemoirStoryClaim.story_id.in_(story_ids),
                        MemoirStoryClaim.claim_id.in_(claim_ids),
                    )
                )
            ).all()
        )
        if linked_claim_ids != set(claim_ids):
            raise ValueError("draft block claims must be linked to a referenced story")


def _add_source_links(
    db: AsyncSession,
    *,
    project_id: str,
    version_id: str,
    claims: Sequence[MemoirDraftBlockClaimSource],
    stories: Sequence[MemoirDraftBlockStorySource],
) -> None:
    db.add_all(
        [
            MemoirDraftBlockClaim(
                project_id=project_id,
                block_version_id=version_id,
                claim_id=item.claim_id,
                usage_role=item.usage_role.value,
            )
            for item in claims
        ]
        + [
            MemoirDraftBlockStory(
                project_id=project_id,
                block_version_id=version_id,
                story_id=item.story_id,
                story_revision_id=item.story_revision_id,
                usage_role=item.usage_role.value,
            )
            for item in stories
        ]
    )


async def create_block(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    node_id: str,
    block_role: MemoirDraftBlockRole,
    sort_order: int,
    content: str,
    created_by: MemoirNarrativeActorType,
    claims: Sequence[MemoirDraftBlockClaimSource],
    stories: Sequence[MemoirDraftBlockStorySource],
    model: str | None = None,
    prompt_snapshot: str = "",
    feedback: str | None = None,
) -> MemoirDraftBlock:
    content = content.strip()
    if not content:
        raise ValueError("draft block content cannot be empty")
    if sort_order < 0:
        raise ValueError("draft block sort order cannot be negative")
    chapter_exists = await db.scalar(
        select(MemoirBookPlanChapter.id).where(
            MemoirBookPlanChapter.project_id == project_id,
            MemoirBookPlanChapter.id == chapter_id,
        )
    )
    if chapter_exists is None:
        raise ValueError("chapter does not belong to the project")
    await _validate_block_node(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        node_id=node_id,
    )
    await _validate_sources(
        db,
        project_id=project_id,
        block_role=block_role,
        claims=claims,
        stories=stories,
    )

    block_id = str(uuid4())
    version_id = str(uuid4())
    now = _utcnow()
    block = MemoirDraftBlock(
        id=block_id,
        project_id=project_id,
        chapter_id=chapter_id,
        node_id=node_id,
        block_role=block_role.value,
        sort_order=sort_order,
        editorial_status=MemoirDraftBlockEditorialStatus.DRAFT.value,
        freshness_status=MemoirDraftBlockFreshnessStatus.CURRENT.value,
        locked=False,
        current_version_id=None,
        created_at=now,
        updated_at=now,
    )
    version = MemoirDraftBlockVersion(
        id=version_id,
        project_id=project_id,
        block_id=block_id,
        version=1,
        content=content,
        created_by=created_by.value,
        model=model,
        prompt_snapshot=prompt_snapshot,
        feedback=feedback,
        created_at=now,
    )
    db.add_all([block, version])
    await db.flush()
    _add_source_links(
        db,
        project_id=project_id,
        version_id=version_id,
        claims=claims,
        stories=stories,
    )
    await db.flush()
    block.current_version_id = version_id
    db.add(block)
    await db.flush()
    return block


async def _current_sources(
    db: AsyncSession,
    *,
    block: MemoirDraftBlock,
) -> tuple[list[MemoirDraftBlockClaimSource], list[MemoirDraftBlockStorySource]]:
    claims_by_version, stories_by_version = await draft_block_queries.load_version_sources(
        db,
        project_id=block.project_id,
        version_ids=[str(block.current_version_id)],
    )
    return (
        claims_by_version.get(str(block.current_version_id), []),
        stories_by_version.get(str(block.current_version_id), []),
    )


async def append_version(
    db: AsyncSession,
    *,
    block: MemoirDraftBlock,
    expected_current_version_id: str,
    content: str,
    created_by: MemoirNarrativeActorType,
    claims: Sequence[MemoirDraftBlockClaimSource],
    stories: Sequence[MemoirDraftBlockStorySource],
    model: str | None = None,
    prompt_snapshot: str = "",
    feedback: str | None = None,
    sources_refreshed: bool,
) -> MemoirDraftBlockVersion:
    content = content.strip()
    if not content:
        raise ValueError("draft block content cannot be empty")
    if block.current_version_id != expected_current_version_id:
        raise ValueError("draft block changed concurrently; reload and retry")
    if created_by != MemoirNarrativeActorType.USER and block.locked:
        raise ValueError("locked draft block cannot be modified by an automatic task")
    block_role = MemoirDraftBlockRole(block.block_role)
    await _validate_sources(
        db,
        project_id=block.project_id,
        block_role=block_role,
        claims=claims,
        stories=stories,
    )
    latest_version = await db.scalar(
        select(func.max(MemoirDraftBlockVersion.version)).where(
            MemoirDraftBlockVersion.project_id == block.project_id,
            MemoirDraftBlockVersion.block_id == block.id,
        )
    )
    version = MemoirDraftBlockVersion(
        project_id=block.project_id,
        block_id=block.id,
        version=int(latest_version or 0) + 1,
        content=content,
        created_by=created_by.value,
        model=model,
        prompt_snapshot=prompt_snapshot,
        feedback=feedback,
    )
    db.add(version)
    await db.flush()
    _add_source_links(
        db,
        project_id=block.project_id,
        version_id=version.id,
        claims=claims,
        stories=stories,
    )
    await db.flush()
    block.current_version_id = version.id
    block.editorial_status = MemoirDraftBlockEditorialStatus.DRAFT.value
    if sources_refreshed:
        block.freshness_status = MemoirDraftBlockFreshnessStatus.CURRENT.value
    block.updated_at = _utcnow()
    db.add(block)
    await db.flush()
    return version


async def manually_edit_block(
    db: AsyncSession,
    *,
    project_id: str,
    block_id: str,
    expected_current_version_id: str,
    content: str,
    feedback: str | None,
) -> MemoirDraftBlock:
    block = await draft_block_queries.load_block(db, project_id=project_id, block_id=block_id)
    claims, stories = await _current_sources(db, block=block)
    await append_version(
        db,
        block=block,
        expected_current_version_id=expected_current_version_id,
        content=content,
        created_by=MemoirNarrativeActorType.USER,
        claims=claims,
        stories=stories,
        feedback=feedback,
        sources_refreshed=False,
    )
    return block


async def confirm_block(
    db: AsyncSession,
    *,
    project_id: str,
    block_id: str,
    expected_current_version_id: str,
    actor_user_id: str,
    idempotency_key: str,
    reason: str,
) -> MemoirDraftBlock:
    existing = await decision_log.existing_decision(
        db,
        project_id=project_id,
        idempotency_key=idempotency_key,
        target_type=MemoirNarrativeDecisionTargetType.DRAFT_BLOCK,
        target_id=block_id,
        action=MemoirNarrativeDecisionAction.CONFIRM,
        actor_type=MemoirNarrativeActorType.USER,
        actor_user_id=actor_user_id,
    )
    block = await draft_block_queries.load_block(db, project_id=project_id, block_id=block_id)
    if existing is not None:
        return block
    if block.current_version_id != expected_current_version_id:
        raise ValueError("draft block changed concurrently; reload and retry")
    if block.editorial_status == MemoirDraftBlockEditorialStatus.CONFIRMED.value:
        raise ValueError("draft block is already confirmed")
    before = _block_state(block)
    block.editorial_status = MemoirDraftBlockEditorialStatus.CONFIRMED.value
    block.updated_at = _utcnow()
    db.add(block)
    await decision_log.record_decision(
        db,
        project_id=project_id,
        target_type=MemoirNarrativeDecisionTargetType.DRAFT_BLOCK,
        target_id=block.id,
        action=MemoirNarrativeDecisionAction.CONFIRM,
        actor_type=MemoirNarrativeActorType.USER,
        actor_user_id=actor_user_id,
        before_state=before,
        after_state=_block_state(block),
        reason=reason,
        idempotency_key=idempotency_key,
    )
    return block


async def set_block_locked(
    db: AsyncSession,
    *,
    project_id: str,
    block_id: str,
    expected_current_version_id: str,
    locked: bool,
    actor_user_id: str,
    idempotency_key: str,
    reason: str,
) -> MemoirDraftBlock:
    action = (
        MemoirNarrativeDecisionAction.LOCK
        if locked
        else MemoirNarrativeDecisionAction.UNLOCK
    )
    existing = await decision_log.existing_decision(
        db,
        project_id=project_id,
        idempotency_key=idempotency_key,
        target_type=MemoirNarrativeDecisionTargetType.DRAFT_BLOCK,
        target_id=block_id,
        action=action,
        actor_type=MemoirNarrativeActorType.USER,
        actor_user_id=actor_user_id,
    )
    block = await draft_block_queries.load_block(db, project_id=project_id, block_id=block_id)
    if existing is not None:
        return block
    if block.current_version_id != expected_current_version_id:
        raise ValueError("draft block changed concurrently; reload and retry")
    if block.locked == locked:
        raise ValueError(f"draft block is already {'locked' if locked else 'unlocked'}")
    before = _block_state(block)
    block.locked = locked
    block.updated_at = _utcnow()
    db.add(block)
    await decision_log.record_decision(
        db,
        project_id=project_id,
        target_type=MemoirNarrativeDecisionTargetType.DRAFT_BLOCK,
        target_id=block.id,
        action=action,
        actor_type=MemoirNarrativeActorType.USER,
        actor_user_id=actor_user_id,
        before_state=before,
        after_state=_block_state(block),
        reason=reason,
        idempotency_key=idempotency_key,
    )
    return block


async def mark_blocks_stale_for_claim(
    db: AsyncSession,
    *,
    project_id: str,
    claim_id: str,
) -> int:
    dependent_versions = select(MemoirDraftBlockClaim.block_version_id).where(
        MemoirDraftBlockClaim.project_id == project_id,
        MemoirDraftBlockClaim.claim_id == claim_id,
    )
    result = await db.execute(
        update(MemoirDraftBlock)
        .where(
            MemoirDraftBlock.project_id == project_id,
            MemoirDraftBlock.current_version_id.in_(dependent_versions),
            MemoirDraftBlock.freshness_status
            == MemoirDraftBlockFreshnessStatus.CURRENT.value,
        )
        .values(
            freshness_status=MemoirDraftBlockFreshnessStatus.STALE.value,
            updated_at=_utcnow(),
        )
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)


async def mark_chapter_blocks_stale(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_ids: set[str],
) -> int:
    if not chapter_ids:
        return 0
    result = await db.execute(
        update(MemoirDraftBlock)
        .where(
            MemoirDraftBlock.project_id == project_id,
            MemoirDraftBlock.chapter_id.in_(chapter_ids),
            MemoirDraftBlock.freshness_status
            == MemoirDraftBlockFreshnessStatus.CURRENT.value,
        )
        .values(
            freshness_status=MemoirDraftBlockFreshnessStatus.STALE.value,
            updated_at=_utcnow(),
        )
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)


async def mark_blocks_stale_for_editorial_context(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    story_id: str | None,
) -> int:
    conditions = [
        MemoirDraftBlock.project_id == project_id,
        MemoirDraftBlock.chapter_id == chapter_id,
        MemoirDraftBlock.freshness_status
        == MemoirDraftBlockFreshnessStatus.CURRENT.value,
    ]
    if story_id:
        dependent_versions = select(MemoirDraftBlockStory.block_version_id).where(
            MemoirDraftBlockStory.project_id == project_id,
            MemoirDraftBlockStory.story_id == story_id,
        )
        conditions.append(
            MemoirDraftBlock.current_version_id.in_(dependent_versions)
        )
    result = await db.execute(
        update(MemoirDraftBlock)
        .where(*conditions)
        .values(
            freshness_status=MemoirDraftBlockFreshnessStatus.STALE.value,
            updated_at=_utcnow(),
        )
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)


async def mark_blocks_stale_for_story_revision(
    db: AsyncSession,
    *,
    project_id: str,
    story_id: str,
    current_story_revision_id: str,
) -> int:
    outdated_versions = select(MemoirDraftBlockStory.block_version_id).where(
        MemoirDraftBlockStory.project_id == project_id,
        MemoirDraftBlockStory.story_id == story_id,
        MemoirDraftBlockStory.story_revision_id != current_story_revision_id,
    )
    result = await db.execute(
        update(MemoirDraftBlock)
        .where(
            MemoirDraftBlock.project_id == project_id,
            MemoirDraftBlock.current_version_id.in_(outdated_versions),
            MemoirDraftBlock.freshness_status
            == MemoirDraftBlockFreshnessStatus.CURRENT.value,
        )
        .values(
            freshness_status=MemoirDraftBlockFreshnessStatus.STALE.value,
            updated_at=_utcnow(),
        )
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)
