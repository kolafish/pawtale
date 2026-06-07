from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memoir import MemoirSession, MemoirSessionSummary
from app.schemas.memoir import MemoirSessionSummaryResponse


async def upsert_session_summary(
    db: AsyncSession,
    session: MemoirSession,
    *,
    summary: str,
    new_people_json: list[dict],
    new_events_json: list[dict],
    new_places_json: list[dict],
    new_themes_json: list[dict],
    open_questions_json: list[dict],
    next_question_suggestions_json: list[dict] | None = None,
) -> MemoirSessionSummaryResponse:
    existing = await db.scalar(
        select(MemoirSessionSummary).where(MemoirSessionSummary.session_id == session.id)
    )
    if existing:
        session_summary = existing
        session_summary.summary = summary
        session_summary.new_people_json = new_people_json
        session_summary.new_events_json = new_events_json
        session_summary.new_places_json = new_places_json
        session_summary.new_themes_json = new_themes_json
        session_summary.open_questions_json = open_questions_json
        session_summary.next_question_suggestions_json = next_question_suggestions_json or []
    else:
        session_summary = MemoirSessionSummary(
            project_id=session.project_id,
            session_id=session.id,
            summary=summary,
            new_people_json=new_people_json,
            new_events_json=new_events_json,
            new_places_json=new_places_json,
            new_themes_json=new_themes_json,
            open_questions_json=open_questions_json,
            next_question_suggestions_json=next_question_suggestions_json or [],
        )
        db.add(session_summary)

    await db.flush()
    return MemoirSessionSummaryResponse.model_validate(session_summary)
