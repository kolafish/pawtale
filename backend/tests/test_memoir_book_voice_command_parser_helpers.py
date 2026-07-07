from __future__ import annotations

from app.schemas.memoir_book import (
    MemoirBookVoiceCommandBookContext,
    MemoirBookVoiceCommandUncoveredSourceContext,
)
from app.services.memoir.book_voice_command_resolution import resolve_uncovered_source


def test_resolve_uncovered_source_falls_back_to_source_index_after_list_position():
    book_context = MemoirBookVoiceCommandBookContext(
        uncovered_sources=[
            MemoirBookVoiceCommandUncoveredSourceContext(
                source_index=7,
                utterance_id="utterance-7",
                text="第七条讲述",
            ),
            MemoirBookVoiceCommandUncoveredSourceContext(
                source_index=11,
                utterance_id="utterance-11",
                text="第十一条讲述",
            ),
        ]
    )

    source = resolve_uncovered_source(book_context, "待收录第7条", None)

    assert source is not None
    assert source.utterance_id == "utterance-7"
