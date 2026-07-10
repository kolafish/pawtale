from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from app.services.memoir.voice_transcripts import (
    CleanedTranscriptText,
    MemoirTranscriptCleanupContext,
    generate_cleaned_transcript_text,
)


SPEAKER_PROOFREAD_CONTEXT_LIMIT = 8
SPEAKER_PROOFREAD_CONTEXT_TEXT_LIMIT = 160


@dataclass(frozen=True)
class SpeakerProofreadContextLine:
    speaker_id: str | None
    text: str
    start_ms: int | None = None
    end_ms: int | None = None


def speaker_transcript_cleanup_context(
    context_lines: Sequence[SpeakerProofreadContextLine],
) -> MemoirTranscriptCleanupContext:
    utterances: list[str] = []
    for line in context_lines[-SPEAKER_PROOFREAD_CONTEXT_LIMIT:]:
        text = " ".join(line.text.split()).strip()
        if not text:
            continue
        label = _speaker_context_label(line.speaker_id)
        prefix = f"{label}：" if label else ""
        utterances.append(f"{prefix}{text[:SPEAKER_PROOFREAD_CONTEXT_TEXT_LIMIT]}")
    return MemoirTranscriptCleanupContext(
        scene="多人实时转写",
        recent_user_utterances=tuple(utterances),
    )


async def proofread_speaker_transcript_text(
    text: str,
    *,
    context_lines: Sequence[SpeakerProofreadContextLine] = (),
    ai_model: str | None = None,
) -> CleanedTranscriptText:
    return await generate_cleaned_transcript_text(
        text,
        ai_model=ai_model,
        context=speaker_transcript_cleanup_context(context_lines),
        strict=False,
    )


def _speaker_context_label(speaker_id: str | None) -> str:
    value = (speaker_id or "").strip()
    if not value:
        return ""
    if value == "speaker_unknown":
        return "未分辨说话人"
    if value.startswith("speaker_"):
        suffix = value.removeprefix("speaker_").strip()
        return f"说话人 {suffix}" if suffix else "说话人"
    return value
