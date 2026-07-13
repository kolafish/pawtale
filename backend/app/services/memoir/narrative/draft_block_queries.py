from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memoir import MemoirBookPlanChapter
from app.models.memoir_narrative import (
    MemoirDraftBlock,
    MemoirDraftBlockClaim,
    MemoirDraftBlockStory,
    MemoirDraftBlockVersion,
)
from app.schemas.memoir_narrative import (
    MemoirDraftBlockClaimSource,
    MemoirDraftBlockListResponse,
    MemoirDraftBlockResponse,
    MemoirDraftBlockStorySource,
    MemoirDraftBlockVersionResponse,
)


async def load_block(
    db: AsyncSession,
    *,
    project_id: str,
    block_id: str,
) -> MemoirDraftBlock:
    block = await db.scalar(
        select(MemoirDraftBlock).where(
            MemoirDraftBlock.project_id == project_id,
            MemoirDraftBlock.id == block_id,
        )
    )
    if block is None:
        raise ValueError("draft block does not belong to the project")
    if block.current_version_id is None:
        raise ValueError("draft block has no current version")
    return block


async def load_version_sources(
    db: AsyncSession,
    *,
    project_id: str,
    version_ids: Sequence[str],
) -> tuple[
    dict[str, list[MemoirDraftBlockClaimSource]],
    dict[str, list[MemoirDraftBlockStorySource]],
]:
    if not version_ids:
        return {}, {}
    claim_links = list(
        (
            await db.scalars(
                select(MemoirDraftBlockClaim)
                .where(
                    MemoirDraftBlockClaim.project_id == project_id,
                    MemoirDraftBlockClaim.block_version_id.in_(version_ids),
                )
                .order_by(
                    MemoirDraftBlockClaim.block_version_id,
                    MemoirDraftBlockClaim.claim_id,
                )
            )
        ).all()
    )
    story_links = list(
        (
            await db.scalars(
                select(MemoirDraftBlockStory)
                .where(
                    MemoirDraftBlockStory.project_id == project_id,
                    MemoirDraftBlockStory.block_version_id.in_(version_ids),
                )
                .order_by(
                    MemoirDraftBlockStory.block_version_id,
                    MemoirDraftBlockStory.story_id,
                )
            )
        ).all()
    )
    claims_by_version: dict[str, list[MemoirDraftBlockClaimSource]] = {}
    for link in claim_links:
        claims_by_version.setdefault(link.block_version_id, []).append(
            MemoirDraftBlockClaimSource(
                claim_id=link.claim_id,
                usage_role=link.usage_role,
            )
        )
    stories_by_version: dict[str, list[MemoirDraftBlockStorySource]] = {}
    for link in story_links:
        stories_by_version.setdefault(link.block_version_id, []).append(
            MemoirDraftBlockStorySource(
                story_id=link.story_id,
                story_revision_id=link.story_revision_id,
                usage_role=link.usage_role,
            )
        )
    return claims_by_version, stories_by_version


def _version_response(
    version: MemoirDraftBlockVersion,
    *,
    claims: list[MemoirDraftBlockClaimSource],
    stories: list[MemoirDraftBlockStorySource],
) -> MemoirDraftBlockVersionResponse:
    return MemoirDraftBlockVersionResponse(
        id=version.id,
        project_id=version.project_id,
        block_id=version.block_id,
        version=version.version,
        content=version.content,
        created_by=version.created_by,
        model=version.model,
        prompt_snapshot=version.prompt_snapshot,
        feedback=version.feedback,
        legacy_draft_version_id=version.legacy_draft_version_id,
        claims=claims,
        stories=stories,
        created_at=version.created_at,
    )


async def _block_responses(
    db: AsyncSession,
    *,
    project_id: str,
    blocks: Sequence[MemoirDraftBlock],
) -> list[MemoirDraftBlockResponse]:
    if not blocks:
        return []
    if any(block.current_version_id is None for block in blocks):
        raise ValueError("draft block has no current version")
    current_version_ids = [str(block.current_version_id) for block in blocks]
    versions = list(
        (
            await db.scalars(
                select(MemoirDraftBlockVersion).where(
                    MemoirDraftBlockVersion.project_id == project_id,
                    MemoirDraftBlockVersion.id.in_(current_version_ids),
                )
            )
        ).all()
    )
    versions_by_id = {version.id: version for version in versions}
    if set(versions_by_id) != set(current_version_ids):
        raise ValueError("draft block current version is missing")
    claims_by_version, stories_by_version = await load_version_sources(
        db,
        project_id=project_id,
        version_ids=current_version_ids,
    )
    counts = dict(
        (
            await db.execute(
                select(
                    MemoirDraftBlockVersion.block_id,
                    func.count(MemoirDraftBlockVersion.id),
                )
                .where(
                    MemoirDraftBlockVersion.project_id == project_id,
                    MemoirDraftBlockVersion.block_id.in_([block.id for block in blocks]),
                )
                .group_by(MemoirDraftBlockVersion.block_id)
            )
        ).all()
    )
    responses: list[MemoirDraftBlockResponse] = []
    for block in blocks:
        version = versions_by_id[str(block.current_version_id)]
        responses.append(
            MemoirDraftBlockResponse(
                id=block.id,
                project_id=block.project_id,
                chapter_id=block.chapter_id,
                node_id=block.node_id,
                block_role=block.block_role,
                sort_order=block.sort_order,
                editorial_status=block.editorial_status,
                freshness_status=block.freshness_status,
                locked=block.locked,
                current_version_id=version.id,
                legacy_draft_id=block.legacy_draft_id,
                current_version=_version_response(
                    version,
                    claims=claims_by_version.get(version.id, []),
                    stories=stories_by_version.get(version.id, []),
                ),
                version_count=int(counts.get(block.id, 0)),
                created_at=block.created_at,
                updated_at=block.updated_at,
            )
        )
    return responses


async def block_response(
    db: AsyncSession,
    *,
    project_id: str,
    block_id: str,
) -> MemoirDraftBlockResponse:
    block = await load_block(db, project_id=project_id, block_id=block_id)
    return (await _block_responses(db, project_id=project_id, blocks=[block]))[0]


async def list_chapter_blocks(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> MemoirDraftBlockListResponse:
    chapter_exists = await db.scalar(
        select(MemoirBookPlanChapter.id).where(
            MemoirBookPlanChapter.project_id == project_id,
            MemoirBookPlanChapter.id == chapter_id,
        )
    )
    if chapter_exists is None:
        raise ValueError("chapter does not belong to the project")
    blocks = list(
        (
            await db.scalars(
                select(MemoirDraftBlock)
                .where(
                    MemoirDraftBlock.project_id == project_id,
                    MemoirDraftBlock.chapter_id == chapter_id,
                )
                .order_by(MemoirDraftBlock.sort_order, MemoirDraftBlock.id)
            )
        ).all()
    )
    return MemoirDraftBlockListResponse(
        chapter_id=chapter_id,
        blocks=await _block_responses(db, project_id=project_id, blocks=blocks),
    )


async def list_block_versions(
    db: AsyncSession,
    *,
    project_id: str,
    block_id: str,
) -> list[MemoirDraftBlockVersionResponse]:
    await load_block(db, project_id=project_id, block_id=block_id)
    versions = list(
        (
            await db.scalars(
                select(MemoirDraftBlockVersion)
                .where(
                    MemoirDraftBlockVersion.project_id == project_id,
                    MemoirDraftBlockVersion.block_id == block_id,
                )
                .order_by(MemoirDraftBlockVersion.version.desc())
            )
        ).all()
    )
    claims_by_version, stories_by_version = await load_version_sources(
        db,
        project_id=project_id,
        version_ids=[version.id for version in versions],
    )
    return [
        _version_response(
            version,
            claims=claims_by_version.get(version.id, []),
            stories=stories_by_version.get(version.id, []),
        )
        for version in versions
    ]
