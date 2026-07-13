from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import (
    MemoirClaimEvidenceRole,
    MemoirClaimEvidenceSourceType,
    MemoirClaimEvidenceSpeakerRole,
    MemoirClaimVerificationStatus,
    MemoirNarrativeActorType,
)
from app.domain.memoir.types import (
    MemoirChatMessageRole,
)
from app.models.memoir import (
    MemoirTranscriptVersion,
    MemoirVoiceUtterance,
)
from app.models.memoir_narrative import MemoirClaim, MemoirClaimEvidence
from app.models.projects import Project
from app.repositories.memoir_narrative import MemoirClaimRepository
from app.schemas.memoir_narrative import (
    MemoirClaimCreate,
    MemoirClaimEvidenceCreate,
    MemoirNarrativeExtraction,
)
from app.schemas.memoir_narrative.extraction import MemoirNarrativeExtractedClaim
from app.services.ai_types import AITextClient
from app.services.memoir.narrative import decisions, entities
from app.services.memoir.narrative.extraction import extract_narrative_state


NARRATIVE_INGESTION_ENGINE = "memoir_narrative_ingestion_v1"
NARRATIVE_EXTRACTION_SCHEMA_VERSION = "1"


@dataclass(frozen=True)
class NarrativeIngestionResult:
    created_claim_ids: tuple[str, ...]
    reused_claim_ids: tuple[str, ...]
    created_evidence_ids: tuple[str, ...]
    skipped_claim_keys: tuple[str, ...]
    entity_review_candidates: dict[str, tuple[str, ...]]
    provider: str | None
    model: str | None


def transcript_text_hash(content: str) -> str:
    return sha256(content.encode("utf-8")).hexdigest()


def _speaker_role(utterance: MemoirVoiceUtterance) -> MemoirClaimEvidenceSpeakerRole:
    if utterance.chat_message and utterance.chat_message.role == MemoirChatMessageRole.INTERVIEWER.value:
        return MemoirClaimEvidenceSpeakerRole.INTERVIEWER
    return MemoirClaimEvidenceSpeakerRole.NARRATOR


def _valid_claims(
    extraction: MemoirNarrativeExtraction,
    *,
    source_text: str,
) -> tuple[list[tuple[MemoirNarrativeExtractedClaim, tuple[str, ...]]], tuple[str, ...]]:
    valid: list[tuple[MemoirNarrativeExtractedClaim, tuple[str, ...]]] = []
    skipped: list[str] = []
    for claim in extraction.claims:
        quotes = tuple(quote for quote in claim.evidence_quotes if quote in source_text)
        if not quotes:
            skipped.append(claim.key)
            continue
        valid.append((claim, quotes))
    return valid, tuple(skipped)


def _optional_equals(column, value):
    return column.is_(None) if value is None else column == value


async def _find_matching_claim(
    db: AsyncSession,
    *,
    project_id: str,
    candidate: MemoirNarrativeExtractedClaim,
    entity_ids: dict[str, str],
) -> MemoirClaim | None:
    subject_id = entity_ids.get(candidate.subject_entity_key or "")
    object_id = entity_ids.get(candidate.object_entity_key or "")
    place_id = entity_ids.get(candidate.place_entity_key or "")
    statement = select(MemoirClaim).where(
        MemoirClaim.project_id == project_id,
        MemoirClaim.statement == candidate.statement,
        MemoirClaim.claim_type == candidate.claim_type.value,
        MemoirClaim.predicate_code == candidate.predicate_code.value,
        _optional_equals(MemoirClaim.subject_entity_id, subject_id),
        _optional_equals(MemoirClaim.object_entity_id, object_id),
        _optional_equals(MemoirClaim.place_entity_id, place_id),
        _optional_equals(MemoirClaim.time_text, candidate.time_text),
        _optional_equals(MemoirClaim.time_start, candidate.time_start),
        _optional_equals(MemoirClaim.time_end, candidate.time_end),
        MemoirClaim.time_precision == candidate.time_precision.value,
        MemoirClaim.certainty == candidate.certainty.value,
        _optional_equals(MemoirClaim.object_text, candidate.object_text),
        MemoirClaim.superseded_by_claim_id.is_(None),
        MemoirClaim.verification_status != MemoirClaimVerificationStatus.REJECTED.value,
    )
    return await db.scalar(statement.order_by(MemoirClaim.created_at, MemoirClaim.id).limit(1))


async def _add_transcript_evidence(
    db: AsyncSession,
    *,
    claim: MemoirClaim,
    utterance: MemoirVoiceUtterance,
    version: MemoirTranscriptVersion,
    quote: str,
    source_hash: str,
    speaker_role: MemoirClaimEvidenceSpeakerRole,
    speaker_entity_id: str | None,
) -> MemoirClaimEvidence | None:
    existing = await db.scalar(
        select(MemoirClaimEvidence).where(
            MemoirClaimEvidence.project_id == claim.project_id,
            MemoirClaimEvidence.claim_id == claim.id,
            MemoirClaimEvidence.transcript_version_id == version.id,
            MemoirClaimEvidence.evidence_role == MemoirClaimEvidenceRole.SUPPORTS.value,
            MemoirClaimEvidence.quote == quote,
        )
    )
    if existing is not None:
        return None
    start_ms = utterance.start_ms
    end_ms = utterance.end_ms
    if start_ms is None or end_ms is None or start_ms < 0 or end_ms <= start_ms:
        start_ms = None
        end_ms = None
    return await MemoirClaimRepository(db).add_evidence(
        MemoirClaimEvidenceCreate(
            project_id=claim.project_id,
            claim_id=claim.id,
            evidence_role=MemoirClaimEvidenceRole.SUPPORTS,
            evidence_source_type=MemoirClaimEvidenceSourceType.TRANSCRIPT,
            utterance_id=utterance.id,
            transcript_version_id=version.id,
            audio_file_id=utterance.audio_file.id if utterance.audio_file else None,
            speaker_entity_id=speaker_entity_id,
            speaker_role=speaker_role,
            start_ms=start_ms,
            end_ms=end_ms,
            quote=quote,
            source_text_hash=source_hash,
            created_by=MemoirNarrativeActorType.AI,
        )
    )


async def ingest_transcript_version(
    db: AsyncSession,
    *,
    project: Project,
    utterance: MemoirVoiceUtterance,
    version: MemoirTranscriptVersion,
    ai_model: str | None = None,
    ai_client: AITextClient | None = None,
) -> NarrativeIngestionResult:
    source_text = version.content
    speaker_role = _speaker_role(utterance)
    profile = entities.memoir_profile(project)
    extraction, provider, model = await extract_narrative_state(
        source_text=source_text,
        speaker_role=speaker_role,
        narrator_name=str(profile.get("narrator_name") or "").strip(),
        narrator_origin=str(profile.get("narrator_origin") or "").strip(),
        ai_model=ai_model,
        ai_client=ai_client,
    )
    valid_claims, skipped_keys = _valid_claims(extraction, source_text=source_text)
    needed_entity_keys = {
        key
        for candidate, _quotes in valid_claims
        for key in (
            candidate.subject_entity_key,
            candidate.object_entity_key,
            candidate.place_entity_key,
        )
        if key
    }
    extracted_entities = {candidate.key: candidate for candidate in extraction.entities}
    entity_ids: dict[str, str] = {}
    entity_review_candidates: dict[str, tuple[str, ...]] = {}
    for key in sorted(needed_entity_keys):
        entity, review_candidates = await entities.resolve_extracted_entity(
            db,
            project=project,
            candidate=extracted_entities[key],
            candidate_identity=f"{version.id}:{key}",
        )
        entity_ids[key] = entity.id
        if review_candidates:
            entity_review_candidates[entity.id] = review_candidates

    narrator = None
    if valid_claims and speaker_role == MemoirClaimEvidenceSpeakerRole.NARRATOR:
        narrator = await entities.ensure_narrator_entity(db, project=project)

    repository = MemoirClaimRepository(db)
    created_claim_ids: list[str] = []
    reused_claim_ids: list[str] = []
    created_evidence_ids: list[str] = []
    touched_claim_ids: set[str] = set()
    source_hash = transcript_text_hash(source_text)
    for candidate, quotes in valid_claims:
        claim = await _find_matching_claim(
            db,
            project_id=project.id,
            candidate=candidate,
            entity_ids=entity_ids,
        )
        if claim is None:
            claim = await repository.create(
                MemoirClaimCreate(
                    project_id=project.id,
                    claim_type=candidate.claim_type,
                    statement=candidate.statement,
                    subject_entity_id=entity_ids.get(candidate.subject_entity_key or ""),
                    predicate_code=candidate.predicate_code,
                    object_entity_id=entity_ids.get(candidate.object_entity_key or ""),
                    object_text=candidate.object_text,
                    place_entity_id=entity_ids.get(candidate.place_entity_key or ""),
                    time_text=candidate.time_text,
                    time_start=candidate.time_start,
                    time_end=candidate.time_end,
                    time_precision=candidate.time_precision,
                    certainty=candidate.certainty,
                    verification_status=(
                        MemoirClaimVerificationStatus.NEEDS_VERIFICATION
                        if speaker_role == MemoirClaimEvidenceSpeakerRole.INTERVIEWER
                        else MemoirClaimVerificationStatus.UNREVIEWED
                    ),
                    created_by=MemoirNarrativeActorType.AI,
                )
            )
            created_claim_ids.append(claim.id)
        else:
            reused_claim_ids.append(claim.id)
        touched_claim_ids.add(claim.id)
        for quote in quotes:
            evidence = await _add_transcript_evidence(
                db,
                claim=claim,
                utterance=utterance,
                version=version,
                quote=quote,
                source_hash=source_hash,
                speaker_role=speaker_role,
                speaker_entity_id=narrator.id if narrator else None,
            )
            if evidence is not None:
                created_evidence_ids.append(evidence.id)

    prior_claims = list(
        (
            await db.scalars(
                select(MemoirClaim)
                .join(MemoirClaimEvidence, MemoirClaimEvidence.claim_id == MemoirClaim.id)
                .where(
                    MemoirClaim.project_id == project.id,
                    MemoirClaimEvidence.project_id == project.id,
                    MemoirClaimEvidence.utterance_id == utterance.id,
                    MemoirClaimEvidence.transcript_version_id != version.id,
                    MemoirClaim.superseded_by_claim_id.is_(None),
                    MemoirClaim.verification_status.notin_(
                        [
                            MemoirClaimVerificationStatus.REJECTED.value,
                            MemoirClaimVerificationStatus.NEEDS_VERIFICATION.value,
                        ]
                    ),
                )
                .distinct()
            )
        ).all()
    )
    for claim in prior_claims:
        if claim.id in touched_claim_ids:
            continue
        await decisions.mark_claim_needs_review(
            db,
            claim=claim,
            reason="effective transcript version changed and no longer supports this claim",
            idempotency_key=f"transcript-review:{claim.id}:{version.id}",
        )

    return NarrativeIngestionResult(
        created_claim_ids=tuple(created_claim_ids),
        reused_claim_ids=tuple(dict.fromkeys(reused_claim_ids)),
        created_evidence_ids=tuple(created_evidence_ids),
        skipped_claim_keys=skipped_keys,
        entity_review_candidates=entity_review_candidates,
        provider=provider,
        model=model,
    )
