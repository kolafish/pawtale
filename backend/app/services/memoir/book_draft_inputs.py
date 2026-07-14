from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import (
    MemoirFollowupAnswerInterpretationStatus,
    MemoirFollowupAnswerKind,
    MemoirFollowupTaskStatus,
)
from app.domain.memoir.types import MemoirChapterSourceLinkStatus, MemoirSemanticCacheStatus
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirBookPlanChapter,
    MemoirChatMessage,
    MemoirChapterSourceLink,
    MemoirSourceCard,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.models.memoir_narrative import MemoirFollowupTask, MemoirFollowupTaskUtterance
from app.schemas.memoir_book import MemoirBookChapterSourceRef
from app.services.memoir.book_plan_sources import text_or_empty
from app.services.memoir.book_source_links import (
    ChapterSourceChangeState,
    chapter_source_ref_dicts,
)
from app.services.memoir.followup_answer_parts import validate_resolved_answer_parts
from app.services.memoir.source_cards import (
    SOURCE_CARD_ENGINE,
    source_text_for_utterance,
    source_text_hash,
)


@dataclass(frozen=True)
class ChapterSourceCardContext:
    source_card_id: str
    summary: str = ""
    people: list[str] | None = None
    places: list[str] | None = None
    time_hints: list[str] | None = None
    event_tags: list[str] | None = None
    key_facts: list[str] | None = None
    open_questions: list[str] | None = None


@dataclass(frozen=True)
class ChapterSourceText:
    ref: MemoirBookChapterSourceRef
    text: str
    change_type: Literal["existing", "new", "changed"] = "existing"
    source_card: ChapterSourceCardContext | None = None


@dataclass(frozen=True)
class ChapterEditorialContext:
    task_id: str
    ref: MemoirBookChapterSourceRef
    question: str
    purpose: str
    answer_kind: MemoirFollowupAnswerKind
    clarification_text: str = ""
    fact_resolution_text: str = ""


@dataclass(frozen=True)
class ChapterDraftInput:
    narrative_sources: list[ChapterSourceText]
    editorial_contexts: list[ChapterEditorialContext]


def utterance_display_text(utterance: MemoirVoiceUtterance) -> str:
    return text_or_empty(utterance.transcript_edited) or text_or_empty(utterance.transcript_raw)


def limited_string_items(value: object, *, limit: int = 6, item_limit: int = 80) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in items:
            items.append(text[:item_limit])
        if len(items) >= limit:
            break
    return items


def _latest_draft_version(
    draft: MemoirBookChapterDraft | None,
) -> MemoirBookChapterDraftVersion | None:
    if not draft or not draft.versions:
        return None
    return sorted(draft.versions, key=lambda version: (version.version, version.created_at, version.id))[-1]


def _source_card_context(card: MemoirSourceCard | None) -> ChapterSourceCardContext | None:
    if not card or card.status != MemoirSemanticCacheStatus.READY.value or not isinstance(card.card_json, dict):
        return None
    key_facts: list[str] = []
    raw_key_facts = card.card_json.get("key_facts")
    if isinstance(raw_key_facts, list):
        for item in raw_key_facts:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            quote = str(item.get("evidence_quote") or "").strip()
            if text and quote:
                key_facts.append(f"{text}（证据：{quote[:80]}）")
            elif text:
                key_facts.append(text[:120])
            if len(key_facts) >= 5:
                break
    return ChapterSourceCardContext(
        source_card_id=card.id,
        summary=str(card.card_json.get("summary") or "").strip()[:260],
        people=limited_string_items(card.card_json.get("people")),
        places=limited_string_items(card.card_json.get("places")),
        time_hints=limited_string_items(card.card_json.get("time_hints")),
        event_tags=limited_string_items(card.card_json.get("event_tags")),
        key_facts=key_facts,
        open_questions=limited_string_items(card.card_json.get("open_questions"), limit=4),
    )


def _source_change_type(
    *,
    utterance_id: str,
    link: MemoirChapterSourceLink | None,
    latest_version: MemoirBookChapterDraftVersion | None,
    pending_source_ids: set[str],
) -> Literal["existing", "new", "changed"]:
    if utterance_id not in pending_source_ids:
        return "existing"
    if not link or not latest_version:
        return "new"
    if link.created_at and link.created_at <= latest_version.created_at:
        return "changed"
    return "new"


async def _active_links_by_utterance(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> dict[str, MemoirChapterSourceLink]:
    rows = (
        await db.execute(
            select(MemoirChapterSourceLink).where(
                MemoirChapterSourceLink.project_id == project_id,
                MemoirChapterSourceLink.chapter_id == chapter_id,
                MemoirChapterSourceLink.status == MemoirChapterSourceLinkStatus.ACTIVE.value,
            )
        )
    ).scalars().all()
    return {link.utterance_id: link for link in rows}


async def _ready_source_cards_by_utterance(
    db: AsyncSession,
    *,
    project_id: str,
    source_text_by_utterance: dict[str, str],
) -> dict[str, MemoirSourceCard]:
    if not source_text_by_utterance:
        return {}
    source_hash_by_utterance = {
        utterance_id: source_text_hash(text)
        for utterance_id, text in source_text_by_utterance.items()
    }
    rows = (
        await db.execute(
            select(MemoirSourceCard).where(
                MemoirSourceCard.project_id == project_id,
                MemoirSourceCard.utterance_id.in_(list(source_hash_by_utterance.keys())),
                MemoirSourceCard.engine == SOURCE_CARD_ENGINE,
                MemoirSourceCard.status == MemoirSemanticCacheStatus.READY.value,
            )
        )
    ).scalars().all()
    return {
        card.utterance_id: card
        for card in rows
        if card.source_text_hash == source_hash_by_utterance.get(card.utterance_id)
    }


def _source_ref(
    *,
    utterance: MemoirVoiceUtterance,
    message: MemoirChatMessage,
    quote: str,
) -> MemoirBookChapterSourceRef:
    return MemoirBookChapterSourceRef(
        voice_session_id=utterance.voice_session_id,
        message_id=message.id,
        utterance_id=utterance.id,
        transcript_version_id=message.transcript_version_id,
        audio_file_id=utterance.audio_file_id or message.audio_file_id,
        start_ms=utterance.start_ms,
        end_ms=utterance.end_ms,
        created_at=utterance.created_at,
        quote=quote,
    )


async def build_chapter_draft_input(
    db: AsyncSession,
    *,
    project_id: str,
    chapter: MemoirBookPlanChapter,
    source_change_state: ChapterSourceChangeState,
) -> ChapterDraftInput:
    refs = [
        MemoirBookChapterSourceRef.model_validate(ref)
        for ref in await chapter_source_ref_dicts(db, chapter=chapter)
        if isinstance(ref, dict)
    ]
    followup_rows = (
        await db.execute(
            select(MemoirFollowupTaskUtterance, MemoirFollowupTask)
            .join(
                MemoirFollowupTask,
                MemoirFollowupTaskUtterance.task_id == MemoirFollowupTask.id,
            )
            .where(
                MemoirFollowupTaskUtterance.project_id == project_id,
                MemoirFollowupTaskUtterance.is_assignment_snapshot.is_(True),
                MemoirFollowupTaskUtterance.interpretation_status
                == MemoirFollowupAnswerInterpretationStatus.RESOLVED.value,
                MemoirFollowupTask.project_id == project_id,
                MemoirFollowupTask.chapter_id == chapter.id,
                MemoirFollowupTask.status == MemoirFollowupTaskStatus.ANSWERED.value,
            )
            .order_by(
                MemoirFollowupTaskUtterance.created_at,
                MemoirFollowupTaskUtterance.id,
            )
        )
    ).all()
    followup_by_utterance: dict[str, tuple[MemoirFollowupTaskUtterance, MemoirFollowupTask]] = {}
    for relation, task in followup_rows:
        followup_by_utterance.setdefault(relation.utterance_id, (relation, task))

    utterance_ids = list(
        dict.fromkeys(
            [ref.utterance_id for ref in refs]
            + [relation.utterance_id for relation, _task in followup_rows]
        )
    )
    result = await db.execute(
        select(MemoirVoiceUtterance, MemoirChatMessage)
        .join(MemoirVoiceSession, MemoirVoiceUtterance.voice_session_id == MemoirVoiceSession.id)
        .join(MemoirChatMessage, MemoirVoiceUtterance.chat_message_id == MemoirChatMessage.id)
        .where(
            MemoirVoiceSession.project_id == project_id,
            MemoirVoiceUtterance.id.in_(utterance_ids),
        )
    )
    by_utterance_id = {
        utterance.id: (utterance, message)
        for utterance, message in result.all()
    }
    answer_kind_by_relation_id: dict[str, MemoirFollowupAnswerKind] = {}
    for relation, _task in followup_rows:
        pair = by_utterance_id.get(relation.utterance_id)
        if not pair:
            raise HTTPException(status_code=400, detail="补全回答对应的讲述不存在")
        try:
            answer_kind = MemoirFollowupAnswerKind(relation.answer_kind)
            validate_resolved_answer_parts(
                source_text=utterance_display_text(pair[0]),
                answer_kind=answer_kind,
                direct_narrative_text=relation.direct_narrative_text,
                clarification_text=relation.clarification_text,
                fact_resolution_text=relation.fact_resolution_text,
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=409,
                detail="补全回答用途与当前转写不一致，请重新确认后再生成章节",
            ) from exc
        answer_kind_by_relation_id[relation.id] = answer_kind

    source_text_by_utterance = {
        ref.utterance_id: source_text_for_utterance(by_utterance_id[ref.utterance_id][0])
        for ref in refs
        if ref.utterance_id in by_utterance_id
    }
    cards_by_utterance = await _ready_source_cards_by_utterance(
        db,
        project_id=project_id,
        source_text_by_utterance=source_text_by_utterance,
    )
    links_by_utterance = await _active_links_by_utterance(
        db,
        project_id=project_id,
        chapter_id=chapter.id,
    )
    latest_version = _latest_draft_version(chapter.draft)
    sources: list[ChapterSourceText] = []
    narrative_utterance_ids: set[str] = set()
    for ref in refs:
        pair = by_utterance_id.get(ref.utterance_id)
        if not pair:
            raise HTTPException(status_code=400, detail="章节来源讲述不存在")
        utterance, message = pair
        raw_text = utterance_display_text(utterance)
        if not raw_text:
            raise HTTPException(status_code=400, detail="章节来源讲述为空")
        followup = followup_by_utterance.get(utterance.id)
        direct_text = followup[0].direct_narrative_text.strip() if followup else ""
        if followup and not direct_text:
            continue
        narrative_text = direct_text or raw_text
        sources.append(
            ChapterSourceText(
                ref=_source_ref(
                    utterance=utterance,
                    message=message,
                    quote=direct_text or ref.quote,
                ),
                text=narrative_text,
                change_type=_source_change_type(
                    utterance_id=ref.utterance_id,
                    link=links_by_utterance.get(ref.utterance_id),
                    latest_version=latest_version,
                    pending_source_ids=source_change_state.pending_source_ids,
                ),
                source_card=(
                    _source_card_context(cards_by_utterance.get(ref.utterance_id))
                    if narrative_text == raw_text
                    else None
                ),
            )
        )
        narrative_utterance_ids.add(utterance.id)

    editorial_contexts: list[ChapterEditorialContext] = []
    for relation, task in followup_rows:
        utterance, message = by_utterance_id[relation.utterance_id]
        direct_text = relation.direct_narrative_text.strip()
        clarification_text = relation.clarification_text.strip()
        fact_resolution_text = relation.fact_resolution_text.strip()
        if direct_text and utterance.id not in narrative_utterance_ids:
            sources.append(
                ChapterSourceText(
                    ref=_source_ref(
                        utterance=utterance,
                        message=message,
                        quote=direct_text,
                    ),
                    text=direct_text,
                )
            )
            narrative_utterance_ids.add(utterance.id)
        if clarification_text or fact_resolution_text:
            editorial_contexts.append(
                ChapterEditorialContext(
                    task_id=task.id,
                    ref=_source_ref(
                        utterance=utterance,
                        message=message,
                        quote="",
                    ),
                    question=task.question,
                    purpose=task.purpose,
                    answer_kind=answer_kind_by_relation_id[relation.id],
                    clarification_text=clarification_text,
                    fact_resolution_text=fact_resolution_text,
                )
            )
    if not sources:
        raise HTTPException(status_code=400, detail="章节缺少可写入正文的来源讲述")
    return ChapterDraftInput(
        narrative_sources=sources,
        editorial_contexts=editorial_contexts,
    )
