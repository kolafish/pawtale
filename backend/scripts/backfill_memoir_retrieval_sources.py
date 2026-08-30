from __future__ import annotations

import argparse
import asyncio
import json

from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.database import async_session_maker
from app.models.memoir import MemoirVoiceSession, MemoirVoiceUtterance
from app.services.memoir.material_roles import narrator_material_clause
from app.services.memoir.retrieval_source_index import schedule_source_indexing_job


async def backfill(*, project_id: str | None, batch_size: int) -> dict[str, int]:
    scanned = 0
    offset = 0
    async with async_session_maker() as db:
        while True:
            statement = (
                select(MemoirVoiceUtterance)
                .join(
                    MemoirVoiceSession,
                    MemoirVoiceSession.id == MemoirVoiceUtterance.voice_session_id,
                )
                .where(
                    narrator_material_clause(),
                    or_(
                        func.trim(MemoirVoiceUtterance.transcript_edited) != "",
                        func.trim(MemoirVoiceUtterance.transcript_raw) != "",
                    ),
                )
                .options(
                    selectinload(MemoirVoiceUtterance.voice_session),
                    selectinload(MemoirVoiceUtterance.chat_message),
                )
                .order_by(MemoirVoiceUtterance.id)
                .offset(offset)
                .limit(batch_size)
            )
            if project_id:
                statement = statement.where(MemoirVoiceSession.project_id == project_id)
            utterances = (await db.scalars(statement)).all()
            if not utterances:
                break
            for utterance in utterances:
                await schedule_source_indexing_job(
                    db,
                    project_id=utterance.voice_session.project_id,
                    utterance=utterance,
                    source="retrieval_source_backfill",
                )
                scanned += 1
            await db.commit()
            offset += len(utterances)
    return {"scanned": scanned}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Enqueue source-text retrieval indexing for existing memoir utterances."
    )
    parser.add_argument("--project-id")
    parser.add_argument("--batch-size", type=int, default=200)
    args = parser.parse_args()
    if args.batch_size <= 0:
        parser.error("--batch-size must be greater than zero")
    print(
        json.dumps(
            asyncio.run(
                backfill(
                    project_id=(args.project_id or "").strip() or None,
                    batch_size=args.batch_size,
                )
            ),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
