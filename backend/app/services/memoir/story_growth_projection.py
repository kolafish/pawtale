"""Resolve completed growth events against the current, authorized story tree."""

from app.schemas.memoir_snapshots import (
    MemoirStoryBookChange,
    MemoirStoryChange,
    MemoirStoryChapterChange,
    MemoirStoryGrowthEvent,
    MemoirStorySectionChange,
)
from app.schemas.memoir_voice import MemoirAutomationEventResponse


def _ids(value: object) -> set[str]:
    return {item for item in value if isinstance(item, str)} if isinstance(value, list) else set()


def _integer(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def story_growth_events(
    events: list[MemoirAutomationEventResponse],
    *,
    chapter_ids: set[str],
    section_chapters: dict[str, str],
    section_by_utterance: dict[str, str],
) -> list[MemoirStoryGrowthEvent]:
    result = []
    for event in events:
        if event.status != "completed" or event.metadata_json.get("acknowledged_at"):
            continue
        changes: list[MemoirStoryChange] = []
        if event.event_type == "story_structure_updated":
            new_chapters = _ids(event.metadata_json.get("new_chapter_ids")) & chapter_ids
            new_sections = _ids(event.metadata_json.get("new_section_ids")) & section_chapters.keys()
            affected_sections = {
                section_by_utterance[utterance_id]
                for utterance_id in event.utterance_ids
                if utterance_id in section_by_utterance
            }
            changes.extend(
                MemoirStoryChapterChange(kind="chapter_added", chapter_id=chapter_id)
                for chapter_id in sorted(new_chapters)
            )
            changes.extend(
                MemoirStorySectionChange(
                    kind="section_added" if section_id in new_sections else "section_updated",
                    chapter_id=chapter_id,
                    section_id=section_id,
                )
                for section_id, chapter_id in section_chapters.items()
                if section_id in new_sections or section_id in affected_sections
            )
        elif event.event_type == "chapter_draft_generated" and event.chapter_id in chapter_ids:
            changes.append(MemoirStoryChapterChange(kind="chapter_updated", chapter_id=event.chapter_id))
        elif event.event_type in {"book_output_generated", "book_editorial_copy_generated"}:
            changes.append(MemoirStoryBookChange())
        result.append(MemoirStoryGrowthEvent(
            event_id=event.id,
            summary_text=event.summary_text,
            created_at=event.created_at,
            book_char_count_delta=_integer(event.metadata_json.get("book_char_count_delta")),
            changes=changes,
        ))
    return result
