from app.schemas.memoir_projects import MemoirProjectCreate, MemoirProjectUpdate
from app.services.memoir.projects import _profile_metadata


def test_project_profile_persists_default_interview_fields():
    created = _profile_metadata(
        MemoirProjectCreate(
            title="爷爷的回忆",
            default_guidance_mode="free_narration",
            default_interview_type="human_interview",
        ),
        include_defaults=True,
    )

    assert created["memoir_profile"]["default_guidance_mode"] == "free_narration"
    assert created["memoir_profile"]["default_interview_type"] == "human_interview"

    updated = _profile_metadata(
        MemoirProjectUpdate(
            default_guidance_mode="guided_interview",
            default_interview_type="ai_interview",
        ),
        existing_metadata=created,
    )

    assert updated["memoir_profile"]["default_guidance_mode"] == "guided_interview"
    assert updated["memoir_profile"]["default_interview_type"] == "ai_interview"

    legacy_update = _profile_metadata(
        MemoirProjectUpdate(default_guidance_mode="guided_interview"),
        existing_metadata=created,
    )

    assert legacy_update["memoir_profile"]["default_guidance_mode"] == "guided_interview"
    assert legacy_update["memoir_profile"]["default_interview_type"] == "ai_interview"
