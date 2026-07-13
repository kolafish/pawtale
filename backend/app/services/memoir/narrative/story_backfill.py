from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import (
    MemoirClaimEvidenceRole,
    MemoirClaimType,
    MemoirNarrativeActorType,
    MemoirStoryNarrativeRole,
    MemoirStoryRevisionChangeType,
    MemoirStoryType,
)
from app.domain.memoir.types import MemoirSemanticCacheStatus
from app.models.memoir import MemoirChapterEventClusterSnapshot, MemoirSourceCard
from app.models.memoir_narrative import (
    MemoirClaim,
    MemoirClaimEvidence,
    MemoirStory,
    MemoirStoryClaim,
)
from app.schemas.memoir_narrative import MemoirStoryClaimInput
from app.schemas.memoir_narrative.extraction import (
    MemoirNarrativeExtractedStory,
    MemoirNarrativeExtractedStoryClaim,
)
from app.services.memoir.narrative import stories, story_decisions, story_matching


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


def _fingerprint(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def _cluster_items(snapshot: MemoirChapterEventClusterSnapshot) -> list[dict[str, object]]:
    return [item for item in snapshot.clusters_json if isinstance(item, dict)]


async def _latest_cluster_snapshots(
    db: AsyncSession,
    *,
    project_id: str | None,
) -> list[MemoirChapterEventClusterSnapshot]:
    statement = (
        select(MemoirChapterEventClusterSnapshot)
        .where(MemoirChapterEventClusterSnapshot.status == MemoirSemanticCacheStatus.READY.value)
        .order_by(
            MemoirChapterEventClusterSnapshot.updated_at.desc(),
            MemoirChapterEventClusterSnapshot.id.desc(),
        )
    )
    if project_id:
        statement = statement.where(MemoirChapterEventClusterSnapshot.project_id == project_id)
    snapshots = list((await db.scalars(statement)).all())
    latest: dict[tuple[str, str], MemoirChapterEventClusterSnapshot] = {}
    for snapshot in snapshots:
        latest.setdefault((snapshot.project_id, snapshot.chapter_id), snapshot)
    return list(latest.values())


async def _latest_source_cards(
    db: AsyncSession,
    *,
    project_id: str | None,
) -> list[MemoirSourceCard]:
    statement = (
        select(MemoirSourceCard)
        .where(MemoirSourceCard.status == MemoirSemanticCacheStatus.READY.value)
        .order_by(MemoirSourceCard.updated_at.desc(), MemoirSourceCard.id.desc())
    )
    if project_id:
        statement = statement.where(MemoirSourceCard.project_id == project_id)
    cards = list((await db.scalars(statement)).all())
    latest: dict[tuple[str, str], MemoirSourceCard] = {}
    for card in cards:
        latest.setdefault((card.project_id, card.utterance_id), card)
    return list(latest.values())


async def _claims_by_utterance_ids(
    db: AsyncSession,
    *,
    utterance_ids: set[str],
) -> tuple[dict[str, list[MemoirClaim]], dict[str, MemoirClaim]]:
    if not utterance_ids:
        return {}, {}
    rows = (
        await db.execute(
            select(MemoirClaimEvidence.utterance_id, MemoirClaim)
            .join(
                MemoirClaim,
                (MemoirClaim.id == MemoirClaimEvidence.claim_id)
                & (MemoirClaim.project_id == MemoirClaimEvidence.project_id),
            )
            .where(
                MemoirClaimEvidence.utterance_id.in_(utterance_ids),
                MemoirClaimEvidence.evidence_role == MemoirClaimEvidenceRole.SUPPORTS.value,
                MemoirClaim.superseded_by_claim_id.is_(None),
                MemoirClaim.verification_status != "rejected",
            )
        )
    ).all()
    by_utterance: dict[str, list[MemoirClaim]] = {}
    by_id: dict[str, MemoirClaim] = {}
    seen: set[tuple[str, str]] = set()
    for utterance_id, claim in rows:
        if not utterance_id or (utterance_id, claim.id) in seen:
            continue
        seen.add((utterance_id, claim.id))
        by_utterance.setdefault(utterance_id, []).append(claim)
        by_id[claim.id] = claim
    return by_utterance, by_id


async def _existing_story_ids_for_claims(
    db: AsyncSession,
    *,
    project_id: str,
    claim_ids: tuple[str, ...],
) -> tuple[str, ...]:
    if not claim_ids:
        return ()
    return tuple(
        dict.fromkeys(
            (
                await db.scalars(
                    select(MemoirStoryClaim.story_id)
                    .join(MemoirStory, MemoirStory.id == MemoirStoryClaim.story_id)
                    .where(
                        MemoirStoryClaim.project_id == project_id,
                        MemoirStoryClaim.claim_id.in_(claim_ids),
                        MemoirStory.project_id == project_id,
                        MemoirStory.lifecycle_status == "active",
                    )
                )
            ).all()
        )
    )


async def collect_story_backfill_candidates(
    db: AsyncSession,
    *,
    project_id: str | None = None,
) -> list[StoryBackfillCandidate]:
    snapshots = await _latest_cluster_snapshots(db, project_id=project_id)
    cards = await _latest_source_cards(db, project_id=project_id)
    utterance_ids = {
        source_id
        for snapshot in snapshots
        for item in _cluster_items(snapshot)
        for source_id in item.get("source_ids", [])
        if isinstance(source_id, str) and source_id
    } | {card.utterance_id for card in cards}
    claims_by_utterance, _claims_by_id = await _claims_by_utterance_ids(
        db,
        utterance_ids=utterance_ids,
    )
    candidates: list[StoryBackfillCandidate] = []
    event_cluster_source_ids: set[str] = set()
    for snapshot in snapshots:
        for index, item in enumerate(_cluster_items(snapshot)):
            source_ids = tuple(
                dict.fromkeys(
                    source_id
                    for source_id in item.get("source_ids", [])
                    if isinstance(source_id, str) and source_id
                )
            )
            claim_ids = tuple(
                dict.fromkeys(
                    claim.id
                    for source_id in source_ids
                    for claim in claims_by_utterance.get(source_id, [])
                    if claim.project_id == snapshot.project_id
                )
            )
            if not claim_ids:
                continue
            event_cluster_source_ids.update(source_ids)
            title = str(item.get("title") or "").strip() or "待确认故事"
            summary = str(item.get("summary") or "").strip()
            fingerprint = _fingerprint(
                {
                    "origin": "event_cluster",
                    "project_id": snapshot.project_id,
                    "title": title,
                    "claim_ids": sorted(claim_ids),
                }
            )
            candidates.append(
                StoryBackfillCandidate(
                    fingerprint=fingerprint,
                    project_id=snapshot.project_id,
                    origin=f"event_cluster:{snapshot.id}:{index}",
                    title=title,
                    summary=summary,
                    story_type=MemoirStoryType.EVENT,
                    claim_ids=claim_ids,
                    existing_story_ids=await _existing_story_ids_for_claims(
                        db,
                        project_id=snapshot.project_id,
                        claim_ids=claim_ids,
                    ),
                )
            )

    grouped_cards: dict[tuple[str, str], list[MemoirSourceCard]] = {}
    for card in cards:
        if card.utterance_id in event_cluster_source_ids:
            continue
        tags = card.card_json.get("event_tags", []) if isinstance(card.card_json, dict) else []
        tag = next((str(item).strip() for item in tags if str(item).strip()), "")
        if tag:
            grouped_cards.setdefault((card.project_id, tag), []).append(card)
    for (current_project_id, tag), grouped in grouped_cards.items():
        if len({card.utterance_id for card in grouped}) < 2:
            continue
        claim_ids = tuple(
            dict.fromkeys(
                claim.id
                for card in grouped
                for claim in claims_by_utterance.get(card.utterance_id, [])
                if claim.project_id == current_project_id
            )
        )
        if len(claim_ids) < 2:
            continue
        summaries = [
            str(card.card_json.get("summary") or "").strip()
            for card in grouped
            if isinstance(card.card_json, dict) and str(card.card_json.get("summary") or "").strip()
        ]
        fingerprint = _fingerprint(
            {
                "origin": "source_card_group",
                "project_id": current_project_id,
                "tag": tag,
                "claim_ids": sorted(claim_ids),
            }
        )
        candidates.append(
            StoryBackfillCandidate(
                fingerprint=fingerprint,
                project_id=current_project_id,
                origin=f"source_card_group:{tag}",
                title=tag,
                summary=" ".join(dict.fromkeys(summaries))[:4000],
                story_type=MemoirStoryType.THEME,
                claim_ids=claim_ids,
                existing_story_ids=await _existing_story_ids_for_claims(
                    db,
                    project_id=current_project_id,
                    claim_ids=claim_ids,
                ),
            )
        )
    return candidates


def _event_claim_mapping(claim: MemoirClaim, *, index: int) -> MemoirStoryClaimInput:
    if claim.claim_type == MemoirClaimType.REFLECTION.value:
        role = MemoirStoryNarrativeRole.REFLECTION
        slot_keys = ["reflection"]
        is_core = False
    elif claim.claim_type == MemoirClaimType.RELATIONSHIP.value:
        role = MemoirStoryNarrativeRole.CONTEXT
        slot_keys = ["people"]
        is_core = True
    elif claim.claim_type == MemoirClaimType.CHRONOLOGY.value:
        role = MemoirStoryNarrativeRole.TRIGGER
        slot_keys = ["trigger"]
        is_core = True
    elif claim.claim_type == MemoirClaimType.ATTRIBUTE.value:
        role = MemoirStoryNarrativeRole.BACKGROUND
        slot_keys = ["background"]
        is_core = False
    else:
        role = MemoirStoryNarrativeRole.ACTION
        slot_keys = ["actions"]
        is_core = True
    return MemoirStoryClaimInput(
        claim_id=claim.id,
        narrative_role=role,
        sort_order=index,
        is_core=is_core,
        slot_keys=slot_keys,
    )


async def run_story_backfill(
    db: AsyncSession,
    *,
    project_id: str | None = None,
    dry_run: bool = True,
    limit: int | None = None,
) -> dict[str, object]:
    candidates = await collect_story_backfill_candidates(db, project_id=project_id)
    if limit is not None:
        candidates = candidates[: max(0, limit)]
    created_story_ids: list[str] = []
    reused_story_ids: list[str] = []
    if not dry_run:
        all_claim_ids = {claim_id for candidate in candidates for claim_id in candidate.claim_ids}
        claims_by_id = {
            claim.id: claim
            for claim in (
                await db.scalars(select(MemoirClaim).where(MemoirClaim.id.in_(all_claim_ids)))
            ).all()
        }
        for candidate in candidates:
            claim_ids = {claim_id for claim_id in candidate.claim_ids if claim_id in claims_by_id}
            explicit_story_id = (
                candidate.existing_story_ids[0]
                if len(candidate.existing_story_ids) == 1
                else None
            )
            extracted = MemoirNarrativeExtractedStory(
                key=f"backfill_{candidate.fingerprint[:16]}",
                title=candidate.title,
                story_type=candidate.story_type,
                summary=candidate.summary,
                existing_story_id=explicit_story_id,
                match_confidence=1.0 if explicit_story_id else 0.0,
                claims=[
                    MemoirNarrativeExtractedStoryClaim(
                        claim_key=f"claim_{index}",
                        narrative_role=MemoirStoryNarrativeRole.CONTEXT,
                        slot_keys=(
                            ["supporting_cases"]
                            if candidate.story_type == MemoirStoryType.THEME
                            else ["actions"]
                        ),
                        is_core=True,
                    )
                    for index, _claim_id in enumerate(candidate.claim_ids)
                ],
            )
            story, review_ids, created = await story_matching.resolve_extracted_story(
                db,
                project_id=candidate.project_id,
                transcript_version_id=f"backfill:{candidate.fingerprint}",
                candidate=extracted,
                primary_place_entity_id=None,
                claim_ids=claim_ids,
                force_needs_verification=True,
                created_by=MemoirNarrativeActorType.SYSTEM,
                creation_change_type=MemoirStoryRevisionChangeType.BACKFILL_CANDIDATE,
                creation_reason=f"candidate seeded from {candidate.origin}",
            )
            if created:
                created_story_ids.append(story.id)
            else:
                reused_story_ids.append(story.id)
            claim_inputs = (
                [
                    MemoirStoryClaimInput(
                        claim_id=claim_id,
                        narrative_role=MemoirStoryNarrativeRole.CONTEXT,
                        sort_order=index,
                        is_core=True,
                        slot_keys=(
                            ["theme_definition", "supporting_cases"]
                            if index == 0
                            else ["supporting_cases"]
                        ),
                    )
                    for index, claim_id in enumerate(candidate.claim_ids)
                ]
                if candidate.story_type == MemoirStoryType.THEME
                else [
                    _event_claim_mapping(claims_by_id[claim_id], index=index)
                    for index, claim_id in enumerate(candidate.claim_ids)
                ]
            )
            await stories.attach_claims_to_story(
                db,
                story=story,
                claims=claim_inputs,
                actor_type=MemoirNarrativeActorType.SYSTEM,
                actor_user_id=None,
                reason=f"candidate seeded from {candidate.origin}",
            )
            ambiguous_existing_ids = (
                candidate.existing_story_ids
                if len(candidate.existing_story_ids) > 1
                else ()
            )
            review_candidates = tuple(
                dict.fromkeys([*ambiguous_existing_ids, *review_ids])
            )
            if created or review_candidates:
                await story_decisions.mark_story_needs_review(
                    db,
                    story=story,
                    candidate_story_ids=review_candidates,
                    reason="legacy semantic snapshot is only a Story candidate",
                    idempotency_key=f"story-backfill-review:{candidate.fingerprint}",
                )
        await db.commit()
    return {
        "dry_run": dry_run,
        "project_id": project_id,
        "candidate_count": len(candidates),
        "event_cluster_candidate_count": sum(
            candidate.origin.startswith("event_cluster:") for candidate in candidates
        ),
        "source_card_candidate_count": sum(
            candidate.origin.startswith("source_card_group:") for candidate in candidates
        ),
        "created_story_count": len(created_story_ids),
        "reused_story_count": len(reused_story_ids),
        "created_story_ids": created_story_ids,
        "reused_story_ids": reused_story_ids,
        "candidates": [
            {
                "fingerprint": candidate.fingerprint,
                "project_id": candidate.project_id,
                "origin": candidate.origin,
                "title": candidate.title,
                "claim_count": len(candidate.claim_ids),
                "existing_story_ids": list(candidate.existing_story_ids),
            }
            for candidate in candidates
        ],
    }
