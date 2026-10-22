from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

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
    MemoirChatMessage,
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
OPENING_OFFER_TRIGGER_MESSAGE_KEY = "opening_completion_trigger_message_id"
OPENING_OFFER_STATUS_KEY = "opening_completion_offer_status"
OPENING_OFFER_PENDING_AT_KEY = "opening_completion_offer_pending_at"
OPENING_OFFER_PENDING = "pending"
OPENING_OFFER_CONSUMED = "consumed"
OPENING_OFFER_PENDING_TTL = timedelta(minutes=15)
OPENING_OFFER_DECLINED_KEY = "opening_completion_offer_declined"
SUPPLEMENT_DECLINE_PHRASES = (
    "不想补充",
    "不补充",
    "不用补充",
    "不想回答",
    "不回答",
    "不想说这个",
    "不聊这个",
    "先讲别的",
)
# Assistant lines that leave the narrator still answering the opening.
OPENING_INTENTS = ("opening", "no_answer_prompt")


async def offer_for_opening(db: AsyncSession, *, voice_session: MemoirVoiceSession) -> OpeningCompletionOffer | None:
    """Prepare one retryable offer without starting an issue or binding narration."""
    metadata = voice_session.metadata_json or {}
    if metadata.get(OPENING_OFFER_DECLINED_KEY):
        return None
    current_issue_id = metadata.get(OPENING_OFFER_KEY)
    current_status = metadata.get(OPENING_OFFER_STATUS_KEY)
    if current_issue_id and current_status != OPENING_OFFER_PENDING:
        # Older sessions have no status field; their existing offer was already shown.
        return None
    eligible_sections = await _eligible_opening_section_ids(
        db,
        project_id=voice_session.project_id,
    )
    if current_issue_id and current_status == OPENING_OFFER_PENDING:
        pending_issue = await db.get(MemoirCompletionIssue, current_issue_id)
        pending_started_at = _pending_offer_started_at(
            metadata,
            fallback=voice_session.updated_at,
        )
        if (
            pending_started_at is not None
            and not _pending_offer_expired(metadata, fallback=voice_session.updated_at)
            and pending_issue is not None
            and pending_issue.project_id == voice_session.project_id
            and pending_issue.channel == MemoirCompletionIssueChannel.INTERVIEW.value
            and pending_issue.status == MemoirCompletionIssueStatus.OPEN.value
            and await _issue_has_eligible_target(
                db,
                issue=pending_issue,
                eligible_sections=eligible_sections,
            )
        ):
            latest_user_message = await _latest_user_message(db, voice_session.id)
            pending_metadata = {
                **metadata,
                OPENING_OFFER_STATUS_KEY: OPENING_OFFER_PENDING,
                OPENING_OFFER_PENDING_AT_KEY: pending_started_at.isoformat(),
            }
            if latest_user_message is not None:
                pending_metadata[OPENING_OFFER_TRIGGER_MESSAGE_KEY] = latest_user_message.id
            voice_session.metadata_json = pending_metadata
            await db.flush()
            return OpeningCompletionOffer(issue_id=pending_issue.id, question=pending_issue.question)
        await clear_pending_opening_offer(db, voice_session=voice_session)
        metadata = voice_session.metadata_json or {}
    previously_offered = await _offered_issue_ids(
        db,
        project_id=voice_session.project_id,
        excluded_session_id=voice_session.id,
    )
    for issue in await completion_issues.list_issues(db, project_id=voice_session.project_id):
        if issue.channel != MemoirCompletionIssueChannel.INTERVIEW.value or issue.status != MemoirCompletionIssueStatus.OPEN.value:
            continue
        if issue.id in previously_offered:
            continue
        # Serialize claims for one issue. The lock is held until the caller
        # commits the pending metadata, so another opening cannot claim it
        # between the availability check and the metadata write.
        locked_issue = await db.scalar(
            select(MemoirCompletionIssue)
            .where(MemoirCompletionIssue.id == issue.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if locked_issue is None or locked_issue.project_id != voice_session.project_id:
            continue
        if locked_issue.channel != MemoirCompletionIssueChannel.INTERVIEW.value or locked_issue.status != MemoirCompletionIssueStatus.OPEN.value:
            continue
        previously_offered = await _offered_issue_ids(
            db,
            project_id=voice_session.project_id,
            excluded_session_id=voice_session.id,
        )
        if locked_issue.id in previously_offered or not await _issue_has_eligible_target(
            db,
            issue=locked_issue,
            eligible_sections=eligible_sections,
        ):
            continue
        latest_user_message = await _latest_user_message(db, voice_session.id)
        metadata = {
            **(voice_session.metadata_json or {}),
            OPENING_OFFER_KEY: locked_issue.id,
            OPENING_OFFER_STATUS_KEY: OPENING_OFFER_PENDING,
            OPENING_OFFER_PENDING_AT_KEY: datetime.now(UTC).replace(tzinfo=None).isoformat(),
        }
        if latest_user_message is not None:
            metadata[OPENING_OFFER_TRIGGER_MESSAGE_KEY] = latest_user_message.id
        voice_session.metadata_json = metadata
        await db.flush()
        return OpeningCompletionOffer(issue_id=locked_issue.id, question=locked_issue.question)
    return None


async def _eligible_opening_section_ids(db: AsyncSession, *, project_id: str) -> set[str]:
    return set((await db.scalars(select(MemoirSection.id).join(MemoirBookPlanChapter).where(
        MemoirSection.project_id == project_id,
        MemoirSection.status == MemoirSectionStatus.ACTIVE.value,
        MemoirSection.include_in_book.is_(True),
        MemoirBookPlanChapter.include_in_book.is_(True),
    ))).all())


async def _issue_has_eligible_target(
    db: AsyncSession,
    *,
    issue: MemoirCompletionIssue,
    eligible_sections: set[str],
) -> bool:
    try:
        return await _target_section_id(db, issue=issue) in eligible_sections
    except HTTPException as exc:
        if exc.status_code == 409:
            return False
        raise


async def _offered_issue_ids(
    db: AsyncSession,
    *,
    project_id: str,
    excluded_session_id: str,
) -> set[str]:
    offered_rows = await db.execute(select(
        MemoirVoiceSession.metadata_json[OPENING_OFFER_KEY].as_string(),
        MemoirVoiceSession.metadata_json[OPENING_OFFER_STATUS_KEY].as_string(),
        MemoirVoiceSession.metadata_json[OPENING_OFFER_PENDING_AT_KEY].as_string(),
        MemoirVoiceSession.updated_at,
    ).where(
        MemoirVoiceSession.project_id == project_id,
        MemoirVoiceSession.id != excluded_session_id,
        MemoirVoiceSession.metadata_json[OPENING_OFFER_KEY].as_string().is_not(None),
    ))
    result = set()
    for issue_id, status, pending_at, updated_at in offered_rows.all():
        if status == OPENING_OFFER_PENDING and _pending_offer_expired(
            {OPENING_OFFER_PENDING_AT_KEY: pending_at},
            fallback=updated_at,
        ):
            continue
        result.add(issue_id)
    return result


def _pending_offer_started_at(
    metadata: dict,
    *,
    fallback: datetime | None,
) -> datetime | None:
    raw = metadata.get(OPENING_OFFER_PENDING_AT_KEY)
    if isinstance(raw, str):
        try:
            value = datetime.fromisoformat(raw)
        except ValueError:
            value = None
        if value is not None:
            if value.tzinfo is not None:
                return value.astimezone(UTC).replace(tzinfo=None)
            return value
    return fallback


def _pending_offer_expired(metadata: dict, *, fallback: datetime | None) -> bool:
    started_at = _pending_offer_started_at(metadata, fallback=fallback)
    if started_at is None:
        return True
    now = datetime.now(UTC).replace(tzinfo=None)
    return now - started_at >= OPENING_OFFER_PENDING_TTL


async def _latest_user_message(
    db: AsyncSession,
    voice_session_id: str,
) -> MemoirChatMessage | None:
    return await db.scalar(
        select(MemoirChatMessage)
        .where(
            MemoirChatMessage.voice_session_id == voice_session_id,
            MemoirChatMessage.role == "user",
            MemoirChatMessage.content_text != "",
        )
        .order_by(
            MemoirChatMessage.turn_index.desc(),
            MemoirChatMessage.created_at.desc(),
            MemoirChatMessage.id.desc(),
        )
        .limit(1)
    )


async def consume_opening_offer(
    db: AsyncSession,
    *,
    voice_session: MemoirVoiceSession,
    issue_id: str,
) -> None:
    metadata = voice_session.metadata_json or {}
    if (
        metadata.get(OPENING_OFFER_KEY) != issue_id
        or metadata.get(OPENING_OFFER_STATUS_KEY) != OPENING_OFFER_PENDING
    ):
        return
    next_metadata = {
        **metadata,
        OPENING_OFFER_STATUS_KEY: OPENING_OFFER_CONSUMED,
    }
    next_metadata.pop(OPENING_OFFER_PENDING_AT_KEY, None)
    voice_session.metadata_json = next_metadata
    await db.flush()


async def clear_pending_opening_offer(
    db: AsyncSession,
    *,
    voice_session: MemoirVoiceSession,
) -> bool:
    metadata = voice_session.metadata_json or {}
    if metadata.get(OPENING_OFFER_STATUS_KEY) != OPENING_OFFER_PENDING:
        return False
    next_metadata = dict(metadata)
    for key in (
        OPENING_OFFER_KEY,
        OPENING_OFFER_TRIGGER_MESSAGE_KEY,
        OPENING_OFFER_STATUS_KEY,
        OPENING_OFFER_PENDING_AT_KEY,
    ):
        next_metadata.pop(key, None)
    voice_session.metadata_json = next_metadata
    await db.flush()
    return True


def explicitly_declines_supplement(text: str) -> bool:
    normalized = "".join(text.split())
    return any(phrase in normalized for phrase in SUPPLEMENT_DECLINE_PHRASES)


async def record_supplement_decline(
    db: AsyncSession,
    *,
    voice_session: MemoirVoiceSession,
    latest_user_message: MemoirChatMessage | None,
) -> None:
    if latest_user_message is None or not explicitly_declines_supplement(latest_user_message.content_text):
        return
    metadata = voice_session.metadata_json or {}
    trigger_message_id = metadata.get(OPENING_OFFER_TRIGGER_MESSAGE_KEY)
    if not metadata.get(OPENING_OFFER_KEY) or latest_user_message.id == trigger_message_id:
        return
    if metadata.get(OPENING_OFFER_DECLINED_KEY):
        return
    voice_session.metadata_json = {**metadata, OPENING_OFFER_DECLINED_KEY: True}
    await db.flush()


async def offered_chapters(db: AsyncSession, message_ids: Iterable[str | None]) -> dict[str, str]:
    """For narrator messages answering an opening offer, the chapter the offered question is about.

    Narrator messages before 小余's first follow-up may answer the opening, including replies after
    a no-answer reminder or an interrupted opening. Recovery retains the hint only when its saved
    turn_index is before that follow-up; deleting a reply never moves this boundary. Only the chapter is given:
    chapter issues do not keep their section. The reply stays ordinary narration and the issue is
    untouched."""
    ids = {message_id for message_id in message_ids if message_id}
    if not ids:
        return {}
    follow_up = aliased(MemoirChatMessage)
    first_follow_up = (
        select(func.min(follow_up.turn_index))
        .where(
            follow_up.voice_session_id == MemoirChatMessage.voice_session_id,
            follow_up.role == "assistant",
            or_(follow_up.guidance_intent.is_(None), follow_up.guidance_intent.not_in(OPENING_INTENTS)),
        )
        .scalar_subquery()
    )
    rows = (await db.execute(
        select(
            MemoirChatMessage.id,
            MemoirCompletionIssue,
            MemoirVoiceSession.metadata_json[OPENING_OFFER_TRIGGER_MESSAGE_KEY].as_string(),
        )
        .join(MemoirVoiceSession, MemoirVoiceSession.id == MemoirChatMessage.voice_session_id)
        .join(MemoirCompletionIssue, MemoirCompletionIssue.id == MemoirVoiceSession.metadata_json[OPENING_OFFER_KEY].as_string())
        .where(
            MemoirChatMessage.id.in_(ids),
            MemoirChatMessage.role == "user",
            MemoirChatMessage.content_text != "",
            or_(first_follow_up.is_(None), MemoirChatMessage.turn_index < first_follow_up),
        )
    )).all()
    result = {}
    for message_id, issue, trigger_message_id in rows:
        if trigger_message_id and trigger_message_id == message_id:
            continue
        chapter_ids = (
            [issue.chapter_id]
            if issue.scope == MemoirCompletionIssueScope.CHAPTER.value
            else issue.related_chapter_ids_json
        )
        if len(chapter_ids) == 1 and chapter_ids[0]:
            result[message_id] = chapter_ids[0]
    return result


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

    _, changed_ids = await sections.assign_material_to_section(
        db,
        project_id=voice_session.project_id,
        section_id=await _target_section_id(db, issue=issue),
        utterance_id=utterance.id,
        assigned_by=MemoirSectionMaterialAssignedBy.SYSTEM,
        assignment_reason=f"completion_issue:{issue.id}",
    )
    from app.services.memoir.section_digests import mark_section_digests_stale
    if issue.status != MemoirCompletionIssueStatus.CLOSED.value:
        await mark_section_digests_stale(db, project_id=voice_session.project_id, section_ids=changed_ids)
        await completion_issues.close_issue(
            db,
            project_id=voice_session.project_id,
            issue_id=issue.id,
            outcome=MemoirCompletionIssueOutcome.ANSWERED,
            note="回答已保存并直接归入对应小章节",
            answered_material_id=utterance.id,
        )
    else:
        await sections.finalize_section_changes(db, project_id=voice_session.project_id, section_ids=changed_ids)
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
