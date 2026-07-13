#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.services.memoir.narrative.quality_evaluation import (
    evaluate_quality_packet,
    quality_report_markdown,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate a blinded human review packet against the memoir cutover gate."
    )
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--answer-key", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    packet = json.loads(args.packet.read_text(encoding="utf-8"))
    answer_key = json.loads(args.answer_key.read_text(encoding="utf-8"))
    result = evaluate_quality_packet(packet, answer_key)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(quality_report_markdown(result), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
