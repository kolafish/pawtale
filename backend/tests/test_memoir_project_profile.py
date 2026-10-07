from app.schemas.memoir_projects import MemoirProjectCreate, MemoirProjectUpdate
from app.services.memoir.projects import _profile_metadata


def test_project_profile_persists_default_interview_fields():
    created = _profile_metadata(
        MemoirProjectCreate(
            title="爷爷的回忆",
            default_guidance_mode="free_narration",
            default_interview_type="human_interview",
            default_session_goal="open_narration",
        ),
        include_defaults=True,
    )

    assert created["memoir_profile"]["default_guidance_mode"] == "free_narration"
    assert created["memoir_profile"]["default_interview_type"] == "human_interview"
    assert created["memoir_profile"]["default_session_goal"] == "open_narration"

    updated = _profile_metadata(
        MemoirProjectUpdate(
            default_guidance_mode="free_narration",
            default_interview_type="human_interview",
            default_session_goal="book_completion",
        ),
        existing_metadata=created,
    )

    assert updated["memoir_profile"]["default_guidance_mode"] == "guided_interview"
    assert updated["memoir_profile"]["default_interview_type"] == "ai_interview"
    assert updated["memoir_profile"]["default_session_goal"] == "book_completion"

    legacy_update = _profile_metadata(
        MemoirProjectUpdate(default_guidance_mode="guided_interview"),
        existing_metadata=updated,
    )

    assert legacy_update["memoir_profile"]["default_guidance_mode"] == "guided_interview"
    assert legacy_update["memoir_profile"]["default_interview_type"] == "ai_interview"
    assert legacy_update["memoir_profile"]["default_session_goal"] == "open_narration"
