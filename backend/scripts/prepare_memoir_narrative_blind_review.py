#!/usr/bin/env python3
"""Build blinded chapter candidates and a separate provenance answer key."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
from typing import Callable
from uuid import uuid4

from app.services.memoir.narrative.quality_evaluation import (
    BLIND_CANDIDATE_LABELS,
    QUALITY_RUBRIC_VERSION,
    QUALITY_SAMPLE_CATEGORIES,
    candidate_content_sha256,
)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        required=True,
        help="controlled manifest containing old/new content paths and version provenance",
    )
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--answer-key", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value.strip()


def _content_path(value: object, *, manifest_dir: Path, field: str) -> Path:
    raw_path = Path(_required_text(value, field=field)).expanduser()
    path = raw_path if raw_path.is_absolute() else manifest_dir / raw_path
    if not path.is_file():
        raise ValueError(f"{field} does not reference a readable file: {path}")
    return path


def _manifest_content_paths(manifest: object, *, manifest_dir: Path) -> set[Path]:
    if not isinstance(manifest, dict) or not isinstance(manifest.get("samples"), list):
        return set()
    paths: set[Path] = set()
    for sample in manifest["samples"]:
        if not isinstance(sample, dict):
            continue
        for pipeline in ("old", "new"):
            source = sample.get(pipeline)
            if not isinstance(source, dict):
                continue
            value = source.get("content_path")
            if not isinstance(value, str) or not value.strip():
                continue
            raw_path = Path(value).expanduser()
            path = raw_path if raw_path.is_absolute() else manifest_dir / raw_path
            paths.add(path.resolve())
    return paths


def build_blind_review_artifacts(
    manifest: dict,
    *,
    manifest_dir: Path,
    choose_new_label: Callable[[str], str] | None = None,
) -> tuple[dict, dict]:
    if manifest.get("rubric_version") != QUALITY_RUBRIC_VERSION:
        raise ValueError("manifest rubric_version is missing or unsupported")
    samples = manifest.get("samples")
    if not isinstance(samples, list) or len(samples) < 2:
        raise ValueError("manifest requires at least two samples")
    selector = choose_new_label or (
        lambda _sample_id: secrets.choice(BLIND_CANDIDATE_LABELS)
    )
    packet_samples: list[dict] = []
    answer_samples: dict[str, dict] = {}
    seen_ids: set[str] = set()
    covered_categories: set[str] = set()

    for raw_sample in samples:
        if not isinstance(raw_sample, dict):
            raise ValueError("every manifest sample must be an object")
        sample_id = _required_text(raw_sample.get("id"), field="sample.id")
        if sample_id in seen_ids:
            raise ValueError(f"duplicate sample id: {sample_id}")
        seen_ids.add(sample_id)
        categories = raw_sample.get("categories")
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
            raise ValueError(f"{sample_id}.categories is invalid")
        covered_categories.update(categories)

        sources: dict[str, dict] = {}
        contents: dict[str, str] = {}
        hashes: dict[str, str] = {}
        metrics: dict[str, dict] = {}
        for pipeline, expected_name in (("old", "legacy"), ("new", "narrative")):
            source = raw_sample.get(pipeline)
            if not isinstance(source, dict):
                raise ValueError(f"{sample_id}.{pipeline} is required")
            content = _content_path(
                source.get("content_path"),
                manifest_dir=manifest_dir,
                field=f"{sample_id}.{pipeline}.content_path",
            ).read_text(encoding="utf-8")
            if not content.strip():
                raise ValueError(f"{sample_id}.{pipeline} content is empty")
            contents[pipeline] = content
            hashes[pipeline] = candidate_content_sha256(content)
            sources[pipeline] = {
                "pipeline": expected_name,
                "project_id": _required_text(
                    source.get("project_id"),
                    field=f"{sample_id}.{pipeline}.project_id",
                ),
                "chapter_id": _required_text(
                    source.get("chapter_id"),
                    field=f"{sample_id}.{pipeline}.chapter_id",
                ),
                "version_id": _required_text(
                    source.get("version_id"),
                    field=f"{sample_id}.{pipeline}.version_id",
                ),
            }
            candidate_metrics = source.get("candidate_metrics")
            if candidate_metrics is not None:
                if not isinstance(candidate_metrics, dict):
                    raise ValueError(
                        f"{sample_id}.{pipeline}.candidate_metrics must be an object"
                    )
                metrics[pipeline] = dict(candidate_metrics)

        if sources["old"]["project_id"] != sources["new"]["project_id"]:
            raise ValueError(f"{sample_id} candidates must belong to the same project")
        if sources["old"]["chapter_id"] != sources["new"]["chapter_id"]:
            raise ValueError(f"{sample_id} candidates must belong to the same chapter")
        if sources["old"]["version_id"] == sources["new"]["version_id"]:
            raise ValueError(f"{sample_id} candidates require different version ids")
        if hashes["old"] == hashes["new"]:
            raise ValueError(f"{sample_id} candidates contain identical content")
        if "new" not in metrics:
            raise ValueError(f"{sample_id}.new.candidate_metrics is required")
        provenance = metrics["new"].get("measurement_provenance")
        if not isinstance(provenance, dict):
            raise ValueError(
                f"{sample_id}.new.candidate_metrics.measurement_provenance is required"
            )
        metrics["new"]["measurement_provenance"] = {
            **provenance,
            "content_sha256": hashes["new"],
        }

        new_label = selector(sample_id)
        if new_label not in BLIND_CANDIDATE_LABELS:
            raise ValueError("candidate label selector must return A or B")
        old_label = "B" if new_label == "A" else "A"
        label_by_pipeline = {"old": old_label, "new": new_label}
        content_by_label = {
            label_by_pipeline[pipeline]: {
                "content": contents[pipeline],
                "content_sha256": hashes[pipeline],
            }
            for pipeline in ("old", "new")
        }
        hash_by_label = {
            label_by_pipeline[pipeline]: hashes[pipeline]
            for pipeline in ("old", "new")
        }
        metrics_by_label = {
            label_by_pipeline[pipeline]: value
            for pipeline, value in metrics.items()
        }
        packet_samples.append(
            {
                "id": sample_id,
                "categories": categories,
                "candidates": {
                    label: content_by_label[label]
                    for label in BLIND_CANDIDATE_LABELS
                },
                "reviews": [],
            }
        )
        answer_samples[sample_id] = {
            "old": old_label,
            "new": new_label,
            "candidate_sha256": {
                label: hash_by_label[label]
                for label in BLIND_CANDIDATE_LABELS
            },
            "source_versions": sources,
            "candidate_metrics": metrics_by_label,
        }

    missing_categories = QUALITY_SAMPLE_CATEGORIES - covered_categories
    if missing_categories:
        raise ValueError(
            "manifest is missing evaluation categories: "
            + ", ".join(sorted(missing_categories))
        )

    return (
        {"rubric_version": QUALITY_RUBRIC_VERSION, "samples": packet_samples},
        {"rubric_version": QUALITY_RUBRIC_VERSION, "samples": answer_samples},
    )


def _write_private_json(path: Path, payload: dict, *, overwrite: bool) -> None:
    path = path.expanduser().resolve()
    if path.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite existing file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    args = _args()
    manifest_path = args.manifest.expanduser().resolve()
    packet_path = args.packet.expanduser().resolve()
    answer_key_path = args.answer_key.expanduser().resolve()
    if packet_path == answer_key_path:
        raise ValueError("--packet and --answer-key must be different files")
    if manifest_path in {packet_path, answer_key_path}:
        raise ValueError("output files must not overwrite the source manifest")
    if not args.overwrite:
        for output_path in (packet_path, answer_key_path):
            if output_path.exists():
                raise FileExistsError(
                    f"refusing to overwrite existing file: {output_path}"
                )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    protected_inputs = {manifest_path} | _manifest_content_paths(
        manifest,
        manifest_dir=manifest_path.parent,
    )
    if packet_path in protected_inputs or answer_key_path in protected_inputs:
        raise ValueError("output files must not overwrite manifest candidate content")
    packet, answer_key = build_blind_review_artifacts(
        manifest,
        manifest_dir=manifest_path.parent,
    )
    _write_private_json(packet_path, packet, overwrite=args.overwrite)
    _write_private_json(answer_key_path, answer_key, overwrite=args.overwrite)
    print(
        json.dumps(
            {
                "sample_count": len(packet["samples"]),
                "packet": str(packet_path),
                "answer_key": str(answer_key_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
