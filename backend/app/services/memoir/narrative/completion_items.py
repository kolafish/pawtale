from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import (
    MEMOIR_STORY_NARRATION_TARGET,
    MemoirCompletionItemStatus,
    MemoirNarrativeGapDetectedBy,
    MemoirNarrativeGapRecommendedAction,
    MemoirNarrativeGapSeverity,
    MemoirNarrativeGapType,
)
from app.domain.memoir.types import MemoirSessionGoal
from app.models.memoir import MemoirVoiceSession, MemoirVoiceUtterance
from app.models.memoir_narrative import MemoirCompletionItem, MemoirStory
from app.schemas.memoir_narrative import MemoirNarrativeGapCandidate
from app.services.memoir.narrative import completion_planner


ACTIVE_STATUSES = {
    MemoirCompletionItemStatus.OPEN.value,
    MemoirCompletionItemStatus.IN_PROGRESS.value,
    MemoirCompletionItemStatus.DEFERRED.value,
}


@dataclass(frozen=True)
class CompletionPromptContext:
    item_id: str
    question: str
    prompt_text: str


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def _clear_session_bindings(
    db: AsyncSession,
    *,
    project_id: str,
    item_id: str,
) -> None:
    await db.execute(
        update(MemoirVoiceSession)
        .where(
            MemoirVoiceSession.project_id == project_id,
            MemoirVoiceSession.current_completion_item_id == item_id,
        )
        .values(current_completion_item_id=None)
    )


def item_fingerprint(candidate: MemoirNarrativeGapCandidate) -> str:
    payload = {
        "scope": candidate.scope_type.value,
        "story": candidate.primary_story_id,
        "chapter": candidate.primary_chapter_id,
        "type": candidate.gap_type.value,
        "dimension": candidate.target_slot,
        "title": candidate.title,
        "claims": sorted(candidate.claim_roles),
        "stories": sorted(candidate.involved_story_ids),
        "chapters": sorted(candidate.involved_chapter_ids),
    }
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return sha256(encoded.encode("utf-8")).hexdigest()


def _dimension(candidate: MemoirNarrativeGapCandidate) -> str:
    if candidate.target_slot.startswith("semantic:"):
        return candidate.target_slot.split(":", 2)[1]
    return candidate.target_slot


def _question(
    candidate: MemoirNarrativeGapCandidate,
    *,
    story_title: str = "",
) -> str:
    prefix = f"关于“{story_title}”，" if story_title else ""
    dimension_questions = {
        "chapter_context": "这段内容开始前，读者还需要知道哪些重要背景？",
        "opening": "如果从一个具体场景开始这一章，你最先想到的是哪一幕？",
        "closing": "讲完这些经历后，你最想让读者记住什么？",
        "chapter_focus": "这一章里哪段经历最能说明你当时的变化？",
        "person_introduction": "这里提到的关键人物分别是谁？他们彼此是什么关系？",
        "relationship_clarity": "这里提到的人彼此是什么关系？",
        "transition": "前后两段经历之间发生了什么变化？",
        "list_style": "这些经历里哪一件最能代表那段日子？",
    }
    dimension = _dimension(candidate)
    if dimension == MEMOIR_STORY_NARRATION_TARGET:
        return f"{prefix}请讲清最关键的经过，以及它后来带来的影响。"
    if dimension in dimension_questions:
        return dimension_questions[dimension]
    questions = {
        MemoirNarrativeGapType.MISSING_SLOT: "你愿意再讲一点最关键的经过和影响吗？",
        MemoirNarrativeGapType.CHRONOLOGY: "这些事情先后是怎样发生的？",
        MemoirNarrativeGapType.RELATIONSHIP: "这里提到的人彼此是什么关系？",
        MemoirNarrativeGapType.CAUSALITY: "是什么促成了这件事，后来又带来了什么？",
        MemoirNarrativeGapType.CONTRADICTION: "这里有两种不同的说法，哪一种更准确？",
        MemoirNarrativeGapType.REPETITION: "这两段内容是否在说同一件事？",
        MemoirNarrativeGapType.UNCLEAR_REFERENCE: "这里说的是谁，或者具体是哪件事？",
        MemoirNarrativeGapType.UNSUPPORTED_FACT: "这条信息来自亲身经历、他人讲述，还是推测？",
    }
    return f"{prefix}{questions[candidate.gap_type]}"


def _priority(candidate: MemoirNarrativeGapCandidate) -> int:
    severity = {
        MemoirNarrativeGapSeverity.HIGH: 300,
        MemoirNarrativeGapSeverity.MEDIUM: 200,
        MemoirNarrativeGapSeverity.LOW: 100,
    }[candidate.severity]
    dimension_bonus = 20 if _dimension(candidate) in {
        "chapter_context",
        "chapter_focus",
        "opening",
        "closing",
        "person_introduction",
        "relationship_clarity",
        "transition",
    } else 0
    return severity + dimension_bonus


def _primary_ids(
    candidate: MemoirNarrativeGapCandidate,
) -> tuple[str | None, str | None, str | None, str | None]:
    chapter_id = candidate.primary_chapter_id or next(
        iter(sorted(candidate.involved_chapter_ids)), None
    )
    story_id = candidate.primary_story_id or next(
        iter(sorted(candidate.involved_story_ids)), None
    )
    claim_ids = sorted(candidate.claim_roles)
    claim_id = claim_ids[0] if claim_ids else None
    related_claim_id = claim_ids[1] if len(claim_ids) > 1 else None
    return chapter_id, story_id, claim_id, related_claim_id


async def upsert_candidates(
    db: AsyncSession,
    *,
    project_id: str,
    candidates: list[MemoirNarrativeGapCandidate],
) -> tuple[list[MemoirCompletionItem], int]:
    fingerprints = [item_fingerprint(candidate) for candidate in candidates]
    existing = {
        item.fingerprint: item
        for item in (
            await db.scalars(
                select(MemoirCompletionItem).where(
                    MemoirCompletionItem.project_id == project_id,
                    MemoirCompletionItem.fingerprint.in_(fingerprints),
                )
            )
        ).all()
    } if fingerprints else {}
    story_ids = {
        story_id
        for candidate in candidates
        for story_id in [
            candidate.primary_story_id,
            *sorted(candidate.involved_story_ids),
        ]
        if story_id
    }
    story_titles = {
        story.id: story.title
        for story in (
            await db.scalars(
                select(MemoirStory).where(
                    MemoirStory.project_id == project_id,
                    MemoirStory.id.in_(story_ids),
                )
            )
        ).all()
    } if story_ids else {}

    items: list[MemoirCompletionItem] = []
    created = 0
    now = _utcnow()
    for candidate, fingerprint in zip(candidates, fingerprints, strict=True):
        action = completion_planner.action_for_candidate(candidate)
        if action == MemoirNarrativeGapRecommendedAction.EDITORIAL:
            continue
        chapter_id, story_id, claim_id, related_claim_id = _primary_ids(candidate)
        values = {
            "scope_type": candidate.scope_type.value,
            "chapter_id": chapter_id,
            "story_id": story_id,
            "claim_id": claim_id,
            "related_claim_id": related_claim_id,
            "item_type": candidate.gap_type.value,
            "severity": candidate.severity.value,
            "dimension": _dimension(candidate),
            "title": candidate.title,
            "description": candidate.description,
            "action": action.value,
            "question": _question(
                candidate,
                story_title=story_titles.get(story_id or "", ""),
            ),
            "priority": _priority(candidate),
            "detected_by": candidate.detected_by.value,
            "last_detected_at": now,
        }
        item = existing.get(fingerprint)
        if item is None:
            item = MemoirCompletionItem(
                project_id=project_id,
                fingerprint=fingerprint,
                **values,
            )
            db.add(item)
            created += 1
        else:
            for key, value in values.items():
                setattr(item, key, value)
            item.detection_count += 1
            db.add(item)
        items.append(item)
    await db.flush()
    return items, created


async def reconcile_candidates(
    db: AsyncSession,
    *,
    project_id: str,
    candidates: list[MemoirNarrativeGapCandidate],
    chapter_id: str | None = None,
    detected_by: MemoirNarrativeGapDetectedBy | None = None,
) -> int:
    seen = {item_fingerprint(candidate) for candidate in candidates}
    filters = [
        MemoirCompletionItem.project_id == project_id,
        MemoirCompletionItem.status.in_(
            {
                MemoirCompletionItemStatus.OPEN.value,
                MemoirCompletionItemStatus.DEFERRED.value,
            }
        ),
    ]
    if chapter_id is not None:
        filters.append(MemoirCompletionItem.chapter_id == chapter_id)
    if detected_by is not None:
        filters.append(MemoirCompletionItem.detected_by == detected_by.value)
    items = list((await db.scalars(select(MemoirCompletionItem).where(*filters))).all())
    now = _utcnow()
    resolved = 0
    for item in items:
        if item.fingerprint in seen:
            continue
        item.status = MemoirCompletionItemStatus.RESOLVED.value
        item.outcome_note = "重新检查后已不再需要处理"
        item.completed_at = now
        item.defer_until = None
        db.add(item)
        resolved += 1
    await db.flush()
    return resolved


async def get_item(
    db: AsyncSession,
    *,
    project_id: str,
    item_id: str,
) -> MemoirCompletionItem | None:
    return await db.scalar(
        select(MemoirCompletionItem).where(
            MemoirCompletionItem.project_id == project_id,
            MemoirCompletionItem.id == item_id,
        )
    )


async def resolve_item(
    db: AsyncSession,
    *,
    project_id: str,
    item_id: str,
    status: MemoirCompletionItemStatus,
    note: str,
) -> MemoirCompletionItem:
    item = await get_item(db, project_id=project_id, item_id=item_id)
    if item is None:
        raise ValueError("补全项不存在")
    if status not in {
        MemoirCompletionItemStatus.RESOLVED,
        MemoirCompletionItemStatus.UNABLE_TO_ANSWER,
    }:
        raise ValueError("补全项结果状态无效")
    if item.status == status.value:
        await _clear_session_bindings(
            db,
            project_id=project_id,
            item_id=item.id,
        )
        return item
    if item.status not in ACTIVE_STATUSES:
        raise ValueError("补全项已经结束，不能再次修改结果")
    now = _utcnow()
    item.status = status.value
    item.defer_until = None
    item.outcome_note = note.strip()
    item.completed_at = now
    db.add(item)
    await _clear_session_bindings(
        db,
        project_id=project_id,
        item_id=item.id,
    )
    await db.flush()
    return item


async def defer_item(
    db: AsyncSession,
    *,
    project_id: str,
    item_id: str,
    note: str,
    defer_until: datetime | None,
) -> MemoirCompletionItem:
    item = await get_item(db, project_id=project_id, item_id=item_id)
    if item is None or item.status not in ACTIVE_STATUSES:
        raise ValueError("补全项当前不能暂时跳过")
    item.status = MemoirCompletionItemStatus.DEFERRED.value
    item.defer_until = defer_until
    item.outcome_note = note.strip()
    db.add(item)
    await _clear_session_bindings(
        db,
        project_id=project_id,
        item_id=item.id,
    )
    await db.flush()
    return item


async def select_next_item(
    db: AsyncSession,
    *,
    project_id: str,
    preferred_chapter_id: str | None = None,
    exclude_item_id: str | None = None,
) -> MemoirCompletionItem | None:
    now = _utcnow()
    due = list(
        (
            await db.scalars(
                select(MemoirCompletionItem).where(
                    MemoirCompletionItem.project_id == project_id,
                    MemoirCompletionItem.status
                    == MemoirCompletionItemStatus.DEFERRED.value,
                    MemoirCompletionItem.defer_until.is_not(None),
                    MemoirCompletionItem.defer_until <= now,
                )
            )
        ).all()
    )
    for item in due:
        item.status = MemoirCompletionItemStatus.OPEN.value
        item.defer_until = None
        db.add(item)
    items = list(
        (
            await db.scalars(
                select(MemoirCompletionItem)
                .where(
                    MemoirCompletionItem.project_id == project_id,
                    MemoirCompletionItem.status
                    == MemoirCompletionItemStatus.OPEN.value,
                    MemoirCompletionItem.action
                    == MemoirNarrativeGapRecommendedAction.INTERVIEW.value,
                )
                .order_by(
                    MemoirCompletionItem.priority.desc(),
                    MemoirCompletionItem.created_at,
                    MemoirCompletionItem.id,
                )
            )
        ).all()
    )
    if exclude_item_id:
        items = [item for item in items if item.id != exclude_item_id]
    items.sort(
        key=lambda item: (
            item.chapter_id != preferred_chapter_id if preferred_chapter_id else False,
            -item.priority,
            item.created_at,
            item.id,
        )
    )
    return items[0] if items else None


async def start_item_for_session(
    db: AsyncSession,
    *,
    item: MemoirCompletionItem,
    voice_session: MemoirVoiceSession,
) -> MemoirCompletionItem:
    if item.project_id != voice_session.project_id:
        raise ValueError("补全项和访谈会话不属于同一项目")
    if voice_session.session_goal != MemoirSessionGoal.BOOK_COMPLETION.value:
        raise ValueError("补全项只能用于成书补全会话")
    if item.status not in {
        MemoirCompletionItemStatus.OPEN.value,
        MemoirCompletionItemStatus.DEFERRED.value,
    }:
        raise ValueError("补全项当前不可开始")
    item.status = MemoirCompletionItemStatus.IN_PROGRESS.value
    item.defer_until = None
    item.outcome_note = ""
    item.started_at = _utcnow()
    voice_session.current_completion_item_id = item.id
    db.add_all([item, voice_session])
    await db.flush()
    return item


async def release_session_item(
    db: AsyncSession,
    *,
    voice_session: MemoirVoiceSession,
    reason: str,
) -> MemoirCompletionItem | None:
    if not voice_session.current_completion_item_id:
        return None
    item = await get_item(
        db,
        project_id=voice_session.project_id,
        item_id=voice_session.current_completion_item_id,
    )
    if item is not None and item.status == MemoirCompletionItemStatus.IN_PROGRESS.value:
        item.status = MemoirCompletionItemStatus.DEFERRED.value
        item.defer_until = None
        item.outcome_note = reason.strip()
        db.add(item)
    voice_session.current_completion_item_id = None
    db.add(voice_session)
    await db.flush()
    return item


async def prompt_context_for_session(
    db: AsyncSession,
    *,
    voice_session: MemoirVoiceSession,
) -> CompletionPromptContext | None:
    if (
        voice_session.session_goal != MemoirSessionGoal.BOOK_COMPLETION.value
        or not voice_session.current_completion_item_id
    ):
        return None
    item = await get_item(
        db,
        project_id=voice_session.project_id,
        item_id=voice_session.current_completion_item_id,
    )
    if (
        item is None
        or item.status != MemoirCompletionItemStatus.IN_PROGRESS.value
    ):
        return None
    prompt_text = "\n".join(
        part
        for part in (
            f"当前唯一补全问题：{item.question}",
            f"目的：{item.description}" if item.description else "",
        )
        if part
    )
    return CompletionPromptContext(
        item_id=item.id,
        question=item.question,
        prompt_text=prompt_text,
    )


async def link_utterance_answer(
    db: AsyncSession,
    *,
    project_id: str,
    utterance: MemoirVoiceUtterance,
) -> MemoirCompletionItem | None:
    item_id = utterance.completion_item_id
    if not item_id:
        return None
    item = await get_item(db, project_id=project_id, item_id=item_id)
    if item is None or item.status not in ACTIVE_STATUSES:
        return item
    return await resolve_item(
        db,
        project_id=project_id,
        item_id=item.id,
        status=MemoirCompletionItemStatus.RESOLVED,
        note="回答已保存并进入章节素材",
    )
