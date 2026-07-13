from app.services.memoir.narrative.quality_evaluation import (
    QUALITY_DIMENSIONS,
    evaluate_quality_packet,
)


def _scores(*, structure: int, chronology: int, people: int) -> dict[str, int]:
    values = {dimension: 4 for dimension in QUALITY_DIMENSIONS}
    values.update(
        structure=structure,
        chronology_causality=chronology,
        people_relationships=people,
    )
    return values


def _sample(sample_id: str, category: str, *, reviewer_type: str = "human") -> dict:
    return {
        "id": sample_id,
        "category": category,
        "candidate_metrics": {
            "A": {},
            "B": {
                "fact_sentence_count": 4,
                "mapped_fact_sentence_count": 4,
                "unmapped_fact_ids": [],
                "unresolved_high_gaps": {"time": 0, "people": 0, "evidence": 0},
                "locked_block_changes": 0,
                "unrelated_block_changes": 0,
            },
        },
        "reviews": [
            {
                "reviewer_type": reviewer_type,
                "role": "owner",
                "independent": False,
                "scores": {
                    "A": _scores(structure=3, chronology=3, people=3),
                    "B": _scores(structure=4, chronology=4, people=3),
                },
            }
        ],
    }


def test_human_blind_quality_gate_requires_provenance_and_two_key_improvements():
    packet = {
        "rubric_version": "1",
        "samples": [
            _sample("event-1", "event"),
            _sample("relationship-1", "relationship"),
            _sample("career-1", "career"),
        ],
    }
    answer_key = {
        "samples": {
            sample["id"]: {"old": "A", "new": "B"}
            for sample in packet["samples"]
        }
    }

    result = evaluate_quality_packet(packet, answer_key)

    assert result["passed"] is True
    assert result["new_fact_source_coverage"] == 1.0
    assert result["improved_key_dimensions"] == ["structure", "chronology_causality"]


def test_model_only_scores_cannot_approve_the_quality_gate():
    samples = [
        _sample("event-1", "event", reviewer_type="model"),
        _sample("relationship-1", "relationship", reviewer_type="model"),
        _sample("career-1", "career", reviewer_type="model"),
    ]

    result = evaluate_quality_packet(
        {"rubric_version": "1", "samples": samples},
        {
            "samples": {
                sample["id"]: {"old": "A", "new": "B"}
                for sample in samples
            }
        },
    )

    assert result["passed"] is False
    assert "project owner or primary editor review is required" in result["reasons"]
    assert "at least two samples need paired human reviews" in result["reasons"]


def test_scores_from_different_reviewers_do_not_count_as_a_paired_blind_review():
    samples = [
        _sample("event-1", "event"),
        _sample("relationship-1", "relationship"),
        _sample("career-1", "career"),
    ]
    for sample in samples:
        complete_scores = sample["reviews"][0]["scores"]
        sample["reviews"] = [
            {
                "reviewer_type": "human",
                "role": "owner",
                "independent": False,
                "scores": {"A": complete_scores["A"]},
            },
            {
                "reviewer_type": "human",
                "role": "editor",
                "independent": True,
                "scores": {"B": complete_scores["B"]},
            },
        ]

    result = evaluate_quality_packet(
        {"rubric_version": "1", "samples": samples},
        {
            "samples": {
                sample["id"]: {"old": "A", "new": "B"}
                for sample in samples
            }
        },
    )

    assert result["passed"] is False
    assert "event-1 has incomplete paired human scores" in result["reasons"]
