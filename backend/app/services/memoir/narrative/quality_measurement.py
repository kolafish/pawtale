from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import re

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import (
    MemoirClaimEvidenceRole,
    MemoirDraftBlockEditorialStatus,
    MemoirDraftBlockFreshnessStatus,
    MemoirDraftBlockRole,
    MemoirNarrativeGapSeverity,
    MemoirNarrativeGapStatus,
)
from app.models.memoir import MemoirBookChapterDraftVersion
from app.models.memoir_narrative import (
    MemoirClaimEvidence,
    MemoirDraftBlock,
    MemoirDraftBlockClaim,
    MemoirDraftBlockVersion,
    MemoirNarrativeGap,
    MemoirNarrativeGapChapter,
)
from app.schemas.memoir_narrative import MemoirDraftBlockAssemblyItem
from app.services.memoir.narrative.quality_artifact import (
    QUALITY_GAP_DIMENSION_BY_TYPE,
    QUALITY_MEASUREMENT_ENGINE,
    measurement_artifact_id,
    validate_measurement_artifact,
)
from app.services.memoir.narrative.quality_evaluation import candidate_content_sha256


_SENTENCE_PATTERN = re.compile(r"[^。！？!?]+[。！？!?]?[”’」』】）)]*")


def split_factual_sentences(content: str) -> list[str]:
    return [
        match.group(0).strip()
        for match in _SENTENCE_PATTERN.finditer(content)
        if match.group(0).strip()
    ]


def _snapshot(version: MemoirBookChapterDraftVersion) -> list[MemoirDraftBlockAssemblyItem]:
    raw = version.block_snapshot_json
    if not isinstance(raw, list) or not raw:
        raise ValueError("quality candidate requires a structured DraftBlock snapshot")
    try:
        snapshot = [MemoirDraftBlockAssemblyItem.model_validate(item) for item in raw]
    except (TypeError, ValueError) as exc:
        raise ValueError("quality candidate contains an invalid DraftBlock snapshot") from exc
    if any(item.block_role == MemoirDraftBlockRole.LEGACY_SNAPSHOT for item in snapshot):
        raise ValueError("quality candidate cannot use a legacy DraftBlock snapshot")
    if any(
        item.editorial_status != MemoirDraftBlockEditorialStatus.CONFIRMED
        or item.freshness_status != MemoirDraftBlockFreshnessStatus.CURRENT
        for item in snapshot
    ):
        raise ValueError("quality candidate requires confirmed and current DraftBlocks")
    block_ids = [item.block_id for item in snapshot]
    version_ids = [item.block_version_id for item in snapshot]
    if len(block_ids) != len(set(block_ids)) or len(version_ids) != len(set(version_ids)):
        raise ValueError("quality candidate contains duplicate DraftBlock references")
    return snapshot


async def _load_chapter_version(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    version_id: str,
) -> MemoirBookChapterDraftVersion:
    version = await db.scalar(
        select(MemoirBookChapterDraftVersion).where(
            MemoirBookChapterDraftVersion.project_id == project_id,
            MemoirBookChapterDraftVersion.chapter_id == chapter_id,
            MemoirBookChapterDraftVersion.id == version_id,
        )
    )
    if version is None:
        raise ValueError("chapter draft version does not belong to the project chapter")
    return version


async def _snapshot_rows(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    snapshot: list[MemoirDraftBlockAssemblyItem],
) -> dict[str, tuple[MemoirDraftBlock, MemoirDraftBlockVersion]]:
    version_ids = [item.block_version_id for item in snapshot]
    rows = list(
        (
            await db.execute(
                select(MemoirDraftBlock, MemoirDraftBlockVersion)
                .join(
                    MemoirDraftBlockVersion,
                    MemoirDraftBlockVersion.block_id == MemoirDraftBlock.id,
                )
                .where(
                    MemoirDraftBlock.project_id == project_id,
                    MemoirDraftBlock.chapter_id == chapter_id,
                    MemoirDraftBlockVersion.project_id == project_id,
                    MemoirDraftBlockVersion.id.in_(version_ids),
                )
            )
        ).tuples().all()
    )
    by_version = {version.id: (block, version) for block, version in rows}
    if set(by_version) != set(version_ids):
        raise ValueError("DraftBlock snapshot references missing versions")
    for item in snapshot:
        block, _ = by_version[item.block_version_id]
        if block.id != item.block_id or block.block_role != item.block_role.value:
            raise ValueError("DraftBlock snapshot provenance does not match stored blocks")
    return by_version


async def _sentence_source_mappings(
    db: AsyncSession,
    *,
    project_id: str,
    snapshot: list[MemoirDraftBlockAssemblyItem],
    rows: dict[str, tuple[MemoirDraftBlock, MemoirDraftBlockVersion]],
) -> list[dict[str, object]]:
    snapshot_version_ids = [item.block_version_id for item in snapshot]
    claim_ids_by_version: dict[str, list[str]] = {}
    if snapshot_version_ids:
        links = list(
            (
                await db.scalars(
                    select(MemoirDraftBlockClaim).where(
                        MemoirDraftBlockClaim.project_id == project_id,
                        MemoirDraftBlockClaim.block_version_id.in_(snapshot_version_ids),
                    )
                )
            ).all()
        )
        for link in links:
            claim_ids_by_version.setdefault(link.block_version_id, []).append(link.claim_id)
    claim_ids = {
        claim_id
        for linked_claim_ids in claim_ids_by_version.values()
        for claim_id in linked_claim_ids
    }
    evidence_ids_by_claim: dict[str, list[str]] = {}
    if claim_ids:
        evidence = list(
            (
                await db.scalars(
                    select(MemoirClaimEvidence).where(
                        MemoirClaimEvidence.project_id == project_id,
                        MemoirClaimEvidence.claim_id.in_(claim_ids),
                        MemoirClaimEvidence.evidence_role
                        == MemoirClaimEvidenceRole.SUPPORTS.value,
                    )
                )
            ).all()
        )
        for item in evidence:
            if (item.quote or item.evidence_note or "").strip():
                evidence_ids_by_claim.setdefault(item.claim_id, []).append(item.id)

    mappings: list[dict[str, object]] = []
    for item in snapshot:
        _, version = rows[item.block_version_id]
        claim_ids_for_block = sorted(set(claim_ids_by_version.get(version.id, [])))
        if (
            item.block_role != MemoirDraftBlockRole.FACTUAL
            and not claim_ids_for_block
        ):
            continue
        evidence_ids = sorted(
            {
                evidence_id
                for claim_id in claim_ids_for_block
                for evidence_id in evidence_ids_by_claim.get(claim_id, [])
            }
        )
        mapped = bool(claim_ids_for_block) and all(
            evidence_ids_by_claim.get(claim_id) for claim_id in claim_ids_for_block
        )
        for index, sentence in enumerate(split_factual_sentences(version.content)):
            sentence_id = sha256(
                f"{version.id}:{index}:{sentence}".encode("utf-8")
            ).hexdigest()
            mappings.append(
                {
                    "sentence_id": sentence_id,
                    "sentence": sentence,
                    "block_id": item.block_id,
                    "block_version_id": version.id,
                    "claim_ids": claim_ids_for_block,
                    "evidence_ids": evidence_ids,
                    "mapped": mapped,
                }
            )
    return mappings


async def _high_gap_details(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> list[dict[str, str]]:
    related_gap_ids = select(MemoirNarrativeGapChapter.gap_id).where(
        MemoirNarrativeGapChapter.project_id == project_id,
        MemoirNarrativeGapChapter.chapter_id == chapter_id,
    )
    gaps = list(
        (
            await db.scalars(
                select(MemoirNarrativeGap).where(
                    MemoirNarrativeGap.project_id == project_id,
                    MemoirNarrativeGap.status == MemoirNarrativeGapStatus.OPEN.value,
                    MemoirNarrativeGap.severity == MemoirNarrativeGapSeverity.HIGH.value,
                    or_(
                        MemoirNarrativeGap.primary_chapter_id == chapter_id,
                        MemoirNarrativeGap.id.in_(related_gap_ids),
                    ),
                ).order_by(MemoirNarrativeGap.id)
            )
        ).all()
    )
    return [
        {
            "gap_id": gap.id,
            "gap_type": gap.gap_type,
            "dimension": QUALITY_GAP_DIMENSION_BY_TYPE[gap.gap_type],
        }
        for gap in gaps
        if gap.gap_type in QUALITY_GAP_DIMENSION_BY_TYPE
    ]


async def measure_narrative_candidate(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    baseline_draft_version_id: str,
    candidate_draft_version_id: str,
) -> tuple[str, dict[str, object]]:
    baseline = await _load_chapter_version(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        version_id=baseline_draft_version_id,
    )
    candidate = await _load_chapter_version(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        version_id=candidate_draft_version_id,
    )
    if baseline.draft_id != candidate.draft_id or baseline.version >= candidate.version:
        raise ValueError("quality baseline must be an earlier version of the same chapter draft")
    baseline_snapshot = _snapshot(baseline)
    candidate_snapshot = _snapshot(candidate)
    baseline_rows = await _snapshot_rows(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        snapshot=baseline_snapshot,
    )
    candidate_rows = await _snapshot_rows(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        snapshot=candidate_snapshot,
    )
    baseline_content = "\n\n".join(
        baseline_rows[item.block_version_id][1].content.strip()
        for item in baseline_snapshot
    ).strip()
    candidate_content = "\n\n".join(
        candidate_rows[item.block_version_id][1].content.strip()
        for item in candidate_snapshot
    ).strip()
    if baseline_content != baseline.content:
        raise ValueError("baseline content does not match its DraftBlock snapshot")
    if candidate_content != candidate.content:
        raise ValueError("candidate content does not match its DraftBlock snapshot")

    baseline_versions = {item.block_id: item.block_version_id for item in baseline_snapshot}
    candidate_versions = {item.block_id: item.block_version_id for item in candidate_snapshot}
    changed_block_ids = sorted(
        block_id
        for block_id in set(baseline_versions) | set(candidate_versions)
        if baseline_versions.get(block_id) != candidate_versions.get(block_id)
    )
    intended_block_ids = sorted(
        block_id
        for block_id in changed_block_ids
        if block_id in baseline_versions
        and block_id in candidate_versions
        and bool(
            (candidate_rows[candidate_versions[block_id]][1].feedback or "").strip()
        )
    )
    if not intended_block_ids:
        raise ValueError("quality measurement requires a traceable local block revision")
    unrelated_block_ids = sorted(set(changed_block_ids) - set(intended_block_ids))
    locked_baseline_ids = {
        item.block_id for item in baseline_snapshot if item.locked
    }
    locked_changed_ids = sorted(locked_baseline_ids & set(changed_block_ids))

    sentence_mappings = await _sentence_source_mappings(
        db,
        project_id=project_id,
        snapshot=candidate_snapshot,
        rows=candidate_rows,
    )
    high_gap_details = await _high_gap_details(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    gap_counts = {"time": 0, "people": 0, "evidence": 0}
    for detail in high_gap_details:
        gap_counts[detail["dimension"]] += 1
    unmapped_fact_ids = [
        str(mapping["sentence_id"])
        for mapping in sentence_mappings
        if not mapping["mapped"]
    ]
    content_hash = candidate_content_sha256(candidate.content)
    body: dict[str, object] = {
        "project_id": project_id,
        "chapter_id": chapter_id,
        "draft_version_id": candidate.id,
        "baseline_draft_version_id": baseline.id,
        "content_sha256": content_hash,
        "metrics": {
            "fact_sentence_count": len(sentence_mappings),
            "mapped_fact_sentence_count": len(sentence_mappings) - len(unmapped_fact_ids),
            "unmapped_fact_ids": unmapped_fact_ids,
            "unresolved_high_gaps": gap_counts,
            "locked_block_changes": len(locked_changed_ids),
            "unrelated_block_changes": len(unrelated_block_ids),
        },
        "source_mapping_granularity": "draft_block_claim_set",
        "sentence_sources": sentence_mappings,
        "high_gap_details": high_gap_details,
        "comparison": {
            "changed_block_ids": changed_block_ids,
            "intended_block_ids": intended_block_ids,
            "locked_changed_block_ids": locked_changed_ids,
            "unrelated_block_ids": unrelated_block_ids,
        },
    }
    artifact_body = {"engine": QUALITY_MEASUREMENT_ENGINE, **body}
    artifact: dict[str, object] = {
        "engine": QUALITY_MEASUREMENT_ENGINE,
        "artifact_id": measurement_artifact_id(artifact_body),
        "measured_at": datetime.now(UTC).isoformat(),
        **body,
    }
    validate_measurement_artifact(artifact)
    return candidate.content, artifact
