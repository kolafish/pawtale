"""Small I/O helpers shared by opt-in memoir model evaluations."""

import json
from pathlib import Path


def select_cases(cases: list[dict], identifiers: list[str] | None) -> list[dict]:
    if not identifiers:
        return cases
    unknown = set(identifiers) - {case["id"] for case in cases}
    if unknown:
        raise ValueError(f"Unknown case IDs: {sorted(unknown)}")
    return [case for case in cases if case["id"] in identifiers]


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
