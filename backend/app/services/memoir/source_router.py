from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import MemoirBookPlanStatus
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirBookPlan,
    MemoirBookPlanChapter,
)
from app.schemas.memoir_book import (
    MemoirBookSourceRouteEvidence,
    MemoirBookSourceRouteResponse,
)
from app.services.memoir.access import load_memoir_project
from app.services.memoir.book_plan_sources import (
    SourceSegment,
    covered_utterance_ids,
    source_ref_utterance_ids,
    source_segments,
    sorted_chapters,
)


ASSIGN_CONFIDENCE_THRESHOLD = 0.52
AMBIGUOUS_MARGIN = 0.08
MIN_ROUTABLE_CHAR_COUNT = 8
MAX_CONTEXT_TEXT_CHARS = 1200
MAX_EVIDENCE_QUOTE_CHARS = 90

STOP_TOKENS = {
    "一个",
    "一些",
    "这个",
    "那个",
    "时候",
    "然后",
    "就是",
    "但是",
    "因为",
    "所以",
    "我们",
    "他们",
    "你们",
    "可以",
    "没有",
    "觉得",
}

TITLE_HINTS = (
    ("工作", "工作经历"),
    ("单位", "工作经历"),
    ("职场", "职场往事"),
    ("朋友", "朋友往事"),
    ("同学", "同学朋友"),
    ("外婆", "外婆的记忆"),
    ("奶奶", "奶奶的记忆"),
    ("爷爷", "爷爷的记忆"),
    ("父亲", "父亲的记忆"),
    ("母亲", "母亲的记忆"),
    ("过年", "年节记忆"),
    ("春节", "年节记忆"),
    ("学校", "求学岁月"),
    ("读书", "求学岁月"),
    ("老家", "老家记忆"),
    ("院子", "院子里的日子"),
)


@dataclass(frozen=True)
class ChapterRouteCandidate:
    chapter: MemoirBookPlanChapter
    context_text: str
    source_quotes: list[str]
    confidence: float
    overlap_score: float
    similarity_score: float
    title_score: float


def _char_count(text: str) -> int:
    return len("".join(text.split()))


def _compact_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _tokens(text: str) -> set[str]:
    compact = _compact_text(text)
    if not compact:
        return set()
    tokens: set[str] = set()
    for match in re.finditer(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9]+", compact):
        value = match.group(0)
        if re.fullmatch(r"[\u4e00-\u9fff]+", value):
            for size in (2, 3):
                for index in range(0, max(0, len(value) - size + 1)):
                    token = value[index:index + size]
                    if token not in STOP_TOKENS:
                        tokens.add(token)
        else:
            tokens.add(value.lower())
    return tokens


def _text_similarity(left: str, right: str) -> float:
    compact_left = _compact_text(left)[:500]
    compact_right = _compact_text(right)[:1200]
    if not compact_left or not compact_right:
        return 0.0
    return SequenceMatcher(None, compact_left, compact_right).ratio()


def _overlap_score(source_text: str, context_text: str) -> float:
    source_tokens = _tokens(source_text)
    if not source_tokens:
        return 0.0
    context_tokens = _tokens(context_text)
    if not context_tokens:
        return 0.0
    return len(source_tokens & context_tokens) / len(source_tokens)


def _title_score(source_text: str, title: str) -> float:
    source_tokens = _tokens(source_text)
    title_tokens = _tokens(title)
    if not source_tokens or not title_tokens:
        return 0.0
    return min(1.0, len(source_tokens & title_tokens) / max(1, len(title_tokens)))


def _confidence(source_text: str, chapter: MemoirBookPlanChapter, context_text: str) -> tuple[float, float, float, float]:
    overlap = _overlap_score(source_text, context_text)
    similarity = _text_similarity(source_text, context_text)
    title = _title_score(source_text, chapter.title)
    evidence_base = 0.18 if overlap >= 0.18 or title >= 0.2 else 0.0
    confidence = min(0.95, evidence_base + (overlap * 0.72) + (similarity * 0.24) + (title * 0.12))
    return confidence, overlap, similarity, title


def _latest_draft_text(chapter: MemoirBookPlanChapter) -> str:
    draft = chapter.draft
    if not draft or not draft.versions:
        return ""
    latest = max(draft.versions, key=lambda item: (item.version, item.created_at, item.id))
    return latest.content or ""


def _chapter_context(
    chapter: MemoirBookPlanChapter,
    *,
    source_by_utterance: dict[str, SourceSegment],
) -> tuple[str, list[str]]:
    source_quotes: list[str] = []
    for utterance_id in source_ref_utterance_ids(chapter.source_refs_json or []):
        source = source_by_utterance.get(utterance_id)
        if source:
            source_quotes.append(source.text)
    context_parts = [
        chapter.title,
        *source_quotes,
        _latest_draft_text(chapter),
    ]
    context = "\n".join(part for part in context_parts if part).strip()
    return context[:MAX_CONTEXT_TEXT_CHARS], source_quotes


def _candidate(
    chapter: MemoirBookPlanChapter,
    *,
    source_text: str,
    source_by_utterance: dict[str, SourceSegment],
) -> ChapterRouteCandidate:
    context_text, source_quotes = _chapter_context(chapter, source_by_utterance=source_by_utterance)
    confidence, overlap, similarity, title = _confidence(source_text, chapter, context_text)
    return ChapterRouteCandidate(
        chapter=chapter,
        context_text=context_text,
        source_quotes=source_quotes,
        confidence=round(confidence, 3),
        overlap_score=round(overlap, 3),
        similarity_score=round(similarity, 3),
        title_score=round(title, 3),
    )


def _quote(text: str) -> str:
    return _compact_text(text)[:MAX_EVIDENCE_QUOTE_CHARS]


def _source_evidence(source: SourceSegment) -> MemoirBookSourceRouteEvidence:
    return MemoirBookSourceRouteEvidence(
        utterance_id=source.utterance_id,
        quote=_quote(source.text),
        reason="待整理讲述记录",
    )


def _candidate_evidence(candidate: ChapterRouteCandidate) -> MemoirBookSourceRouteEvidence:
    quote = candidate.source_quotes[0] if candidate.source_quotes else candidate.chapter.title
    return MemoirBookSourceRouteEvidence(
        chapter_id=candidate.chapter.id,
        quote=_quote(quote),
        reason=(
            f"章节「{candidate.chapter.title}」相似度 "
            f"{candidate.confidence:.2f}"
        ),
    )


def _suggested_title(source_text: str) -> str:
    compact = _compact_text(source_text)
    for keyword, title in TITLE_HINTS:
        if keyword in compact:
            return title
    if not compact:
        return "新的回忆"
    return f"{compact[:8]}的记忆"


async def _load_plan(db: AsyncSession, project_id: str) -> MemoirBookPlan | None:
    return await db.scalar(
        select(MemoirBookPlan)
        .where(MemoirBookPlan.project_id == project_id)
        .options(
            selectinload(MemoirBookPlan.chapters)
            .selectinload(MemoirBookPlanChapter.draft)
            .selectinload(MemoirBookChapterDraft.versions),
        )
    )


def _route_from_candidates(
    source: SourceSegment,
    candidates: list[ChapterRouteCandidate],
) -> MemoirBookSourceRouteResponse:
    source_evidence = _source_evidence(source)
    if _char_count(source.text) < MIN_ROUTABLE_CHAR_COUNT:
        return MemoirBookSourceRouteResponse(
            action="hold_for_review",
            utterance_id=source.utterance_id,
            source_index=source.source_index,
            confidence=0.2,
            reason="讲述内容太短，不能可靠判断章节归属",
            evidence=[source_evidence],
        )
    if not candidates:
        return MemoirBookSourceRouteResponse(
            action="suggest_new_chapter",
            utterance_id=source.utterance_id,
            source_index=source.source_index,
            confidence=0.5,
            suggested_title=_suggested_title(source.text),
            reason="当前没有可匹配的章节，可作为候选新章",
            evidence=[source_evidence],
        )

    ordered = sorted(candidates, key=lambda item: item.confidence, reverse=True)
    best = ordered[0]
    second = ordered[1] if len(ordered) > 1 else None
    if second and best.confidence - second.confidence < AMBIGUOUS_MARGIN and best.confidence >= 0.42:
        return MemoirBookSourceRouteResponse(
            action="hold_for_review",
            utterance_id=source.utterance_id,
            source_index=source.source_index,
            confidence=best.confidence,
            reason="多个章节相似度接近，需要用户确认目标章节",
            evidence=[source_evidence, _candidate_evidence(best), _candidate_evidence(second)],
        )
    if best.confidence >= ASSIGN_CONFIDENCE_THRESHOLD:
        return MemoirBookSourceRouteResponse(
            action="assign_to_chapter",
            utterance_id=source.utterance_id,
            source_index=source.source_index,
            target_chapter_id=best.chapter.id,
            target_chapter_title=best.chapter.title,
            confidence=best.confidence,
            reason=f"与章节「{best.chapter.title}」已有素材或正文高度相关",
            evidence=[source_evidence, _candidate_evidence(best)],
        )
    if _char_count(source.text) >= 18:
        return MemoirBookSourceRouteResponse(
            action="suggest_new_chapter",
            utterance_id=source.utterance_id,
            source_index=source.source_index,
            confidence=max(0.45, round(1 - best.confidence, 3)),
            suggested_title=_suggested_title(source.text),
            reason="与已有章节相关度不足，更像新话题",
            evidence=[source_evidence, _candidate_evidence(best)],
        )
    return MemoirBookSourceRouteResponse(
        action="hold_for_review",
        utterance_id=source.utterance_id,
        source_index=source.source_index,
        confidence=best.confidence,
        reason="讲述较短且没有足够明确的章节匹配",
        evidence=[source_evidence, _candidate_evidence(best)],
    )


async def route_book_source(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    utterance_id: str,
) -> MemoirBookSourceRouteResponse:
    await load_memoir_project(db, project_id, user_id)
    plan = await _load_plan(db, project_id)
    if not plan:
        raise HTTPException(status_code=404, detail="章节规划不存在")
    if plan.status != MemoirBookPlanStatus.CONFIRMED.value:
        raise HTTPException(status_code=400, detail="请先确认章节规划")

    sources = await source_segments(db, user_id=user_id, project_id=project_id)
    source_by_utterance = {source.utterance_id: source for source in sources}
    source = source_by_utterance.get(utterance_id)
    if not source:
        raise HTTPException(status_code=404, detail="讲述记录不存在")
    if utterance_id in covered_utterance_ids(plan):
        raise HTTPException(status_code=400, detail="讲述记录已收录到章节")

    candidates = [
        _candidate(chapter, source_text=source.text, source_by_utterance=source_by_utterance)
        for chapter in sorted_chapters(plan)
    ]
    return _route_from_candidates(source, candidates)
