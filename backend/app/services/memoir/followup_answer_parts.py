from __future__ import annotations

from itertools import product

from app.domain.memoir.narrative_types import MemoirFollowupAnswerKind


def _occurrence_spans(source_text: str, excerpt: str) -> tuple[tuple[int, int], ...]:
    spans: list[tuple[int, int]] = []
    start = source_text.find(excerpt)
    while start >= 0:
        spans.append((start, start + len(excerpt)))
        start = source_text.find(excerpt, start + 1)
    return tuple(spans)


def validate_resolved_answer_parts(
    *,
    source_text: str,
    answer_kind: MemoirFollowupAnswerKind,
    direct_narrative_text: str,
    clarification_text: str,
    fact_resolution_text: str,
) -> None:
    parts = {
        MemoirFollowupAnswerKind.DIRECT_NARRATIVE: direct_narrative_text,
        MemoirFollowupAnswerKind.CLARIFICATION: clarification_text,
        MemoirFollowupAnswerKind.FACT_RESOLUTION: fact_resolution_text,
    }
    populated = {kind: text for kind, text in parts.items() if text}
    for kind, excerpt in populated.items():
        if excerpt not in source_text:
            raise ValueError(f"{kind.value} 不是讲述原文片段")

    if answer_kind == MemoirFollowupAnswerKind.MIXED:
        if len(populated) < 2:
            raise ValueError("混合回答没有拆出至少两种用途")
        span_options = [_occurrence_spans(source_text, excerpt) for excerpt in populated.values()]
        if not any(
            all(
                left_end <= right_start or right_end <= left_start
                for index, (left_start, left_end) in enumerate(candidate)
                for right_start, right_end in candidate[index + 1 :]
            )
            for candidate in product(*span_options)
        ):
            raise ValueError("混合回答的用途片段相互重叠")
        return

    if answer_kind not in populated or len(populated) != 1:
        raise ValueError("回答用途与拆分文本不一致")
