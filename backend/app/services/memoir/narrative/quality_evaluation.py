from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from statistics import median


QUALITY_DIMENSIONS = (
    "focus",
    "structure",
    "chronology_causality",
    "people_relationships",
    "factual_faithfulness",
    "readability_voice",
    "honest_gaps",
)
KEY_IMPROVEMENT_DIMENSIONS = {
    "structure",
    "chronology_causality",
    "people_relationships",
}
QUALITY_RUBRIC_VERSION = "1"
BLIND_CANDIDATE_LABELS = ("A", "B")
QUALITY_SAMPLE_CATEGORIES = {"event", "relationship", "career"}
HUMAN_REVIEW_ROLES = {"owner", "editor", "narrator", "reviewer"}
QUALITY_SCORE_ANCHORS = {
    "focus": {1: "主题散乱", 3: "主线可辨但有明显游离", 5: "始终围绕章节目标"},
    "structure": {1: "素材堆叠", 3: "有开合但过程薄弱", 5: "开端、展开、转折和收束完整"},
    "chronology_causality": {1: "时间或因果难以成立", 3: "基本可跟随但有跳跃", 5: "时间与因果清楚严密"},
    "people_relationships": {1: "人物身份关系混乱", 3: "主要人物可辨但介绍不足", 5: "人物身份和关系清晰一致"},
    "factual_faithfulness": {1: "有明显扩写或事实偏移", 3: "主要事实可靠但个别表述过实", 5: "每个事实都忠实且可回溯"},
    "readability_voice": {1: "机械或不像讲述者", 3: "通顺但个人声音不稳定", 5: "书面流畅且保留个人声音"},
    "honest_gaps": {1: "用扩写掩盖缺口", 3: "部分缺口有提示", 5: "缺口和不确定性均被诚实呈现"},
}


def candidate_content_sha256(content: str) -> str:
    return sha256(content.encode("utf-8")).hexdigest()


def _nonempty_string(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _valid_review_timestamp(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _valid_scores(value: object) -> dict[str, int] | None:
    if not isinstance(value, dict) or set(value) != set(QUALITY_DIMENSIONS):
        return None
    if not all(type(score) is int and 1 <= score <= 5 for score in value.values()):
        return None
    return value


def _paired_human_reviews(
    sample: dict,
    *,
    sample_id: str,
    old_label: str,
    new_label: str,
    reasons: list[str],
) -> list[tuple[dict, dict[str, int], dict[str, int]]]:
    paired = []
    reviews = sample.get("reviews", [])
    if not isinstance(reviews, list):
        reasons.append(f"{sample_id} reviews must be a list")
        return paired
    seen_reviewer_ids: set[str] = set()
    for review in reviews:
        if not isinstance(review, dict):
            reasons.append(f"{sample_id} contains an invalid review record")
            continue
        if review.get("reviewer_type") != "human":
            continue
        reviewer_id = _nonempty_string(review.get("reviewer_id"))
        role = review.get("role")
        independent = review.get("independent")
        if (
            reviewer_id is None
            or reviewer_id in seen_reviewer_ids
            or role not in HUMAN_REVIEW_ROLES
            or type(independent) is not bool
            or not _valid_review_timestamp(review.get("reviewed_at"))
        ):
            reasons.append(f"{sample_id} contains invalid human review provenance")
            continue
        seen_reviewer_ids.add(reviewer_id)
        scores = review.get("scores", {})
        if not isinstance(scores, dict):
            continue
        old_scores = _valid_scores(scores.get(old_label))
        new_scores = _valid_scores(scores.get(new_label))
        if old_scores is not None and new_scores is not None:
            paired.append((review, old_scores, new_scores))
    return paired


def _candidate_hashes(
    sample: dict,
    *,
    sample_id: str,
    reasons: list[str],
) -> dict[str, str] | None:
    candidates = sample.get("candidates")
    if not isinstance(candidates, dict) or set(candidates) != set(BLIND_CANDIDATE_LABELS):
        reasons.append(f"{sample_id} requires exactly two blinded candidates A and B")
        return None
    hashes: dict[str, str] = {}
    for label in BLIND_CANDIDATE_LABELS:
        candidate = candidates.get(label)
        if not isinstance(candidate, dict):
            reasons.append(f"{sample_id} candidate {label} is invalid")
            return None
        if set(candidate) != {"content", "content_sha256"}:
            reasons.append(f"{sample_id} candidate {label} exposes non-blind metadata")
            return None
        content = candidate.get("content")
        declared_hash = candidate.get("content_sha256")
        if not isinstance(content, str) or not content.strip():
            reasons.append(f"{sample_id} candidate {label} has no chapter content")
            return None
        actual_hash = candidate_content_sha256(content)
        if declared_hash != actual_hash:
            reasons.append(f"{sample_id} candidate {label} content hash does not match")
            return None
        hashes[label] = actual_hash
    if hashes["A"] == hashes["B"]:
        reasons.append(f"{sample_id} blind candidates must contain different content")
        return None
    return hashes


def _valid_answer_mapping(
    mapping: object,
    *,
    sample_id: str,
    candidate_hashes: dict[str, str] | None,
    reasons: list[str],
) -> tuple[str, str] | None:
    if not isinstance(mapping, dict):
        reasons.append(f"{sample_id} has no valid blind answer key")
        return None
    old_label = mapping.get("old")
    new_label = mapping.get("new")
    if (
        not isinstance(old_label, str)
        or not isinstance(new_label, str)
        or {old_label, new_label} != set(BLIND_CANDIDATE_LABELS)
    ):
        reasons.append(f"{sample_id} has no valid blind answer key")
        return None
    answer_hashes = mapping.get("candidate_sha256")
    if (
        candidate_hashes is None
        or not isinstance(answer_hashes, dict)
        or set(answer_hashes) != set(BLIND_CANDIDATE_LABELS)
        or answer_hashes != candidate_hashes
    ):
        reasons.append(f"{sample_id} answer key is not bound to the reviewed candidates")
        return None
    sources = mapping.get("source_versions")
    if not isinstance(sources, dict) or set(sources) != {"old", "new"}:
        reasons.append(f"{sample_id} answer key has no candidate version provenance")
        return None
    required_source_fields = {"pipeline", "project_id", "chapter_id", "version_id"}
    for pipeline in ("old", "new"):
        source = sources.get(pipeline)
        if (
            not isinstance(source, dict)
            or not required_source_fields.issubset(source)
            or source.get("pipeline") != ("legacy" if pipeline == "old" else "narrative")
            or any(_nonempty_string(source.get(field)) is None for field in required_source_fields - {"pipeline"})
        ):
            reasons.append(f"{sample_id} has invalid {pipeline} candidate provenance")
            return None
    old_source = sources["old"]
    new_source = sources["new"]
    if (
        old_source["project_id"] != new_source["project_id"]
        or old_source["chapter_id"] != new_source["chapter_id"]
    ):
        reasons.append(f"{sample_id} candidates are not from the same project chapter")
        return None
    if old_source["version_id"] == new_source["version_id"]:
        reasons.append(f"{sample_id} candidates must use different chapter versions")
        return None
    return str(old_label), str(new_label)


def _valid_new_candidate_metrics(
    mapping: dict,
    *,
    sample_id: str,
    new_label: str,
    new_content_hash: str,
    reasons: list[str],
) -> dict[str, object] | None:
    metrics_by_label = mapping.get("candidate_metrics")
    metrics = metrics_by_label.get(new_label) if isinstance(metrics_by_label, dict) else None
    required_fields = {
        "fact_sentence_count",
        "mapped_fact_sentence_count",
        "unmapped_fact_ids",
        "unresolved_high_gaps",
        "locked_block_changes",
        "unrelated_block_changes",
        "measurement_provenance",
    }
    if not isinstance(metrics, dict) or not required_fields.issubset(metrics):
        reasons.append(f"{sample_id} has no measured metrics for the new candidate")
        return None
    integer_fields = (
        "fact_sentence_count",
        "mapped_fact_sentence_count",
        "locked_block_changes",
        "unrelated_block_changes",
    )
    if any(type(metrics.get(field)) is not int or metrics[field] < 0 for field in integer_fields):
        reasons.append(f"{sample_id} has invalid candidate metric counts")
        return None
    gaps = metrics.get("unresolved_high_gaps")
    if (
        not isinstance(gaps, dict)
        or set(gaps) != {"time", "people", "evidence"}
        or any(type(value) is not int or value < 0 for value in gaps.values())
    ):
        reasons.append(f"{sample_id} has invalid unresolved high-gap metrics")
        return None
    unmapped_ids = metrics.get("unmapped_fact_ids")
    if (
        not isinstance(unmapped_ids, list)
        or any(_nonempty_string(value) is None for value in unmapped_ids)
        or len(set(unmapped_ids)) != len(unmapped_ids)
    ):
        reasons.append(f"{sample_id} has invalid unmapped factual sentence ids")
        return None
    fact_count = metrics["fact_sentence_count"]
    mapped_count = metrics["mapped_fact_sentence_count"]
    if mapped_count > fact_count or len(unmapped_ids) != fact_count - mapped_count:
        reasons.append(f"{sample_id} factual sentence metrics are internally inconsistent")
        return None
    provenance = metrics.get("measurement_provenance")
    if (
        not isinstance(provenance, dict)
        or _nonempty_string(provenance.get("engine")) is None
        or _nonempty_string(provenance.get("artifact_id")) is None
        or not _valid_review_timestamp(provenance.get("measured_at"))
        or provenance.get("content_sha256") != new_content_hash
    ):
        reasons.append(f"{sample_id} candidate metrics have invalid provenance")
        return None
    return metrics


def _validate_narrator_review(
    mapping: dict,
    *,
    sample_id: str,
    new_content_hash: str,
    reasons: list[str],
) -> bool:
    review = mapping.get("narrator_review")
    if review is None:
        return False
    if (
        not isinstance(review, dict)
        or _nonempty_string(review.get("reviewer_id")) is None
        or not _valid_review_timestamp(review.get("reviewed_at"))
        or review.get("content_sha256") != new_content_hash
        or type(review.get("facts_confirmed")) is not bool
        or type(review.get("voice_acceptable")) is not bool
    ):
        reasons.append(f"{sample_id} contains an invalid narrator review")
        return False
    if not review["facts_confirmed"]:
        reasons.append(f"{sample_id} narrator did not confirm the new candidate facts")
    if not review["voice_acceptable"]:
        reasons.append(f"{sample_id} narrator did not accept the new candidate voice")
    return True


def evaluate_quality_packet(packet: dict, answer_key: dict) -> dict:
    packet_data = packet if isinstance(packet, dict) else {}
    answer_key_data = answer_key if isinstance(answer_key, dict) else {}
    raw_samples = packet_data.get("samples", [])
    samples = raw_samples if isinstance(raw_samples, list) else []
    raw_mappings = answer_key_data.get("samples", {})
    mappings = raw_mappings if isinstance(raw_mappings, dict) else {}
    reasons: list[str] = []
    dimension_scores = {
        "old": {dimension: [] for dimension in QUALITY_DIMENSIONS},
        "new": {dimension: [] for dimension in QUALITY_DIMENSIONS},
    }
    completed_samples = 0
    owner_or_editor_reviewed = False
    independent_reviewed = False
    narrator_reviewed_count = 0
    new_fact_sentence_count = 0
    new_mapped_fact_sentence_count = 0
    new_unmapped_facts: list[str] = []
    high_gap_count = 0
    locked_block_changes = 0
    unrelated_block_changes = 0

    if not isinstance(packet, dict):
        reasons.append("quality packet must be an object")
    if not isinstance(answer_key, dict):
        reasons.append("answer key must be an object")
    if packet_data.get("rubric_version") != QUALITY_RUBRIC_VERSION:
        reasons.append("quality rubric version is missing or unsupported")
    if answer_key_data.get("rubric_version") != QUALITY_RUBRIC_VERSION:
        reasons.append("answer key rubric version is missing or unsupported")
    if not isinstance(raw_samples, list):
        reasons.append("quality packet samples must be a list")
    if not isinstance(raw_mappings, dict):
        reasons.append("answer key samples must be an object")

    if len(samples) < 2:
        reasons.append("at least two blind chapter samples are required")
    sample_ids = [
        str(sample.get("id") or "").strip() if isinstance(sample, dict) else ""
        for sample in samples
    ]
    if any(not sample_id for sample_id in sample_ids):
        reasons.append("every evaluation sample requires an id")
    if len(set(sample_ids)) != len(sample_ids):
        reasons.append("evaluation sample ids must be unique")
    covered_categories: set[str] = set()

    for sample in samples:
        if not isinstance(sample, dict):
            reasons.append("every evaluation sample must be an object")
            continue
        sample_id = str(sample.get("id") or "")
        display_id = sample_id or "sample"
        unexpected_sample_fields = set(sample) - {
            "id",
            "categories",
            "candidates",
            "reviews",
        }
        if unexpected_sample_fields:
            reasons.append(f"{display_id} exposes non-blind sample metadata")
        categories = sample.get("categories")
        if (
            not isinstance(categories, list)
            or not categories
            or any(
                not isinstance(category, str)
                or category not in QUALITY_SAMPLE_CATEGORIES
                for category in categories
            )
            or len(set(categories)) != len(categories)
        ):
            reasons.append(f"{display_id} has invalid evaluation categories")
        else:
            covered_categories.update(categories)
        candidate_hashes = _candidate_hashes(
            sample,
            sample_id=display_id,
            reasons=reasons,
        )
        mapping = mappings.get(sample_id)
        labels = _valid_answer_mapping(
            mapping,
            sample_id=display_id,
            candidate_hashes=candidate_hashes,
            reasons=reasons,
        )
        if labels is None:
            continue
        old_label, new_label = labels
        assert candidate_hashes is not None
        paired_reviews = _paired_human_reviews(
            sample,
            sample_id=display_id,
            old_label=old_label,
            new_label=new_label,
            reasons=reasons,
        )
        if not paired_reviews:
            reasons.append(f"{sample_id} has incomplete paired human scores")
            continue
        if not any(review.get("role") in {"owner", "editor"} for review, _, _ in paired_reviews):
            reasons.append(f"{sample_id} requires an owner or primary editor review")
            continue
        assert isinstance(mapping, dict)
        new_metrics = _valid_new_candidate_metrics(
            mapping,
            sample_id=display_id,
            new_label=new_label,
            new_content_hash=candidate_hashes[new_label],
            reasons=reasons,
        )
        if new_metrics is None:
            continue
        completed_samples += 1
        for review, old_scores, new_scores in paired_reviews:
            owner_or_editor_reviewed |= review.get("role") in {"owner", "editor"}
            independent_reviewed |= bool(review.get("independent"))
            for dimension in QUALITY_DIMENSIONS:
                dimension_scores["old"][dimension].append(old_scores[dimension])
                dimension_scores["new"][dimension].append(new_scores[dimension])

        sample_fact_count = int(new_metrics["fact_sentence_count"])
        sample_mapped_fact_count = int(new_metrics["mapped_fact_sentence_count"])
        new_fact_sentence_count += sample_fact_count
        new_mapped_fact_sentence_count += sample_mapped_fact_count
        if sample_fact_count < 1:
            reasons.append(f"{sample_id} has no measured factual sentences")
        elif sample_mapped_fact_count != sample_fact_count:
            reasons.append(f"{sample_id} factual sentence source coverage is not 100%")
        new_unmapped_facts.extend(str(item) for item in new_metrics["unmapped_fact_ids"])
        gaps = new_metrics["unresolved_high_gaps"]
        assert isinstance(gaps, dict)
        high_gap_count += sum(int(gaps[key]) for key in ("time", "people", "evidence"))
        locked_block_changes += int(new_metrics["locked_block_changes"])
        unrelated_block_changes += int(new_metrics["unrelated_block_changes"])
        narrator_reviewed_count += int(
            _validate_narrator_review(
                mapping,
                sample_id=display_id,
                new_content_hash=candidate_hashes[new_label],
                reasons=reasons,
            )
        )

    missing_categories = QUALITY_SAMPLE_CATEGORIES - covered_categories
    if missing_categories:
        reasons.append("missing evaluation categories: " + ", ".join(sorted(missing_categories)))

    if completed_samples < 2:
        reasons.append("at least two samples need paired human reviews")
    if not owner_or_editor_reviewed:
        reasons.append("project owner or primary editor review is required")
    if new_fact_sentence_count < 1 or new_mapped_fact_sentence_count != new_fact_sentence_count:
        reasons.append("new pipeline factual sentence source coverage is not 100%")
    if new_unmapped_facts:
        reasons.append("new pipeline contains unmapped factual sentences")
    if high_gap_count:
        reasons.append("new pipeline has unresolved high time, people, or evidence gaps")
    if locked_block_changes:
        reasons.append("locked blocks changed")
    if unrelated_block_changes:
        reasons.append("local rewrite changed unrelated blocks")

    medians: dict[str, dict[str, float | None]] = {"old": {}, "new": {}}
    improved_key_dimensions: list[str] = []
    for dimension in QUALITY_DIMENSIONS:
        old_values = dimension_scores["old"][dimension]
        new_values = dimension_scores["new"][dimension]
        old_median = float(median(old_values)) if old_values else None
        new_median = float(median(new_values)) if new_values else None
        medians["old"][dimension] = old_median
        medians["new"][dimension] = new_median
        if old_median is None or new_median is None:
            continue
        if new_median < old_median:
            reasons.append(f"new pipeline median declined on {dimension}")
        if dimension in KEY_IMPROVEMENT_DIMENSIONS and new_median > old_median:
            improved_key_dimensions.append(dimension)
    if len(improved_key_dimensions) < 2:
        reasons.append("fewer than two key dimensions improved")

    return {
        "passed": not reasons,
        "completed_samples": completed_samples,
        "sample_count": len(samples),
        "medians": medians,
        "improved_key_dimensions": improved_key_dimensions,
        "new_fact_source_coverage": (
            new_mapped_fact_sentence_count / new_fact_sentence_count
            if new_fact_sentence_count
            else 0.0
        ),
        "unresolved_high_gap_count": high_gap_count,
        "locked_block_changes": locked_block_changes,
        "unrelated_block_changes": unrelated_block_changes,
        "owner_or_editor_reviewed": owner_or_editor_reviewed,
        "independent_reviewed": independent_reviewed,
        "narrator_reviewed_count": narrator_reviewed_count,
        "reasons": list(dict.fromkeys(reasons)),
    }


def quality_report_markdown(result: dict) -> str:
    status = "PASS" if result["passed"] else "NOT READY"
    rows = [
        "# Memoir Narrative Quality Report",
        "",
        f"**Cutover status:** {status}",
        "",
        f"- Completed blind samples: {result['completed_samples']}/{result['sample_count']}",
        f"- New factual source coverage: {result['new_fact_source_coverage']:.0%}",
        f"- Unresolved high gaps: {result['unresolved_high_gap_count']}",
        f"- Locked block changes: {result['locked_block_changes']}",
        f"- Unrelated local rewrite changes: {result['unrelated_block_changes']}",
        f"- Owner/editor reviewed: {'yes' if result['owner_or_editor_reviewed'] else 'no'}",
        f"- Independent reviewer included: {'yes' if result['independent_reviewed'] else 'no'}",
        f"- Narrator-reviewed samples: {result['narrator_reviewed_count']}",
        "",
        "## Median Human Scores",
        "",
        "| Dimension | Old | New |",
        "| --- | ---: | ---: |",
    ]
    for dimension in QUALITY_DIMENSIONS:
        old_value = result["medians"]["old"][dimension]
        new_value = result["medians"]["new"][dimension]
        rows.append(
            f"| {dimension} | {old_value if old_value is not None else '-'} | "
            f"{new_value if new_value is not None else '-'} |"
        )
    rows.extend(["", "## Blocking Reasons", ""])
    rows.extend(
        [f"- {reason}" for reason in result["reasons"]]
        or ["- None"]
    )
    return "\n".join(rows) + "\n"
