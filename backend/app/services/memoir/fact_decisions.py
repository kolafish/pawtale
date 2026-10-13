"""Confirmed facts are writing inputs, never replacements for source recordings."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import (
    MemoirCompletionIssueChannel,
    MemoirCompletionIssueOutcome,
    MemoirCompletionIssueStatus,
    MemoirCompletionIssueType,
)
from app.models.memoir import MemoirCompletionIssue, MemoirSection, MemoirSectionMaterial
from app.services.memoir.semantic_state import semantic_json_signature
from app.services.memoir.transcript_projection import TranscriptEdit, transcript_edits_prompt


@dataclass(frozen=True)
class FactDecision:
    issue_id: str
    question: str
    answer: str
    material_ids: frozenset[str]
    chapter_ids: frozenset[str]
    confirmed_at: datetime | None

    @property
    def signature(self) -> str:
        return semantic_json_signature({
            "issue_id": self.issue_id,
            "question": self.question,
            "answer": self.answer,
        })


def is_confirmed_fact(issue: MemoirCompletionIssue) -> bool:
    return (
        issue.channel == MemoirCompletionIssueChannel.MANUAL_REVIEW.value
        and issue.issue_type == MemoirCompletionIssueType.CONFLICT.value
        and issue.status == MemoirCompletionIssueStatus.CLOSED.value
        and issue.outcome == MemoirCompletionIssueOutcome.CONFIRMED.value
        and bool(issue.outcome_note.strip())
    )


async def bind_sources(db: AsyncSession, issue: MemoirCompletionIssue) -> None:
    if issue.related_material_ids_json:
        return
    chapter_ids = [issue.chapter_id] if issue.chapter_id else issue.related_chapter_ids_json
    query = select(MemoirSectionMaterial.utterance_id).join(
        MemoirSection, MemoirSection.id == MemoirSectionMaterial.section_id,
    ).where(MemoirSection.project_id == issue.project_id)
    if chapter_ids:
        query = query.where(MemoirSection.chapter_id.in_(chapter_ids))
    issue.related_material_ids_json = sorted(set((await db.scalars(query)).all()))


async def load_fact_decisions(db: AsyncSession, *, project_id: str) -> tuple[FactDecision, ...]:
    issues = (await db.scalars(select(MemoirCompletionIssue).where(
        MemoirCompletionIssue.project_id == project_id,
        MemoirCompletionIssue.channel == MemoirCompletionIssueChannel.MANUAL_REVIEW.value,
        MemoirCompletionIssue.issue_type == MemoirCompletionIssueType.CONFLICT.value,
        MemoirCompletionIssue.status == MemoirCompletionIssueStatus.CLOSED.value,
        MemoirCompletionIssue.outcome == MemoirCompletionIssueOutcome.CONFIRMED.value,
    ).order_by(MemoirCompletionIssue.closed_at, MemoirCompletionIssue.id)
        .execution_options(populate_existing=True))).all()
    return tuple(FactDecision(
        issue_id=issue.id,
        question=issue.question.strip(),
        answer=issue.outcome_note.strip(),
        material_ids=frozenset(issue.related_material_ids_json),
        chapter_ids=frozenset(
            [issue.chapter_id] if issue.chapter_id else issue.related_chapter_ids_json
        ),
        confirmed_at=issue.closed_at,
    ) for issue in issues if is_confirmed_fact(issue))


def for_sources(
    decisions: Iterable[FactDecision], *, chapter_id: str, material_ids: Iterable[str],
) -> tuple[FactDecision, ...]:
    """Material-bound decisions follow a story when it moves to another chapter."""
    sources = set(material_ids)
    return tuple(decision for decision in decisions if (
        bool(decision.material_ids & sources) if decision.material_ids
        else not decision.chapter_ids or chapter_id in decision.chapter_ids
    ))


def signature(decisions: Iterable[FactDecision]) -> str:
    signatures = [decision.signature for decision in decisions]
    return semantic_json_signature(signatures) if signatures else ""


def prompt(decisions: Iterable[FactDecision], *, edits: tuple[TranscriptEdit, ...] = ()) -> str:
    decisions = tuple(decisions)
    if not decisions:
        return ""
    events = [(
        decision.confirmed_at or datetime.min,
        f"确认时间（UTC）：{decision.confirmed_at.isoformat() if decision.confirmed_at else '未记录'}\n"
        f"核对问题：{decision.question}\n用户确认：{decision.answer}",
    ) for decision in decisions]
    events.extend((edit.edited_at, transcript_edits_prompt((edit,))) for edit in edits)
    lines = [line for _, line in sorted(events, key=lambda event: event[0])]
    return (
        "用户更正记录（已按时间从早到晚排列，核对回答与人工编辑具有同等效力）：\n" + "\n\n".join(lines)
        + "\n按顺序逐条应用这些更正。每条只替换它实际改变的事实，保留此前仍有效的更正。"
        "较晚记录中的未修改片段只是上下文，不是重新确认旧说法。仅改标点或措辞，"
        "不能撤销此前任何一次事实更正，包括之前的人工编辑。"
        "没有涉及的事实仍以原始讲述为准。只在已有叙述涉及该事实时采用，"
        "不要把核对过程写成新经历，也不要延伸出其他细节。"
        "审阅时不要再次询问已经明确回答的同一个问题。\n"
    )
