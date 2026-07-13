from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import (
    MemoirNarrativeActorType,
    MemoirNarrativeEntityType,
    MemoirNarrativeEntityVerificationStatus,
)
from app.models.memoir_narrative import MemoirNarrativeEntity
from app.models.projects import Project
from app.repositories.memoir_narrative import MemoirNarrativeEntityRepository
from app.schemas.memoir_narrative import MemoirNarrativeEntityCreate
from app.schemas.memoir_narrative.extraction import MemoirNarrativeExtractedEntity


def narrator_entity_id(project_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"pawtale:memoir:narrator:{project_id}"))


def extracted_entity_id(project_id: str, candidate_identity: str) -> str:
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"pawtale:memoir:extracted-entity:{project_id}:{candidate_identity}",
        )
    )


def memoir_profile(project: Project) -> dict[str, object]:
    metadata = project.metadata_json if isinstance(project.metadata_json, dict) else {}
    profile = metadata.get("memoir_profile")
    return profile if isinstance(profile, dict) else {}


async def ensure_narrator_entity(
    db: AsyncSession,
    *,
    project: Project,
) -> MemoirNarrativeEntity:
    entity_id = narrator_entity_id(project.id)
    existing = await db.scalar(
        select(MemoirNarrativeEntity).where(
            MemoirNarrativeEntity.project_id == project.id,
            MemoirNarrativeEntity.id == entity_id,
        )
    )
    if existing is not None:
        return existing

    profile = memoir_profile(project)
    narrator_name = str(profile.get("narrator_name") or "").strip() or "讲述者"
    entity = MemoirNarrativeEntity(
        id=entity_id,
        project_id=project.id,
        entity_type=MemoirNarrativeEntityType.PERSON.value,
        canonical_name=narrator_name,
        aliases_json=["我", "讲述者"],
        description="项目回忆录的叙述者",
        verification_status=MemoirNarrativeEntityVerificationStatus.UNREVIEWED.value,
        created_by=MemoirNarrativeActorType.SYSTEM.value,
        updated_by=MemoirNarrativeActorType.SYSTEM.value,
    )
    db.add(entity)
    await db.flush()
    return entity


async def resolve_extracted_entity(
    db: AsyncSession,
    *,
    project: Project,
    candidate: MemoirNarrativeExtractedEntity,
    candidate_identity: str,
) -> tuple[MemoirNarrativeEntity, tuple[str, ...]]:
    if candidate.is_narrator:
        return await ensure_narrator_entity(db, project=project), ()

    stable_candidate_id = extracted_entity_id(project.id, candidate_identity)
    existing_candidate = await db.scalar(
        select(MemoirNarrativeEntity).where(
            MemoirNarrativeEntity.project_id == project.id,
            MemoirNarrativeEntity.id == stable_candidate_id,
        )
    )
    if existing_candidate is not None:
        return existing_candidate, ()

    matches = list(
        (
            await db.scalars(
                select(MemoirNarrativeEntity).where(
                    MemoirNarrativeEntity.project_id == project.id,
                    MemoirNarrativeEntity.entity_type == candidate.entity_type.value,
                    MemoirNarrativeEntity.canonical_name == candidate.canonical_name,
                    MemoirNarrativeEntity.merged_into_entity_id.is_(None),
                    MemoirNarrativeEntity.verification_status
                    != MemoirNarrativeEntityVerificationStatus.REJECTED.value,
                )
            )
        ).all()
    )
    confirmed = [
        entity
        for entity in matches
        if entity.verification_status == MemoirNarrativeEntityVerificationStatus.CONFIRMED.value
    ]
    if len(confirmed) == 1:
        return confirmed[0], ()

    verification_status = (
        MemoirNarrativeEntityVerificationStatus.NEEDS_VERIFICATION
        if matches
        else MemoirNarrativeEntityVerificationStatus.UNREVIEWED
    )
    entity = await MemoirNarrativeEntityRepository(db).create(
        MemoirNarrativeEntityCreate(
            project_id=project.id,
            entity_type=candidate.entity_type,
            canonical_name=candidate.canonical_name,
            aliases=candidate.aliases,
            description=candidate.description,
            verification_status=verification_status,
            created_by=MemoirNarrativeActorType.AI,
        ),
        entity_id=stable_candidate_id,
    )
    return entity, tuple(match.id for match in matches)
