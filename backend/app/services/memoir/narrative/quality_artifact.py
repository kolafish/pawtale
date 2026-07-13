from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
from uuid import uuid4

from app.domain.memoir.narrative_types import MemoirNarrativeGapType


QUALITY_MEASUREMENT_ENGINE = "memoir-narrative-candidate-audit-v1"
QUALITY_GAP_DIMENSION_BY_TYPE = {
    MemoirNarrativeGapType.MISSING_SLOT.value: "evidence",
    MemoirNarrativeGapType.CHRONOLOGY.value: "time",
    MemoirNarrativeGapType.CAUSALITY.value: "time",
    MemoirNarrativeGapType.RELATIONSHIP.value: "people",
    MemoirNarrativeGapType.UNCLEAR_REFERENCE.value: "people",
    MemoirNarrativeGapType.UNSUPPORTED_FACT.value: "evidence",
    MemoirNarrativeGapType.CONTRADICTION.value: "evidence",
}


def measurement_artifact_id(body: dict[str, object]) -> str:
    return sha256(
        json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def validate_measurement_artifact(artifact: object) -> None:
    required_fields = {
        "engine",
        "artifact_id",
        "measured_at",
        "project_id",
        "chapter_id",
        "draft_version_id",
        "baseline_draft_version_id",
        "content_sha256",
        "metrics",
        "source_mapping_granularity",
        "sentence_sources",
        "high_gap_details",
        "comparison",
    }
    if not isinstance(artifact, dict) or set(artifact) != required_fields:
        raise ValueError("measurement artifact shape is invalid")
    if artifact.get("engine") != QUALITY_MEASUREMENT_ENGINE:
        raise ValueError("measurement artifact engine is invalid")
    for field in (
        "artifact_id",
        "measured_at",
        "project_id",
        "chapter_id",
        "draft_version_id",
        "baseline_draft_version_id",
        "content_sha256",
    ):
        if not isinstance(artifact.get(field), str) or not artifact[field].strip():
            raise ValueError(f"measurement artifact {field} is invalid")
    try:
        measured_at = datetime.fromisoformat(artifact["measured_at"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("measurement artifact measured_at is invalid") from exc
    if measured_at.tzinfo is None or measured_at.utcoffset() is None:
        raise ValueError("measurement artifact measured_at must include a timezone")
    if artifact.get("source_mapping_granularity") != "draft_block_claim_set":
        raise ValueError("measurement artifact source mapping granularity is invalid")

    artifact_body = {
        key: value
        for key, value in artifact.items()
        if key not in {"artifact_id", "measured_at"}
    }
    if artifact["artifact_id"] != measurement_artifact_id(artifact_body):
        raise ValueError("measurement artifact integrity check failed")
    _validate_metrics(artifact)


def _validate_metrics(artifact: dict) -> None:
    metrics = artifact.get("metrics")
    metric_fields = {
        "fact_sentence_count",
        "mapped_fact_sentence_count",
        "unmapped_fact_ids",
        "unresolved_high_gaps",
        "locked_block_changes",
        "unrelated_block_changes",
    }
    if not isinstance(metrics, dict) or set(metrics) != metric_fields:
        raise ValueError("measurement artifact metrics are invalid")
    count_fields = (
        "fact_sentence_count",
        "mapped_fact_sentence_count",
        "locked_block_changes",
        "unrelated_block_changes",
    )
    if any(type(metrics.get(field)) is not int or metrics[field] < 0 for field in count_fields):
        raise ValueError("measurement artifact counts are invalid")
    _validate_sentence_metrics(artifact, metrics)
    _validate_high_gap_metrics(artifact, metrics)
    _validate_comparison_metrics(artifact, metrics)


def _validate_sentence_metrics(artifact: dict, metrics: dict) -> None:
    sentence_sources = artifact.get("sentence_sources")
    sentence_fields = {
        "sentence_id",
        "sentence",
        "block_id",
        "block_version_id",
        "claim_ids",
        "evidence_ids",
        "mapped",
    }
    if not isinstance(sentence_sources, list) or any(
        not isinstance(source, dict)
        or set(source) != sentence_fields
        or any(
            not isinstance(source.get(field), str) or not source[field].strip()
            for field in ("sentence_id", "sentence", "block_id", "block_version_id")
        )
        or not isinstance(source.get("claim_ids"), list)
        or not isinstance(source.get("evidence_ids"), list)
        or any(
            not isinstance(value, str) or not value.strip()
            for field in ("claim_ids", "evidence_ids")
            for value in source[field]
        )
        or len(source["claim_ids"]) != len(set(source["claim_ids"]))
        or len(source["evidence_ids"]) != len(set(source["evidence_ids"]))
        or type(source.get("mapped")) is not bool
        or (
            source["mapped"]
            and (not source["claim_ids"] or not source["evidence_ids"])
        )
        for source in sentence_sources
    ):
        raise ValueError("measurement artifact sentence sources are invalid")
    sentence_ids = [source["sentence_id"] for source in sentence_sources]
    if len(sentence_ids) != len(set(sentence_ids)):
        raise ValueError("measurement artifact contains duplicate sentence sources")
    mapped_count = sum(1 for source in sentence_sources if source["mapped"])
    unmapped_ids = [
        source["sentence_id"] for source in sentence_sources if not source["mapped"]
    ]
    if (
        metrics["fact_sentence_count"] != len(sentence_sources)
        or metrics["mapped_fact_sentence_count"] != mapped_count
        or metrics.get("unmapped_fact_ids") != unmapped_ids
    ):
        raise ValueError("measurement artifact sentence metrics are inconsistent")


def _validate_high_gap_metrics(artifact: dict, metrics: dict) -> None:
    high_gap_details = artifact.get("high_gap_details")
    if not isinstance(high_gap_details, list) or any(
        not isinstance(detail, dict)
        or set(detail) != {"gap_id", "gap_type", "dimension"}
        or not isinstance(detail.get("gap_id"), str)
        or not detail["gap_id"].strip()
        or detail.get("gap_type") not in QUALITY_GAP_DIMENSION_BY_TYPE
        or detail.get("dimension")
        != QUALITY_GAP_DIMENSION_BY_TYPE.get(detail.get("gap_type"))
        for detail in high_gap_details
    ):
        raise ValueError("measurement artifact high-gap details are invalid")
    gap_ids = [detail["gap_id"] for detail in high_gap_details]
    gap_counts = {dimension: 0 for dimension in ("time", "people", "evidence")}
    for detail in high_gap_details:
        gap_counts[detail["dimension"]] += 1
    if (
        len(gap_ids) != len(set(gap_ids))
        or metrics.get("unresolved_high_gaps") != gap_counts
    ):
        raise ValueError("measurement artifact high-gap metrics are inconsistent")


def _validate_comparison_metrics(artifact: dict, metrics: dict) -> None:
    comparison = artifact.get("comparison")
    comparison_fields = {
        "changed_block_ids",
        "intended_block_ids",
        "locked_changed_block_ids",
        "unrelated_block_ids",
    }
    if not isinstance(comparison, dict) or set(comparison) != comparison_fields:
        raise ValueError("measurement artifact comparison is invalid")
    for field in comparison_fields:
        values = comparison.get(field)
        if (
            not isinstance(values, list)
            or any(not isinstance(value, str) or not value.strip() for value in values)
            or len(values) != len(set(values))
        ):
            raise ValueError("measurement artifact comparison is invalid")
    changed_ids = set(comparison["changed_block_ids"])
    intended_ids = set(comparison["intended_block_ids"])
    unrelated_ids = set(comparison["unrelated_block_ids"])
    locked_changed_ids = set(comparison["locked_changed_block_ids"])
    if (
        not intended_ids
        or not intended_ids <= changed_ids
        or unrelated_ids != changed_ids - intended_ids
        or not locked_changed_ids <= changed_ids
        or metrics["locked_block_changes"] != len(locked_changed_ids)
        or metrics["unrelated_block_changes"] != len(unrelated_ids)
    ):
        raise ValueError("measurement artifact comparison metrics are inconsistent")


def write_private_text(path: Path, content: str, *, overwrite: bool) -> None:
    target = path.expanduser().resolve()
    if target.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite existing file: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def write_private_json(path: Path, payload: dict, *, overwrite: bool) -> None:
    write_private_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        overwrite=overwrite,
    )
