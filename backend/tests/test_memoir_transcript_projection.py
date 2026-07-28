from types import SimpleNamespace

import pytest

from app.services.memoir.transcript_projection import (
    current_material_text,
    current_transcript_version_id,
    current_utterance_text,
    current_utterance_text_and_source,
)


@pytest.mark.parametrize(
    ("edited", "raw", "expected", "source"),
    [
        ("  校正后的讲述  ", "原始讲述", "校正后的讲述", "edited"),
        ("  ", "  原始讲述  ", "原始讲述", "raw"),
    ],
)
def test_current_utterance_text_uses_canonical_fallback(
    edited: str,
    raw: str,
    expected: str,
    source: str,
):
    utterance = SimpleNamespace(transcript_edited=edited, transcript_raw=raw)

    assert current_utterance_text(utterance) == expected
    assert current_utterance_text_and_source(utterance) == (expected, source)


def test_current_material_text_prefers_pinned_transcript_version():
    material = SimpleNamespace(
        transcript_version=SimpleNamespace(content="  入书版本  "),
        utterance=SimpleNamespace(
            transcript_edited="当前编辑版本",
            transcript_raw="原始版本",
        ),
    )

    assert current_material_text(material) == "入书版本"
    material.transcript_version.content = " "
    assert current_material_text(material) == "当前编辑版本"
    queried = SimpleNamespace(transcript_edited="已查询的讲述", transcript_raw="")
    assert current_material_text(
        SimpleNamespace(transcript_version=None),
        utterance=queried,
    ) == "已查询的讲述"


def test_current_transcript_version_id_tracks_chat_message():
    utterance = SimpleNamespace(
        chat_message=SimpleNamespace(transcript_version_id="version-1"),
    )

    assert current_transcript_version_id(utterance) == "version-1"
    utterance.chat_message = None
    assert current_transcript_version_id(utterance) is None
