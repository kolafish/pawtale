from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import (
    MemoirCompletionIssueChannel,
    MemoirCompletionIssueOutcome,
    MemoirCompletionIssueScope,
    MemoirCompletionIssueStatus,
    MemoirSectionMaterialAssignedBy,
    MemoirSectionStatus,
    MemoirSessionGoal,
    MemoirVoiceSessionStatus,
)
from app.models.memoir import (
    MemoirBookPlanChapter,
    MemoirCompletionIssue,
    MemoirSection,
    MemoirSectionMaterial,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.services.memoir import completion_issues, sections
from app.services.memoir.transcript_projection import current_utterance_text


@dataclass(frozen=True)
class OpeningCompletionOffer:
    issue_id: str
    question: str


OPENING_OFFER_KEY = "opening_completion_issue_id"


async def offer_for_opening(db: AsyncSession, *, voice_session: MemoirVoiceSession) -> OpeningCompletionOffer | None:
    """Recommend once, without starting an issue or binding any narration."""
    previously_offered = set((await db.scalars(select(
        MemoirVoiceSession.metadata_json[OPENING_OFFER_KEY].as_string(),
    ).where(
        MemoirVoiceSession.project_id == voice_session.project_id,
        MemoirVoiceSession.id != voice_session.id,
        MemoirVoiceSession.metadata_json[OPENING_OFFER_KEY].as_string().is_not(None),
    ).distinct())).all())
    eligible_sections = set((await db.scalars(select(MemoirSection.id).join(MemoirBookPlanChapter).where(
        MemoirSection.project_id == voice_session.project_id,
        MemoirSection.status == "active", MemoirSection.include_in_book.is_(True),
        MemoirBookPlanChapter.include_in_book.is_(True),
    ))).all())
    for issue in await completion_issues.list_issues(db, project_id=voice_session.project_id):
        if issue.channel != "interview" or issue.status != "open" or issue.id in previously_offered:
            continue
        try:
            if await _target_section_id(db, issue=issue) not in eligible_sections:
                continue
        except HTTPException as exc:
            if exc.status_code == 409:
                continue
            raise
        voice_session.metadata_json = {**(voice_session.metadata_json or {}), OPENING_OFFER_KEY: issue.id}
        await db.flush()
        return OpeningCompletionOffer(issue_id=issue.id, question=issue.question)
    return None


@dataclass(frozen=True)
class CompletionIssuePromptContext:
    issue_id: str
    question: str
    prompt_text: str
    answered: bool


async def _target_section_id(
    db: AsyncSession,
    *,
    issue: MemoirCompletionIssue,
) -> str:
    related_material_ids = [
        str(value).strip()
        for value in issue.related_material_ids_json
        if str(value).strip()
    ] if issue.scope == MemoirCompletionIssueScope.CHAPTER.value else []
    if related_material_ids:
        related_section = await db.scalar(
            select(MemoirSection.id)
            .join(
                MemoirSectionMaterial,
                MemoirSectionMaterial.section_id == MemoirSection.id,
            )
            .where(
                MemoirSection.project_id == issue.project_id,
                MemoirSectionMaterial.utterance_id.in_(related_material_ids),
                MemoirSection.status == MemoirSectionStatus.ACTIVE.value,
                MemoirSection.include_in_book.is_(True),
                *(
                    (MemoirSection.chapter_id == issue.chapter_id,)
                    if issue.chapter_id
                    else ()
                ),
            )
            .order_by(
                MemoirSection.sort_order,
                MemoirSection.id,
            )
            .limit(1)
        )
        if related_section is not None:
            return related_section

    chapter_ids = (
        [issue.chapter_id]
        if issue.scope == MemoirCompletionIssueScope.CHAPTER.value and issue.chapter_id
        else issue.related_chapter_ids_json
    )
    for chapter_id in chapter_ids:
        first_section = await db.scalar(
            select(MemoirSection.id)
            .where(
                MemoirSection.project_id == issue.project_id,
                MemoirSection.chapter_id == chapter_id,
                MemoirSection.status == MemoirSectionStatus.ACTIVE.value,
                MemoirSection.include_in_book.is_(True),
            )
            .order_by(MemoirSection.sort_order, MemoirSection.id)
            .limit(1)
        )
        if first_section is not None:
            return first_section

    raise HTTPException(
        status_code=409,
        detail="这个补全问题还没有明确的小章节位置，请先在章节工作台处理",
    )


async def start_issue_for_session(
    db: AsyncSession,
    *,
    issue: MemoirCompletionIssue,
    voice_session: MemoirVoiceSession,
) -> MemoirCompletionIssue:
    if issue.project_id != voice_session.project_id:
        raise HTTPException(status_code=409, detail="补全问题不属于当前项目")
    if voice_session.session_goal != MemoirSessionGoal.BOOK_COMPLETION.value:
        raise HTTPException(status_code=409, detail="补全问题只能用于成书补访")
    if issue.channel != MemoirCompletionIssueChannel.INTERVIEW.value:
        raise HTTPException(status_code=409, detail="这个问题需要直接确认，不需要录音")
    if issue.status == MemoirCompletionIssueStatus.CLOSED.value:
        raise HTTPException(status_code=409, detail="补全问题已经结束")
    await _target_section_id(db, issue=issue)
    active_session = await db.scalar(
        select(MemoirVoiceSession.id).where(
            MemoirVoiceSession.project_id == issue.project_id,
            MemoirVoiceSession.current_completion_issue_id == issue.id,
            MemoirVoiceSession.id != voice_session.id,
            MemoirVoiceSession.status.not_in(
                {
                    MemoirVoiceSessionStatus.STOPPED.value,
                    MemoirVoiceSessionStatus.FAILED.value,
                }
            ),
        )
    )
    if active_session is not None:
        raise HTTPException(status_code=409, detail="这个补全问题正在另一轮访谈中")
    await completion_issues.start_issue(
        db,
        project_id=issue.project_id,
        issue_id=issue.id,
    )
    voice_session.current_completion_issue_id = issue.id
    db.add(voice_session)
    await db.flush()
    return issue


async def prompt_context_for_session(
    db: AsyncSession,
    *,
    voice_session: MemoirVoiceSession,
) -> CompletionIssuePromptContext | None:
    if (
        voice_session.session_goal != MemoirSessionGoal.BOOK_COMPLETION.value
        or not voice_session.current_completion_issue_id
    ):
        return None
    issue = await completion_issues.get_issue(
        db,
        project_id=voice_session.project_id,
        issue_id=voice_session.current_completion_issue_id,
    )
    answered = (
        issue.status == MemoirCompletionIssueStatus.CLOSED.value
        and issue.outcome == MemoirCompletionIssueOutcome.ANSWERED.value
    )
    if (
        issue.status != MemoirCompletionIssueStatus.IN_PROGRESS.value
        and not answered
    ):
        return None
    return CompletionIssuePromptContext(
        issue_id=issue.id,
        question=issue.question,
        prompt_text=(
            "\n".join(
                (
                    f"本轮已回答的补全问题：{issue.question}",
                    "状态：回答已经保存并归入小章节；只做结束确认，不再追问。",
                )
            )
            if answered
            else "\n".join(
                (
                    f"当前唯一补全问题：{issue.question}",
                    f"目的：{issue.reason}",
                )
            )
        ),
        answered=answered,
    )


async def bind_answer_segment(
    db: AsyncSession,
    *,
    voice_session: MemoirVoiceSession,
    utterance: MemoirVoiceUtterance,
) -> MemoirCompletionIssue | None:
    issue_id = (
        utterance.completion_issue_id
        or voice_session.current_completion_issue_id
    )
    if (
        voice_session.session_goal != MemoirSessionGoal.BOOK_COMPLETION.value
        or not issue_id
    ):
        return None
    issue = await completion_issues.get_issue(
        db,
        project_id=voice_session.project_id,
        issue_id=issue_id,
    )
    if (
        utterance.completion_issue_id
        and utterance.completion_issue_id != issue.id
    ):
        raise ValueError("讲述记录已经归属另一补全问题")
    utterance.completion_issue_id = issue.id
    db.add(utterance)
    await db.flush()
    if not current_utterance_text(utterance):
        return issue

    await sections.assign_material_to_section(
        db,
        project_id=voice_session.project_id,
        section_id=await _target_section_id(db, issue=issue),
        utterance_id=utterance.id,
        assigned_by=MemoirSectionMaterialAssignedBy.SYSTEM,
        assignment_reason=f"completion_issue:{issue.id}",
    )
    if issue.status != MemoirCompletionIssueStatus.CLOSED.value:
        await completion_issues.close_issue(
            db,
            project_id=voice_session.project_id,
            issue_id=issue.id,
            outcome=MemoirCompletionIssueOutcome.ANSWERED,
            note="回答已保存并直接归入对应小章节",
            answered_material_id=utterance.id,
        )
    return issue


async def release_session_issue(
    db: AsyncSession,
    *,
    voice_session: MemoirVoiceSession,
) -> MemoirCompletionIssue | None:
    issue_id = voice_session.current_completion_issue_id
    if not issue_id:
        return None
    issue = await completion_issues.get_issue(
        db,
        project_id=voice_session.project_id,
        issue_id=issue_id,
    )
    if issue.status == MemoirCompletionIssueStatus.IN_PROGRESS.value:
        has_answer = (
            await db.scalar(
                select(MemoirVoiceUtterance.id)
                .where(
                    MemoirVoiceUtterance.voice_session_id == voice_session.id,
                    MemoirVoiceUtterance.completion_issue_id == issue.id,
                    (
                        func.trim(MemoirVoiceUtterance.transcript_edited)
                        != ""
                    )
                    | (
                        func.trim(MemoirVoiceUtterance.transcript_raw)
                        != ""
                    ),
                )
                .limit(1)
            )
            is not None
        )
        if not has_answer:
            issue.status = MemoirCompletionIssueStatus.OPEN.value
            issue.started_at = None
            issue.updated_at = datetime.now(UTC).replace(tzinfo=None)
            db.add(issue)
    voice_session.current_completion_issue_id = None
    db.add(voice_session)
    await db.flush()
    return issue
