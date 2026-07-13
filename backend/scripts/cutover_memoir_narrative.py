#!/usr/bin/env python3
"""Evaluate the read-only final gate for the memoir narrative cutover."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.database import async_session_maker, engine
from app.migrations.runtime import assert_runtime_schema
from app.services.memoir.narrative.cutover_checks import collect_migration_report
from app.services.memoir.narrative.quality_evaluation import evaluate_quality_packet


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("check",), nargs="?", default="check")
    parser.add_argument("--quality-packet", type=Path)
    parser.add_argument("--answer-key", type=Path)
    return parser.parse_args()


def _validate_run_args(args: argparse.Namespace) -> None:
    if args.stage != "check":
        raise RuntimeError("final cutover supports only the read-only check stage")
    if getattr(args, "project_id", None):
        raise RuntimeError("final cutover check must include all active memoir projects")
    if getattr(args, "execute", False):
        raise RuntimeError("final cutover check is read-only")


def _quality_result(args: argparse.Namespace) -> dict[str, object]:
    if not args.quality_packet or not args.answer_key:
        return {
            "passed": False,
            "reasons": ["controlled human blind review packet and answer key are required"],
        }
    packet = json.loads(args.quality_packet.read_text(encoding="utf-8"))
    answer_key = json.loads(args.answer_key.read_text(encoding="utf-8"))
    return evaluate_quality_packet(packet, answer_key)


async def _run(args: argparse.Namespace) -> int:
    _validate_run_args(args)
    await assert_runtime_schema(engine)
    async with async_session_maker() as db:
        migration = await collect_migration_report(db)
    quality = _quality_result(args)
    cutover_ready = bool(migration["cutover_data_ready"] and quality["passed"])
    print(
        json.dumps(
            {
                "stage": "check",
                "migration": migration,
                "quality": quality,
                "cutover_ready": cutover_ready,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if cutover_ready else 2


async def _main() -> int:
    try:
        return await _run(_args())
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
