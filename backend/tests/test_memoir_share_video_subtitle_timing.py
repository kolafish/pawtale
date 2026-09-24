from pathlib import Path

from app.services.memoir.share_video_rendering import ShareVideoRenderRequest, _subtitle_file_content


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
