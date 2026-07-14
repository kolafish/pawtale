from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import (
    MemoirClaimEvidenceRole,
    MemoirClaimPublicationStatus,
    MemoirClaimType,
    MemoirNarrativeEntityType,
    MemoirStoryType,
)
from app.models.memoir_narrative import (
    MemoirClaim,
    MemoirClaimEvidence,
    MemoirNarrativeEntity,
    MemoirStory,
    MemoirStoryClaim,
)
from app.services.memoir.narrative.entities import narrator_entity_id


NARRATIVE_STORY_CLUSTER_CLAIM_TYPES = {
    MemoirClaimType.EVENT.value,
    MemoirClaimType.RELATIONSHIP.value,
    MemoirClaimType.CHRONOLOGY.value,
    MemoirClaimType.REFLECTION.value,
}


@dataclass(frozen=True)
class StoryBackfillCandidate:
    fingerprint: str
    project_id: str
    origin: str
    title: str
    summary: str
    story_type: MemoirStoryType
    claim_ids: tuple[str, ...]
    existing_story_ids: tuple[str, ...]
    primary_place_entity_id: str | None = None


def story_backfill_fingerprint(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(encoded.encode("utf-8")).hexdigest()


def _claim_anchors(
    claim: MemoirClaim,
    *,
    entities_by_key: dict[tuple[str, str], MemoirNarrativeEntity],
) -> set[tuple[str, str]]:
    anchors: set[tuple[str, str]] = set()
    narrator_id = narrator_entity_id(claim.project_id)
    for entity_id in (claim.subject_entity_id, claim.object_entity_id):
        if not entity_id or entity_id == narrator_id:
            continue
        entity = entities_by_key.get((claim.project_id, entity_id))
        if entity and entity.entity_type == MemoirNarrativeEntityType.PERSON.value:
            anchors.add(("person", entity_id))
    if claim.place_entity_id:
        place = entities_by_key.get((claim.project_id, claim.place_entity_id))
        if place and place.entity_type == MemoirNarrativeEntityType.PLACE.value:
            anchors.add(("place", place.id))
    return anchors


def _connected_claim_components(
    claim_ids: tuple[str, ...],
    *,
    anchors_by_claim: dict[str, set[tuple[str, str]]],
) -> list[tuple[str, ...]]:
    members_by_anchor: dict[tuple[str, str], set[str]] = {}
    for claim_id in claim_ids:
        for anchor in anchors_by_claim.get(claim_id, set()):
            members_by_anchor.setdefault(anchor, set()).add(claim_id)
    neighbors = {claim_id: set() for claim_id in claim_ids}
    for members in members_by_anchor.values():
        for claim_id in members:
            neighbors[claim_id].update(members - {claim_id})

    components: list[tuple[str, ...]] = []
    remaining = set(claim_ids)
    order = {claim_id: index for index, claim_id in enumerate(claim_ids)}
    while remaining:
        seed = min(remaining, key=order.__getitem__)
        pending = [seed]
        component: set[str] = set()
        while pending:
            claim_id = pending.pop()
            if claim_id in component:
                continue
            component.add(claim_id)
            pending.extend(neighbors[claim_id] - component)
        remaining -= component
        components.append(tuple(sorted(component, key=order.__getitem__)))
    return components


def _story_type_for_claim_cluster(
    claims: list[MemoirClaim],
    *,
    shared_anchors: list[tuple[str, str]],
) -> MemoirStoryType:
    claim_types = {claim.claim_type for claim in claims}
    if (
        MemoirClaimType.RELATIONSHIP.value in claim_types
        and any(anchor_type == "person" for anchor_type, _anchor_id in shared_anchors)
    ):
        return MemoirStoryType.RELATIONSHIP
    if claim_types & {MemoirClaimType.EVENT.value, MemoirClaimType.CHRONOLOGY.value}:
        return MemoirStoryType.EVENT
    return MemoirStoryType.THEME


def _primary_cluster_anchor(
    *,
    story_type: MemoirStoryType,
    anchor_counts: Counter[tuple[str, str]],
    entities_by_key: dict[tuple[str, str], MemoirNarrativeEntity],
    project_id: str,
) -> tuple[str, str]:
    shared_anchors = [anchor for anchor, count in anchor_counts.items() if count >= 2]
    preferred_types = (
        ("person", "place")
        if story_type == MemoirStoryType.RELATIONSHIP
        else ("place", "person")
    )
    rank = {anchor_type: index for index, anchor_type in enumerate(preferred_types)}
    return min(
        shared_anchors,
        key=lambda anchor: (
            rank[anchor[0]],
            -anchor_counts[anchor],
            entities_by_key[(project_id, anchor[1])].canonical_name,
            anchor[1],
        ),
    )


def _cluster_title(
    *,
    story_type: MemoirStoryType,
    anchor_type: str,
    anchor_name: str,
) -> str:
    if story_type == MemoirStoryType.RELATIONSHIP:
        return f"与{anchor_name}的关系"
    if story_type == MemoirStoryType.EVENT:
        return (
            f"{anchor_name}的经历"
            if anchor_type == "place"
            else f"与{anchor_name}有关的经历"
        )
    return f"关于{anchor_name}的回忆"


async def collect_claim_anchor_cluster_candidates(
    db: AsyncSession,
    *,
    project_id: str | None,
    covered_claims: set[tuple[str, str]],
) -> list[StoryBackfillCandidate]:
    statement = (
        select(MemoirClaimEvidence.transcript_version_id, MemoirClaim)
        .join(
            MemoirClaim,
            (MemoirClaim.id == MemoirClaimEvidence.claim_id)
            & (MemoirClaim.project_id == MemoirClaimEvidence.project_id),
        )
        .where(
            MemoirClaimEvidence.transcript_version_id.is_not(None),
            MemoirClaimEvidence.evidence_role == MemoirClaimEvidenceRole.SUPPORTS.value,
            MemoirClaim.superseded_by_claim_id.is_(None),
            MemoirClaim.verification_status != "rejected",
            MemoirClaim.publication_status == MemoirClaimPublicationStatus.ELIGIBLE.value,
        )
        .order_by(
            MemoirClaim.project_id,
            MemoirClaimEvidence.transcript_version_id,
            MemoirClaim.created_at,
            MemoirClaim.id,
        )
    )
    if project_id:
        statement = statement.where(MemoirClaim.project_id == project_id)
    rows = (await db.execute(statement)).all()
    claims_by_source: dict[tuple[str, str], dict[str, MemoirClaim]] = {}
    for transcript_version_id, claim in rows:
        if not transcript_version_id:
            continue
        claims_by_source.setdefault(
            (claim.project_id, transcript_version_id), {}
        ).setdefault(claim.id, claim)
    if not claims_by_source:
        return []

    all_claims = [
        claim
        for claims_by_id in claims_by_source.values()
        for claim in claims_by_id.values()
    ]
    entity_ids = {
        entity_id
        for claim in all_claims
        for entity_id in (
            claim.subject_entity_id,
            claim.object_entity_id,
            claim.place_entity_id,
        )
        if entity_id
    }
    entities_by_key = {
        (entity.project_id, entity.id): entity
        for entity in (
            await db.scalars(
                select(MemoirNarrativeEntity).where(
                    MemoirNarrativeEntity.id.in_(entity_ids)
                )
            )
        ).all()
    }
    claim_ids = {claim.id for claim in all_claims}
    story_ids_by_claim: dict[tuple[str, str], set[str]] = {}
    if claim_ids:
        story_rows = (
            await db.execute(
                select(
                    MemoirStoryClaim.project_id,
                    MemoirStoryClaim.claim_id,
                    MemoirStoryClaim.story_id,
                )
                .join(
                    MemoirStory,
                    (MemoirStory.id == MemoirStoryClaim.story_id)
                    & (MemoirStory.project_id == MemoirStoryClaim.project_id),
                )
                .where(
                    MemoirStoryClaim.claim_id.in_(claim_ids),
                    MemoirStory.lifecycle_status == "active",
                )
            )
        ).all()
        for current_project_id, claim_id, story_id in story_rows:
            story_ids_by_claim.setdefault(
                (current_project_id, claim_id), set()
            ).add(story_id)

    candidates: list[StoryBackfillCandidate] = []
    for (current_project_id, transcript_version_id), claims_by_id in claims_by_source.items():
        ordered_claims = tuple(claims_by_id)
        anchors_by_claim = {
            claim_id: _claim_anchors(
                claims_by_id[claim_id],
                entities_by_key=entities_by_key,
            )
            for claim_id in ordered_claims
        }
        for component_ids in _connected_claim_components(
            ordered_claims,
            anchors_by_claim=anchors_by_claim,
        ):
            if len(component_ids) < 2:
                continue
            unlinked_claim_ids = {
                claim_id
                for claim_id in component_ids
                if not story_ids_by_claim.get((current_project_id, claim_id))
            }
            if not unlinked_claim_ids:
                continue
            if all(
                (current_project_id, claim_id) in covered_claims
                for claim_id in unlinked_claim_ids
            ):
                continue
            component_claims = [claims_by_id[claim_id] for claim_id in component_ids]
            if not any(
                claim.claim_type in NARRATIVE_STORY_CLUSTER_CLAIM_TYPES
                for claim in component_claims
            ):
                continue
            anchor_counts = Counter(
                anchor
                for claim_id in component_ids
                for anchor in anchors_by_claim[claim_id]
            )
            shared_anchors = [
                anchor for anchor, count in anchor_counts.items() if count >= 2
            ]
            if not shared_anchors:
                continue
            story_type = _story_type_for_claim_cluster(
                component_claims,
                shared_anchors=shared_anchors,
            )
            anchor_type, anchor_id = _primary_cluster_anchor(
                story_type=story_type,
                anchor_counts=anchor_counts,
                entities_by_key=entities_by_key,
                project_id=current_project_id,
            )
            anchor = entities_by_key[(current_project_id, anchor_id)]
            existing_story_ids = tuple(
                sorted(
                    {
                        story_id
                        for claim_id in component_ids
                        for story_id in story_ids_by_claim.get(
                            (current_project_id, claim_id), set()
                        )
                    }
                )
            )
            fingerprint = story_backfill_fingerprint(
                {
                    "origin": "claim_anchor_cluster",
                    "project_id": current_project_id,
                    "transcript_version_id": transcript_version_id,
                    "claim_ids": sorted(component_ids),
                }
            )
            candidates.append(
                StoryBackfillCandidate(
                    fingerprint=fingerprint,
                    project_id=current_project_id,
                    origin=(
                        f"claim_anchor_cluster:{transcript_version_id}:"
                        f"{anchor_type}:{anchor_id}"
                    ),
                    title=_cluster_title(
                        story_type=story_type,
                        anchor_type=anchor_type,
                        anchor_name=anchor.canonical_name,
                    ),
                    summary=" ".join(
                        dict.fromkeys(claim.statement for claim in component_claims)
                    )[:4000],
                    story_type=story_type,
                    claim_ids=component_ids,
                    existing_story_ids=existing_story_ids,
                    primary_place_entity_id=(
                        anchor_id if anchor_type == "place" else None
                    ),
                )
            )
    return candidates
