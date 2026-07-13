from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import (
    MemoirClaimPublicationStatus,
    MemoirClaimVerificationStatus,
    MemoirNarrativeEntityType,
    MemoirNarrativeEntityVerificationStatus,
)
from app.models.memoir_narrative import MemoirClaim, MemoirNarrativeEntity
from app.repositories.memoir_narrative import (
    MemoirClaimRepository,
    MemoirNarrativeEntityRepository,
)


async def list_entities(
    db: AsyncSession,
    *,
    project_id: str,
    entity_type: MemoirNarrativeEntityType | None,
    verification_status: MemoirNarrativeEntityVerificationStatus | None,
    limit: int,
    offset: int,
) -> list[MemoirNarrativeEntity]:
    return await MemoirNarrativeEntityRepository(db).list_for_project(
        project_id=project_id,
        entity_type=entity_type,
        verification_status=verification_status,
        limit=limit,
        offset=offset,
    )


async def list_claims(
    db: AsyncSession,
    *,
    project_id: str,
    verification_status: MemoirClaimVerificationStatus | None,
    publication_status: MemoirClaimPublicationStatus | None,
    limit: int,
    offset: int,
) -> list[MemoirClaim]:
    return await MemoirClaimRepository(db).list_for_project(
        project_id=project_id,
        verification_status=verification_status,
        publication_status=publication_status,
        limit=limit,
        offset=offset,
    )


async def get_claim(
    db: AsyncSession,
    *,
    project_id: str,
    claim_id: str,
) -> MemoirClaim | None:
    return await MemoirClaimRepository(db).get(
        project_id=project_id,
        claim_id=claim_id,
        include_evidence=True,
    )
