from datetime import UTC, datetime
from types import SimpleNamespace

from app.domain.memoir.types import (
    MemoirAutomationEventStatus,
    MemoirAutomationEventType,
)
from app.schemas.memoir_voice import (
    MemoirAutomationEventResponse,
    MemoirProjectProgressSnapshotResponse,
)
from app.services.memoir.automation_event_metadata import VOICE_ONLY_KEY
from app.services.memoir.automation_events import _visible_events
from app.services.memoir.voice_followup_ai import build_progress_prompt_context
from app.services.memoir.voice_progress_notices import is_progress_utterance_milestone


def _snapshot(
    *,
    valid_utterance_count: int = 30,
    events: list[MemoirAutomationEventResponse] | None = None,
) -> MemoirProjectProgressSnapshotResponse:
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
        recent_automation_events=events or [],
        chapter_progress_items=[],
    )


def test_snapshot_count_is_not_treated_as_a_pending_voice_notice() -> None:
    context = build_progress_prompt_context(_snapshot(valid_utterance_count=30))

    assert context.progress_update is None
    assert context.voice_announcement_event_ids == []
    assert "30 条讲述记录" not in context.prompt_text


def test_pending_event_is_passed_to_llm_as_one_voice_notice() -> None:
    now = datetime.now(UTC)
    event = MemoirAutomationEventResponse(
        id="event-30",
        project_id="project-1",
        event_type=MemoirAutomationEventType.STORY_STRUCTURE_UPDATED,
        status=MemoirAutomationEventStatus.COMPLETED,
        utterance_ids=["utterance-30"],
        summary_text="这个项目已经留下 30 条讲述记录。",
        created_at=now,
        updated_at=now,
    )

    context = build_progress_prompt_context(_snapshot(events=[event]))

    assert context.progress_update is not None
    assert context.voice_announcement_event_ids == ["event-30"]
    assert "本轮进展：这个项目已经留下 30 条讲述记录。" in context.prompt_text
    assert "统一组织成一段自然口语" in context.prompt_text


def test_progress_milestones_remain_sparse() -> None:
    assert is_progress_utterance_milestone(30) is True
    assert is_progress_utterance_milestone(31) is False
    assert is_progress_utterance_milestone(150) is True


def test_voice_only_notice_stays_out_of_regular_progress_feed() -> None:
    event = SimpleNamespace(metadata_json={VOICE_ONLY_KEY: True})

    assert _visible_events(
        [event],
        include_acknowledged=False,
        voice_unannounced_only=False,
    ) == []
    assert _visible_events(
        [event],
        include_acknowledged=False,
        voice_unannounced_only=True,
    ) == [event]
