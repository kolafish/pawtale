from copy import deepcopy
from pathlib import Path
import subprocess
import sys

import pytest

from app.services.memoir.narrative.quality_evaluation import (
    QUALITY_DIMENSIONS,
    candidate_content_sha256,
    evaluate_quality_packet,
)
from scripts.prepare_memoir_narrative_blind_review import (
    build_blind_review_artifacts,
)


BACKEND_DIR = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "script_name",
    [
        "prepare_memoir_narrative_blind_review.py",
        "evaluate_memoir_narrative_quality.py",
    ],
)
def test_quality_gate_scripts_run_directly_from_backend(script_name: str):
    result = subprocess.run(
        [sys.executable, f"scripts/{script_name}", "--help"],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def _scores(*, structure: int, chronology: int, people: int) -> dict[str, int]:
    values = {dimension: 4 for dimension in QUALITY_DIMENSIONS}
    values.update(
        structure=structure,
        chronology_causality=chronology,
        people_relationships=people,
    )
    return values


def _sample(sample_id: str, categories: list[str], *, reviewer_type: str = "human") -> tuple[dict, dict]:
    old_content = f"{sample_id} 的旧链路章节正文。"
    new_content = f"{sample_id} 的新链路结构化章节正文。"
    candidate_hashes = {
        "A": candidate_content_sha256(old_content),
        "B": candidate_content_sha256(new_content),
    }
    return (
        {
            "id": sample_id,
            "categories": categories,
            "candidates": {
                "A": {
                    "content": old_content,
                    "content_sha256": candidate_hashes["A"],
                },
                "B": {
                    "content": new_content,
                    "content_sha256": candidate_hashes["B"],
                },
            },
            "reviews": [
                {
                    "reviewer_id": "reviewer-owner-1",
                    "reviewer_type": reviewer_type,
                    "role": "owner",
                    "independent": False,
                    "reviewed_at": "2026-07-14T09:00:00+08:00",
                    "scores": {
                        "A": _scores(structure=3, chronology=3, people=3),
                        "B": _scores(structure=4, chronology=4, people=3),
                    },
                }
            ],
        },
        {
            "old": "A",
            "new": "B",
            "candidate_sha256": candidate_hashes,
            "source_versions": {
                "old": {
                    "pipeline": "legacy",
                    "project_id": "project-1",
                    "chapter_id": f"chapter-{sample_id}",
                    "version_id": f"legacy-{sample_id}",
                },
                "new": {
                    "pipeline": "narrative",
                    "project_id": "project-1",
                    "chapter_id": f"chapter-{sample_id}",
                    "version_id": f"narrative-{sample_id}",
                },
            },
            "candidate_metrics": {
                "B": {
                    "fact_sentence_count": 4,
                    "mapped_fact_sentence_count": 4,
                    "unmapped_fact_ids": [],
                    "unresolved_high_gaps": {"time": 0, "people": 0, "evidence": 0},
                    "locked_block_changes": 0,
                    "unrelated_block_changes": 0,
                    "measurement_provenance": {
                        "engine": "deterministic-quality-audit-v1",
                        "artifact_id": f"audit-{sample_id}",
                        "measured_at": "2026-07-14T08:00:00+08:00",
                        "content_sha256": candidate_hashes["B"],
                    },
                }
            },
        },
    )


def _passing_artifacts(*, reviewer_type: str = "human") -> tuple[dict, dict]:
    samples_and_mappings = [
        _sample("event-1", ["event"], reviewer_type=reviewer_type),
        _sample("relationship-1", ["relationship"], reviewer_type=reviewer_type),
        _sample("career-1", ["career"], reviewer_type=reviewer_type),
    ]
    return (
        {
            "rubric_version": "1",
            "samples": [sample for sample, _ in samples_and_mappings],
        },
        {
            "rubric_version": "1",
            "samples": {
                sample["id"]: mapping
                for sample, mapping in samples_and_mappings
            },
        },
    )


def test_human_blind_quality_gate_requires_provenance_and_two_key_improvements():
    packet, answer_key = _passing_artifacts()

    result = evaluate_quality_packet(packet, answer_key)

    assert result["passed"] is True
    assert result["new_fact_source_coverage"] == 1.0
    assert result["improved_key_dimensions"] == ["structure", "chronology_causality"]


def test_model_only_scores_cannot_approve_the_quality_gate():
    packet, answer_key = _passing_artifacts(reviewer_type="model")

    result = evaluate_quality_packet(packet, answer_key)

    assert result["passed"] is False
    assert "project owner or primary editor review is required" in result["reasons"]
    assert "at least two samples need paired human reviews" in result["reasons"]


def test_scores_from_different_reviewers_do_not_count_as_a_paired_blind_review():
    packet, answer_key = _passing_artifacts()
    for sample in packet["samples"]:
        complete_scores = sample["reviews"][0]["scores"]
        sample["reviews"] = [
            {
                "reviewer_id": "owner-1",
                "reviewer_type": "human",
                "role": "owner",
                "independent": False,
                "reviewed_at": "2026-07-14T09:00:00+08:00",
                "scores": {"A": complete_scores["A"]},
            },
            {
                "reviewer_id": "editor-1",
                "reviewer_type": "human",
                "role": "editor",
                "independent": True,
                "reviewed_at": "2026-07-14T10:00:00+08:00",
                "scores": {"B": complete_scores["B"]},
            },
        ]

    result = evaluate_quality_packet(packet, answer_key)

    assert result["passed"] is False
    assert "event-1 has incomplete paired human scores" in result["reasons"]


def test_quality_gate_rejects_reviewed_content_changed_after_blinding():
    packet, answer_key = _passing_artifacts()
    packet["samples"][0]["candidates"]["B"]["content"] += "被替换。"

    result = evaluate_quality_packet(packet, answer_key)

    assert result["passed"] is False
    assert "event-1 candidate B content hash does not match" in result["reasons"]


def test_quality_gate_requires_real_version_provenance():
    packet, answer_key = _passing_artifacts()
    broken_answer_key = deepcopy(answer_key)
    event_mapping = broken_answer_key["samples"]["event-1"]
    event_mapping["source_versions"]["new"]["chapter_id"] = "different-chapter"

    result = evaluate_quality_packet(packet, broken_answer_key)

    assert result["passed"] is False
    assert "event-1 candidates are not from the same project chapter" in result["reasons"]


def test_quality_gate_rejects_internally_inconsistent_deterministic_metrics():
    packet, answer_key = _passing_artifacts()
    answer_key["samples"]["event-1"]["candidate_metrics"]["B"][
        "mapped_fact_sentence_count"
    ] = 3

    result = evaluate_quality_packet(packet, answer_key)

    assert result["passed"] is False
    assert "event-1 factual sentence metrics are internally inconsistent" in result["reasons"]


def test_quality_gate_rejects_identity_leaks_in_blind_candidates():
    packet, answer_key = _passing_artifacts()
    packet["samples"][0]["candidates"]["A"]["pipeline"] = "legacy"

    result = evaluate_quality_packet(packet, answer_key)

    assert result["passed"] is False
    assert "event-1 candidate A exposes non-blind metadata" in result["reasons"]


def test_quality_gate_reports_malformed_json_shapes_instead_of_crashing():
    result = evaluate_quality_packet([], {"rubric_version": "1", "samples": {}})

    assert result["passed"] is False
    assert "quality packet must be an object" in result["reasons"]


def test_narrator_rejection_overrides_other_passing_scores():
    packet, answer_key = _passing_artifacts()
    mapping = answer_key["samples"]["event-1"]
    mapping["narrator_review"] = {
        "reviewer_id": "narrator-1",
        "reviewed_at": "2026-07-14T11:00:00+08:00",
        "content_sha256": mapping["candidate_sha256"][mapping["new"]],
        "facts_confirmed": True,
        "voice_acceptable": False,
    }

    result = evaluate_quality_packet(packet, answer_key)

    assert result["passed"] is False
    assert "event-1 narrator did not accept the new candidate voice" in result["reasons"]


def test_blind_review_builder_keeps_pipeline_identity_out_of_packet(tmp_path):
    old_path = tmp_path / "old.txt"
    new_path = tmp_path / "new.txt"
    old_path_2 = tmp_path / "old-2.txt"
    new_path_2 = tmp_path / "new-2.txt"
    old_path.write_text("旧链路正文", encoding="utf-8")
    new_path.write_text("新链路正文", encoding="utf-8")
    old_path_2.write_text("第二章旧链路正文", encoding="utf-8")
    new_path_2.write_text("第二章新链路正文", encoding="utf-8")
    metrics = {
        "fact_sentence_count": 1,
        "mapped_fact_sentence_count": 1,
        "unmapped_fact_ids": [],
        "unresolved_high_gaps": {"time": 0, "people": 0, "evidence": 0},
        "locked_block_changes": 0,
        "unrelated_block_changes": 0,
        "measurement_provenance": {
            "engine": "deterministic-quality-audit-v1",
            "artifact_id": "audit-builder-1",
            "measured_at": "2026-07-14T08:00:00+08:00",
        },
    }
    manifest = {
        "rubric_version": "1",
        "samples": [
            {
                "id": "sample-1",
                "categories": ["event", "relationship"],
                "old": {
                    "content_path": "old.txt",
                    "project_id": "project-1",
                    "chapter_id": "chapter-1",
                    "version_id": "version-old",
                },
                "new": {
                    "content_path": "new.txt",
                    "project_id": "project-1",
                    "chapter_id": "chapter-1",
                    "version_id": "version-new",
                    "candidate_metrics": metrics,
                },
            },
            {
                "id": "sample-2",
                "categories": ["career"],
                "old": {
                    "content_path": "old-2.txt",
                    "project_id": "project-1",
                    "chapter_id": "chapter-2",
                    "version_id": "version-old-2",
                },
                "new": {
                    "content_path": "new-2.txt",
                    "project_id": "project-1",
                    "chapter_id": "chapter-2",
                    "version_id": "version-new-2",
                    "candidate_metrics": metrics,
                },
            },
        ],
    }

    packet, answer_key = build_blind_review_artifacts(
        manifest,
        manifest_dir=tmp_path,
        choose_new_label=lambda _sample_id: "A",
    )

    serialized_packet = str(packet)
    assert "legacy" not in serialized_packet
    assert "narrative" not in serialized_packet
    assert "version-old" not in serialized_packet
    assert "candidate_metrics" not in serialized_packet
    assert packet["samples"][0]["candidates"]["A"]["content"] == "新链路正文"
    assert answer_key["samples"]["sample-1"]["new"] == "A"
    prepared_metrics = answer_key["samples"]["sample-1"]["candidate_metrics"]["A"]
    assert prepared_metrics["fact_sentence_count"] == metrics["fact_sentence_count"]
    assert prepared_metrics["measurement_provenance"]["content_sha256"] == packet[
        "samples"
    ][0]["candidates"]["A"]["content_sha256"]


def test_blind_review_builder_rejects_an_incomplete_sample_set(tmp_path):
    manifest = {
        "rubric_version": "1",
        "samples": [
            {
                "id": "only-sample",
                "categories": ["event", "relationship", "career"],
            }
        ],
    }

    with pytest.raises(ValueError, match="at least two samples"):
        build_blind_review_artifacts(
            manifest,
            manifest_dir=tmp_path,
            choose_new_label=lambda _sample_id: "A",
        )
