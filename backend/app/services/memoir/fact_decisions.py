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


def prompt(decisions: Iterable[FactDecision]) -> str:
    lines = [
        f"确认时间（UTC）：{decision.confirmed_at.isoformat() if decision.confirmed_at else '未记录'}\n"
        f"核对问题：{decision.question}\n用户确认：{decision.answer}"
        for decision in decisions
    ]
    if not lines:
        return ""
    return (
        "用户已确认的事实（按确认时间排序）：\n" + "\n\n".join(lines)
        + "\n这些是对应原始讲述的事实更正。同一事实以用户较晚的明确确认为准，"
        "没有涉及的事实仍以原始讲述为准。只在已有叙述涉及该事实时采用，"
        "如果用户在更晚的讲述文字编辑中明确再次更正同一事实，采用较晚的更正；"
        "仅改语句、标点不能推翻已确认的事实。"
        "不要把核对过程写成新经历，也不要延伸出其他细节。"
        "审阅时不要再次询问已经明确回答的同一个问题。\n"
    )
