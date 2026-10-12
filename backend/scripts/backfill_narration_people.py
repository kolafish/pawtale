"""Read people from AI-interview narration told before narration_people existed (run once after deploying).

Books run one after another; inside a book, segments run in narration order so each reading sees the people
read so far. A segment already read at its current transcript version is skipped, so the script can be re-run.
Not charged to users. From backend/: PYTHONPATH=. python3 scripts/backfill_narration_people.py [--project-id ID]
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from app.database import async_session_maker
from app.domain.memoir.types import MemoirChatMessageRole, MemoirInterviewType, MemoirSessionGoal
from app.models.memoir import MemoirChatMessage, MemoirVoiceSession, MemoirVoiceUtterance
from app.models.projects import Project
from app.services.memoir import narration_people
from app.services.memoir.material_roles import interviewer_lines
from app.services.memoir.projects import MEMOIR_PROFILE_METADATA_KEY


def _sessions():
    return select(MemoirVoiceSession.id).where(
        MemoirVoiceSession.interview_type == MemoirInterviewType.AI_INTERVIEW.value,
        MemoirVoiceSession.session_goal == MemoirSessionGoal.OPEN_NARRATION.value,
    )


async def backfill_project(project_id: str) -> None:
    async with async_session_maker() as db:
        project = await db.get(Project, project_id)
        rows = (await db.execute(
            select(MemoirVoiceUtterance, MemoirChatMessage)
            .join(MemoirChatMessage, MemoirVoiceUtterance.chat_message_id == MemoirChatMessage.id)
            .join(MemoirVoiceSession, MemoirChatMessage.voice_session_id == MemoirVoiceSession.id)
            .where(
                MemoirChatMessage.project_id == project_id,
                MemoirChatMessage.role == MemoirChatMessageRole.USER.value,
                MemoirChatMessage.voice_session_id.in_(_sessions()),
                MemoirChatMessage.content_text != "",
            )
            .order_by(MemoirVoiceSession.created_at, MemoirChatMessage.turn_index, MemoirChatMessage.created_at)
        )).all()
        lines = await interviewer_lines(db, (message.id for _utterance, message in rows))
        profile = (project.metadata_json or {}).get(MEMOIR_PROFILE_METADATA_KEY) or {}
        read = 0
        for utterance, message in rows:
            version = message.transcript_version_id or ""
            done = ((message.metadata_json or {}).get(narration_people.READING_KEY) or {}).get("transcript_version_id")
            if not message.content_text.strip() or not narration_people.ready(utterance, message) or done == version:
                continue
            read += await narration_people.read_people(
                db,
                project=project,
                narrator=str(profile.get("narrator_name") or ""),
                message=message,
                previous_line=lines.get(message.id, ""),
                transcript_version_id=version,
            )
        people = await narration_people.load_people(db, project_id)
        print(f"{project.title} ({project_id}): read {read} of {len(rows)} segments, {len(people)} people")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", action="append", help="limit to these books; default every book")
    args = parser.parse_args()
    project_ids = args.project_id
    if not project_ids:
        async with async_session_maker() as db:
            project_ids = list((await db.scalars(
                select(MemoirChatMessage.project_id).distinct().where(
                    MemoirChatMessage.role == MemoirChatMessageRole.USER.value,
                    MemoirChatMessage.voice_session_id.in_(_sessions()),
                )
            )).all())
    for project_id in project_ids:
        await backfill_project(project_id)


if __name__ == "__main__":
    asyncio.run(main())
