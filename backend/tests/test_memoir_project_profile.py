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


def test_ai_defaults_normalize_legacy_guidance_at_creation_boundaries():
    from app.schemas.memoir_voice import MemoirVoiceSessionCreate

    for guidance in (None, "free_narration", "guided_interview"):
        values = {} if guidance is None else {"guidance_mode": guidance}
        session = MemoirVoiceSessionCreate(**values, anchor_framework_item_id="topic")
        assert session.guidance_mode == "guided_interview"
        profile = _profile_metadata(
            MemoirProjectCreate(title="回忆", **({} if guidance is None else {"default_guidance_mode": guidance})),
            include_defaults=True,
        )["memoir_profile"]
        assert profile["default_guidance_mode"] == "guided_interview"

    human = MemoirVoiceSessionCreate(interview_type="human_interview")
    assert human.guidance_mode == "free_narration"


def test_saving_legacy_ai_defaults_normalizes_only_explicit_mode_changes():
    old = {"memoir_profile": {"default_guidance_mode": "free_narration", "default_interview_type": "ai_interview", "default_session_goal": "open_narration"}}
    renamed = _profile_metadata(MemoirProjectUpdate(title="新名字"), existing_metadata=old)
    assert renamed["memoir_profile"]["default_guidance_mode"] == "free_narration"
    saved = _profile_metadata(MemoirProjectUpdate(default_guidance_mode="free_narration"), existing_metadata=old)
    assert saved["memoir_profile"]["default_guidance_mode"] == "guided_interview"
