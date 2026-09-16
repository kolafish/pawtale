from app.schemas.memoir_voice import MemoirProjectProgressSnapshotResponse
from app.services.memoir.voice_assistant_turn_stream import (
    _progress_announcement_already_used,
)
from app.services.memoir.voice_followup_ai import (
    build_progress_prompt_context,
    suppress_repeated_progress_context,
)


def _snapshot(*, valid_utterance_count: int = 30) -> MemoirProjectProgressSnapshotResponse:
    return MemoirProjectProgressSnapshotResponse(
        project_id="project-1",
        valid_utterance_count=valid_utterance_count,
        assigned_utterance_count=0,
        pending_utterance_count=valid_utterance_count,
        session_count=2,
        active_session_count=1,
        stopped_session_count=1,
        failed_session_count=0,
        user_message_count=valid_utterance_count,
        assistant_message_count=valid_utterance_count,
        source_char_count=3000,
        total_audio_seconds=600,
        chapter_count=0,
        confirmed_chapter_count=0,
        draft_word_count=0,
        recent_automation_events=[],
        chapter_progress_items=[],
    )


def test_progress_context_uses_stable_key_and_suppresses_repeat() -> None:
    first = build_progress_prompt_context(_snapshot())
    same = build_progress_prompt_context(_snapshot())
    repeated = suppress_repeated_progress_context(same)

    assert first.announcement_key.startswith("memoir-progress:")
    assert same.announcement_key == first.announcement_key
    assert "30 条讲述记录" in first.prompt_text
    assert repeated.progress_update is None
    assert repeated.announcement_key == ""
    assert "30 条讲述记录" not in repeated.prompt_text
    assert "已经自动播报过" in repeated.prompt_text


def test_changed_progress_gets_a_new_announcement_key() -> None:
    first = build_progress_prompt_context(_snapshot(valid_utterance_count=30))
    changed = build_progress_prompt_context(_snapshot(valid_utterance_count=31))

    assert changed.announcement_key != first.announcement_key


async def test_existing_message_metadata_suppresses_same_progress() -> None:
    context = build_progress_prompt_context(_snapshot())

    class FakeSession:
        async def scalars(self, _statement):
            return [{"progress_update": context.progress_update}]

    assert await _progress_announcement_already_used(  # type: ignore[arg-type]
        FakeSession(),
        project_id="project-1",
        announcement_key=context.announcement_key,
    )
