from pathlib import Path

from app.services.memoir.share_video_rendering import (
    ShareVideoRenderRequest,
    _QUOTE_PAGE_MAX_CHARS,
    _quote_line_ranges,
    _subtitle_file_content,
    _subtitle_ranges,
)


def test_subtitles_page_at_complete_sentence_boundaries() -> None:
    text = (
        "小时候，我经常去爷爷奶奶家玩。"
        "爷爷很喜欢我，每天下班都会摘一朵栀子花回来。"
        "三年级的时候，我转学进了城。"
    )

    pages = _subtitle_ranges(text, max_chars=_QUOTE_PAGE_MAX_CHARS)

    assert [text[start:end] for start, end in pages] == [
        "小时候，我经常去爷爷奶奶家玩。",
        "爷爷很喜欢我，每天下班都会摘一朵栀子花回来。",
        "三年级的时候，我转学进了城。",
    ]
    assert all(len(_quote_line_ranges(text, start, end)) <= 2 for start, end in pages)


def test_subtitles_pack_at_most_two_short_sentences_per_page() -> None:
    text = "爷爷种花。奶奶浇水。后来我们搬家。"

    pages = _subtitle_ranges(text, max_chars=_QUOTE_PAGE_MAX_CHARS)

    assert [text[start:end] for start, end in pages] == [
        "爷爷种花。奶奶浇水。",
        "后来我们搬家。",
    ]

    short_text = "我好。你呢。走吧。"
    short_pages = _subtitle_ranges(short_text, max_chars=_QUOTE_PAGE_MAX_CHARS)
    assert [short_text[start:end] for start, end in short_pages] == [
        "我好。你呢。",
        "走吧。",
    ]

    long_then_short = "小时候我和爷爷在院子里看月亮。后来搬家。"
    separate_pages = _subtitle_ranges(long_then_short, max_chars=_QUOTE_PAGE_MAX_CHARS)
    assert [long_then_short[start:end] for start, end in separate_pages] == [
        "小时候我和爷爷在院子里看月亮。",
        "后来搬家。",
    ]


def test_unpunctuated_subtitles_keep_every_character_in_two_line_pages() -> None:
    text = "我小时候经常去爷爷奶奶家玩记得爷爷很喜欢我每天都会摘一朵大栀子花带回来给我三年级的时候我转学进了城"

    pages = _subtitle_ranges(text, max_chars=_QUOTE_PAGE_MAX_CHARS)
    lines = [
        _quote_line_ranges(text, start, end)
        for start, end in pages
    ]

    assert pages == _subtitle_ranges(text, max_chars=_QUOTE_PAGE_MAX_CHARS)
    assert "".join(text[start:end] for start, end in pages) == text
    assert all(len(page_lines) <= 2 for page_lines in lines)
    assert all(
        "".join(text[start:end] for start, end in page_lines) == text[page_start:page_end]
        for (page_start, page_end), page_lines in zip(pages, lines)
    )
    assert all(end - start <= _QUOTE_PAGE_MAX_CHARS for start, end in pages)


def test_karaoke_preserves_silence_between_spoken_phrases(tmp_path: Path) -> None:
    request = ShareVideoRenderRequest(
        source_audio_path=tmp_path / "source.wav",
        source_audio_mime_type="audio/wav",
        target_path=tmp_path / "share.mp4",
        selected_text="去年，后来",
        char_timings=[
            {"start_ms": 0, "end_ms": 100},
            {"start_ms": 100, "end_ms": 200},
            {"start_ms": 200, "end_ms": 250},
            {"start_ms": 1250, "end_ms": 1350},
            {"start_ms": 1350, "end_ms": 1450},
        ],
        start_ms=0,
        end_ms=1450,
    )

    quote_event = next(
        line for line in _subtitle_file_content(request).splitlines() if ",Quote," in line
    )

    # ASS karaoke durations are cumulative from the dialogue start. The 1-second
    # pause must delay the next highlight without extending the first phrase.
    assert r"{\kf25}去年，{\k100}{\kf20}后来" in quote_event
