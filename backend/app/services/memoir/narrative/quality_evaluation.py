from __future__ import annotations

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
QUALITY_SCORE_ANCHORS = {
    "focus": {1: "主题散乱", 3: "主线可辨但有明显游离", 5: "始终围绕章节目标"},
    "structure": {1: "素材堆叠", 3: "有开合但过程薄弱", 5: "开端、展开、转折和收束完整"},
    "chronology_causality": {1: "时间或因果难以成立", 3: "基本可跟随但有跳跃", 5: "时间与因果清楚严密"},
    "people_relationships": {1: "人物身份关系混乱", 3: "主要人物可辨但介绍不足", 5: "人物身份和关系清晰一致"},
    "factual_faithfulness": {1: "有明显扩写或事实偏移", 3: "主要事实可靠但个别表述过实", 5: "每个事实都忠实且可回溯"},
    "readability_voice": {1: "机械或不像讲述者", 3: "通顺但个人声音不稳定", 5: "书面流畅且保留个人声音"},
    "honest_gaps": {1: "用扩写掩盖缺口", 3: "部分缺口有提示", 5: "缺口和不确定性均被诚实呈现"},
}


def _valid_scores(value: object) -> dict[str, int] | None:
    if not isinstance(value, dict) or set(value) != set(QUALITY_DIMENSIONS):
        return None
    if not all(type(score) is int and 1 <= score <= 5 for score in value.values()):
        return None
    return value


def _paired_human_reviews(
    sample: dict,
    *,
    old_label: str,
    new_label: str,
) -> list[tuple[dict, dict[str, int], dict[str, int]]]:
    paired = []
    for review in sample.get("reviews", []):
        if review.get("reviewer_type") != "human":
            continue
        scores = review.get("scores", {})
        if not isinstance(scores, dict):
            continue
        old_scores = _valid_scores(scores.get(old_label))
        new_scores = _valid_scores(scores.get(new_label))
        if old_scores is not None and new_scores is not None:
            paired.append((review, old_scores, new_scores))
    return paired


def evaluate_quality_packet(packet: dict, answer_key: dict) -> dict:
    samples = packet.get("samples", [])
    mappings = answer_key.get("samples", {})
    reasons: list[str] = []
    dimension_scores = {
        "old": {dimension: [] for dimension in QUALITY_DIMENSIONS},
        "new": {dimension: [] for dimension in QUALITY_DIMENSIONS},
    }
    completed_samples = 0
    owner_or_editor_reviewed = False
    independent_reviewed = False
    new_fact_sentence_count = 0
    new_mapped_fact_sentence_count = 0
    new_unmapped_facts: list[str] = []
    high_gap_count = 0
    locked_block_changes = 0
    unrelated_block_changes = 0

    if packet.get("rubric_version") != QUALITY_RUBRIC_VERSION:
        reasons.append("quality rubric version is missing or unsupported")

    if len(samples) < 2:
        reasons.append("at least two blind chapter samples are required")
    sample_ids = [str(sample.get("id") or "").strip() for sample in samples]
    if any(not sample_id for sample_id in sample_ids):
        reasons.append("every evaluation sample requires an id")
    if len(set(sample_ids)) != len(sample_ids):
        reasons.append("evaluation sample ids must be unique")
    categories = {str(sample.get("category") or "") for sample in samples}
    missing_categories = {"event", "relationship", "career"} - categories
    if missing_categories:
        reasons.append("missing evaluation categories: " + ", ".join(sorted(missing_categories)))

    for sample in samples:
        sample_id = str(sample.get("id") or "")
        mapping = mappings.get(sample_id, {})
        old_label = mapping.get("old")
        new_label = mapping.get("new")
        if {old_label, new_label} != {"A", "B"}:
            reasons.append(f"{sample_id or 'sample'} has no valid blind answer key")
            continue
        paired_reviews = _paired_human_reviews(
            sample,
            old_label=old_label,
            new_label=new_label,
        )
        if not paired_reviews:
            reasons.append(f"{sample_id} has incomplete paired human scores")
            continue
        completed_samples += 1
        for review, old_scores, new_scores in paired_reviews:
            owner_or_editor_reviewed |= review.get("role") in {"owner", "editor"}
            independent_reviewed |= bool(review.get("independent"))
            for dimension in QUALITY_DIMENSIONS:
                dimension_scores["old"][dimension].append(old_scores[dimension])
                dimension_scores["new"][dimension].append(new_scores[dimension])

        new_metrics = sample.get("candidate_metrics", {}).get(new_label, {})
        sample_fact_count = int(new_metrics.get("fact_sentence_count") or 0)
        sample_mapped_fact_count = int(new_metrics.get("mapped_fact_sentence_count") or 0)
        new_fact_sentence_count += sample_fact_count
        new_mapped_fact_sentence_count += sample_mapped_fact_count
        if sample_fact_count < 1:
            reasons.append(f"{sample_id} has no measured factual sentences")
        elif sample_mapped_fact_count != sample_fact_count:
            reasons.append(f"{sample_id} factual sentence source coverage is not 100%")
        new_unmapped_facts.extend(str(item) for item in new_metrics.get("unmapped_fact_ids", []))
        gaps = new_metrics.get("unresolved_high_gaps", {})
        high_gap_count += sum(int(gaps.get(key) or 0) for key in ("time", "people", "evidence"))
        locked_block_changes += int(new_metrics.get("locked_block_changes") or 0)
        unrelated_block_changes += int(new_metrics.get("unrelated_block_changes") or 0)

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
