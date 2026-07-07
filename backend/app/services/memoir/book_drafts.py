from __future__ import annotations

import logging

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirBookPlanStatus,
    MemoirChapterSourceLinkStatus,
    MemoirCreatedBy,
    MemoirSemanticCacheStatus,
)
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirChatMessage,
    MemoirChapterCard,
    MemoirChapterSourceLink,
    MemoirSourceCard,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.schemas.memoir_book import (
    MemoirBookAudioRequest,
    MemoirBookAudioResponse,
    MemoirBookChapterDraftGenerateRequest,
    MemoirBookChapterDraftResponse,
    MemoirBookChapterDraftUpdateRequest,
    MemoirBookChapterDraftVersionResponse,
    MemoirBookChapterSourceRef,
)
from app.services.ai_factory import get_ai_service
from app.services.ai_types import AITextClient
from app.services.memoir.access import load_memoir_project
from app.services.memoir.book_audio import (
    audio_cache_payload,
    audio_response,
    cached_audio_response,
    discard_staged_audio_files,
    effective_tts_speed_ratio,
    source_text_hash,
    synthesize_text_audio,
)
from app.services.memoir.book_draft_ai import (
    AI_BOOK_DRAFT_UNAVAILABLE_MESSAGE,
    ChapterCardPromptContext,
    ChapterSourceCardContext,
    ChapterSourceText,
    RemovedChapterSourceText,
    generate_chapter_draft_text,
)
from app.services.memoir.book_plan_sources import (
    DRAFT_STATUS_REASON_KEY,
    REMOVED_SOURCE_UTTERANCE_IDS_KEY,
    metadata_without_keys as _metadata_without_keys,
    source_change_metadata_keys as _source_change_metadata_keys,
    source_change_counts as _source_change_counts,
    text_or_empty as _text_or_empty,
)
from app.services.memoir.book_source_links import (
    ChapterSourceChangeState,
    chapter_source_change_state as _chapter_source_change_state,
    chapter_source_ref_dicts as _chapter_source_ref_dicts,
    mark_chapter_sources_merged as _mark_chapter_sources_merged,
)
from app.services.memoir.chapter_cards import CHAPTER_CARD_ENGINE, build_chapter_card_context
from app.services.memoir.chapter_event_clusters import get_or_build_chapter_event_clusters
from app.services.memoir.source_cards import (
    SOURCE_CARD_ENGINE,
    source_text_for_utterance as _source_card_text_for_utterance,
    source_text_hash as _source_card_text_hash,
)
from app.services.memoir.voice_tts_types import TTSClient


CHAPTER_TTS_CACHE_KEY = "chapter_tts"
logger = logging.getLogger(__name__)


def _display_text(utterance: MemoirVoiceUtterance) -> str:
    return _text_or_empty(utterance.transcript_edited) or _text_or_empty(utterance.transcript_raw)


def _latest_draft_version(draft: MemoirBookChapterDraft | None) -> MemoirBookChapterDraftVersion | None:
    if not draft or not draft.versions:
        return None
    return sorted(draft.versions, key=lambda version: (version.version, version.created_at, version.id))[-1]


def _string_items(value: object, *, limit: int = 6, item_limit: int = 80) -> list[str]:
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
        people=_string_items(card.card_json.get("people")),
        places=_string_items(card.card_json.get("places")),
        time_hints=_string_items(card.card_json.get("time_hints")),
        event_tags=_string_items(card.card_json.get("event_tags")),
        key_facts=key_facts,
        open_questions=_string_items(card.card_json.get("open_questions"), limit=4),
    )


def _chapter_card_context(card: MemoirChapterCard | None) -> ChapterCardPromptContext | None:
    if not card or card.status != MemoirSemanticCacheStatus.READY.value or not isinstance(card.card_json, dict):
        return None
    gaps: list[str] = []
    raw_gaps = card.card_json.get("gaps")
    if isinstance(raw_gaps, list):
        for item in raw_gaps:
            if not isinstance(item, dict):
                continue
            message = str(item.get("message") or "").strip()
            severity = str(item.get("severity") or "").strip()
            if message:
                gaps.append(f"{severity or 'medium'}: {message[:140]}")
            if len(gaps) >= 4:
                break
    return ChapterCardPromptContext(
        chapter_card_id=card.id,
        summary=str(card.card_json.get("summary") or "").strip()[:300],
        covered=card.card_json.get("covered") if isinstance(card.card_json.get("covered"), dict) else {},
        gaps=gaps,
        next_questions=_string_items(card.card_json.get("next_questions"), limit=4),
    )


def _source_change_type(
    *,
    utterance_id: str,
    link: MemoirChapterSourceLink | None,
    latest_version: MemoirBookChapterDraftVersion | None,
    pending_source_ids: set[str],
) -> str:
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
        utterance_id: _source_card_text_hash(text)
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


async def _ready_chapter_card_context(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> ChapterCardPromptContext | None:
    try:
        context = await build_chapter_card_context(db, project_id=project_id, chapter_id=chapter_id)
        card = await db.scalar(
            select(MemoirChapterCard).where(
                MemoirChapterCard.project_id == project_id,
                MemoirChapterCard.chapter_id == chapter_id,
                MemoirChapterCard.engine == CHAPTER_CARD_ENGINE,
                MemoirChapterCard.input_signature == context.input_signature,
                MemoirChapterCard.status == MemoirSemanticCacheStatus.READY.value,
            )
        )
    except Exception:
        logger.exception("memoir chapter draft chapter card context failed chapter_id=%s", chapter_id)
        return None
    return _chapter_card_context(card)


def _version_response(version: MemoirBookChapterDraftVersion) -> MemoirBookChapterDraftVersionResponse:
    return MemoirBookChapterDraftVersionResponse(
        id=version.id,
        draft_id=version.draft_id,
        project_id=version.project_id,
        chapter_id=version.chapter_id,
        version=version.version,
        content=version.content,
        created_by=MemoirCreatedBy(version.created_by),
        model=version.model,
        prompt_snapshot=version.prompt_snapshot,
        feedback=version.feedback,
        created_at=version.created_at,
    )


def _draft_response(
    draft: MemoirBookChapterDraft,
    *,
    chapter_metadata_json: dict | None = None,
    pending_source_count: int | None = None,
    removed_source_count: int | None = None,
) -> MemoirBookChapterDraftResponse:
    versions = [_version_response(version) for version in draft.versions]
    if pending_source_count is None or removed_source_count is None:
        pending_source_count, removed_source_count = _source_change_counts(chapter_metadata_json)
    return MemoirBookChapterDraftResponse(
        id=draft.id,
        project_id=draft.project_id,
        chapter_id=draft.chapter_id,
        status=MemoirBookChapterDraftStatus(draft.status),
        metadata_json=draft.metadata_json or {},
        pending_source_count=pending_source_count,
        removed_source_count=removed_source_count,
        latest_version=versions[-1] if versions else None,
        versions=versions,
        created_at=draft.created_at,
        updated_at=draft.updated_at,
    )


async def _load_chapter(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    chapter_id: str,
) -> MemoirBookPlanChapter:
    await load_memoir_project(db, project_id, user_id)
    chapter = await db.scalar(
        select(MemoirBookPlanChapter)
        .join(MemoirBookPlan, MemoirBookPlanChapter.plan_id == MemoirBookPlan.id)
        .where(
            MemoirBookPlan.project_id == project_id,
            MemoirBookPlanChapter.id == chapter_id,
        )
        .options(
            selectinload(MemoirBookPlanChapter.plan),
            selectinload(MemoirBookPlanChapter.draft).selectinload(MemoirBookChapterDraft.versions),
        )
        .execution_options(populate_existing=True)
    )
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    return chapter


def _require_confirmed_plan(chapter: MemoirBookPlanChapter) -> None:
    if chapter.plan.status != MemoirBookPlanStatus.CONFIRMED.value:
        raise HTTPException(status_code=400, detail="请先确认章节规划")


async def _chapter_sources(
    db: AsyncSession,
    *,
    project_id: str,
    chapter: MemoirBookPlanChapter,
    source_change_state: ChapterSourceChangeState,
) -> list[ChapterSourceText]:
    refs = [
        MemoirBookChapterSourceRef.model_validate(ref)
        for ref in await _chapter_source_ref_dicts(db, chapter=chapter)
        if isinstance(ref, dict)
    ]
    if not refs:
        raise HTTPException(status_code=400, detail="章节缺少来源讲述")
    utterance_ids = [ref.utterance_id for ref in refs]
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
    source_text_by_utterance: dict[str, str] = {}
    for ref in refs:
        pair = by_utterance_id.get(ref.utterance_id)
        if pair:
            source_text_by_utterance[ref.utterance_id] = _source_card_text_for_utterance(pair[0])
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
    for ref in refs:
        pair = by_utterance_id.get(ref.utterance_id)
        if not pair:
            raise HTTPException(status_code=400, detail="章节来源讲述不存在")
        utterance, message = pair
        text = _display_text(utterance)
        if not text:
            raise HTTPException(status_code=400, detail="章节来源讲述为空")
        sources.append(
            ChapterSourceText(
                ref=MemoirBookChapterSourceRef(
                    voice_session_id=utterance.voice_session_id,
                    message_id=message.id,
                    utterance_id=utterance.id,
                    transcript_version_id=message.transcript_version_id,
                    audio_file_id=utterance.audio_file_id or message.audio_file_id,
                    start_ms=utterance.start_ms,
                    end_ms=utterance.end_ms,
                    created_at=utterance.created_at,
                    quote=ref.quote,
                ),
                text=text,
                change_type=_source_change_type(
                    utterance_id=ref.utterance_id,
                    link=links_by_utterance.get(ref.utterance_id),
                    latest_version=latest_version,
                    pending_source_ids=source_change_state.pending_source_ids,
                ),
                source_card=_source_card_context(cards_by_utterance.get(ref.utterance_id)),
            )
        )
    return sources


async def _removed_chapter_sources(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    removed_source_ids: set[str],
) -> list[RemovedChapterSourceText]:
    if not removed_source_ids:
        return []
    links = (
        await db.execute(
            select(MemoirChapterSourceLink).where(
                MemoirChapterSourceLink.project_id == project_id,
                MemoirChapterSourceLink.chapter_id == chapter_id,
                MemoirChapterSourceLink.utterance_id.in_(removed_source_ids),
                MemoirChapterSourceLink.status == MemoirChapterSourceLinkStatus.REMOVED.value,
            )
        )
    ).scalars().all()
    links_by_utterance = {link.utterance_id: link for link in links}
    existing_utterances = (
        await db.execute(
            select(MemoirVoiceUtterance)
            .join(MemoirVoiceSession, MemoirVoiceUtterance.voice_session_id == MemoirVoiceSession.id)
            .where(
                MemoirVoiceSession.project_id == project_id,
                MemoirVoiceUtterance.id.in_(removed_source_ids),
            )
        )
    ).scalars().all()
    text_by_utterance = {utterance.id: _display_text(utterance) for utterance in existing_utterances}
    return [
        RemovedChapterSourceText(
            utterance_id=utterance_id,
            quote=(links_by_utterance.get(utterance_id).quote if links_by_utterance.get(utterance_id) else ""),
            text=text_by_utterance.get(utterance_id, ""),
        )
        for utterance_id in sorted(removed_source_ids)
    ]


async def _load_draft(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> MemoirBookChapterDraft | None:
    return await db.scalar(
        select(MemoirBookChapterDraft)
        .where(
            MemoirBookChapterDraft.project_id == project_id,
            MemoirBookChapterDraft.chapter_id == chapter_id,
        )
        .options(selectinload(MemoirBookChapterDraft.versions))
        .execution_options(populate_existing=True)
    )


async def _latest_version_number(db: AsyncSession, draft_id: str) -> int:
    value = await db.scalar(
        select(func.max(MemoirBookChapterDraftVersion.version)).where(
            MemoirBookChapterDraftVersion.draft_id == draft_id
        )
    )
    return int(value or 0)


async def _append_version(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    content: str,
    created_by: MemoirCreatedBy,
    model: str | None,
    prompt_snapshot: str,
    feedback: str | None = None,
    chapter: MemoirBookPlanChapter | None = None,
    clear_source_assignment_state: bool = False,
) -> MemoirBookChapterDraftResponse:
    draft = await _load_draft(db, project_id=project_id, chapter_id=chapter_id)
    if not draft:
        draft = MemoirBookChapterDraft(
            project_id=project_id,
            chapter_id=chapter_id,
            status=MemoirBookChapterDraftStatus.DRAFT.value,
        )
        db.add(draft)
        await db.flush()
    else:
        draft.status = MemoirBookChapterDraftStatus.DRAFT.value
    if clear_source_assignment_state:
        draft.metadata_json = _metadata_without_keys(draft.metadata_json, {DRAFT_STATUS_REASON_KEY})
        if chapter:
            chapter.metadata_json = _metadata_without_keys(
                chapter.metadata_json,
                _source_change_metadata_keys(),
            )
            db.add(chapter)

    version_number = await _latest_version_number(db, draft.id) + 1
    version = MemoirBookChapterDraftVersion(
        draft_id=draft.id,
        project_id=project_id,
        chapter_id=chapter_id,
        version=version_number,
        content=content,
        created_by=created_by.value,
        model=model,
        prompt_snapshot=prompt_snapshot,
        feedback=feedback,
    )
    db.add(version)
    await db.flush()
    if clear_source_assignment_state:
        await _mark_chapter_sources_merged(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
            draft_version_id=version.id,
            include_removed=True,
        )
    await db.commit()
    refreshed = await _load_draft(db, project_id=project_id, chapter_id=chapter_id)
    if not refreshed:
        raise HTTPException(status_code=500, detail="章节稿保存失败")
    return _draft_response(
        refreshed,
        chapter_metadata_json=chapter.metadata_json if chapter else None,
        pending_source_count=0 if clear_source_assignment_state else None,
        removed_source_count=0 if clear_source_assignment_state else None,
    )


async def get_chapter_draft(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    chapter_id: str,
) -> MemoirBookChapterDraftResponse:
    chapter = await _load_chapter(db, user_id=user_id, project_id=project_id, chapter_id=chapter_id)
    draft = await _load_draft(db, project_id=project_id, chapter_id=chapter_id)
    if not draft:
        raise HTTPException(status_code=404, detail="章节稿不存在")
    source_change_state = await _chapter_source_change_state(db, chapter=chapter)
    return _draft_response(
        draft,
        chapter_metadata_json=chapter.metadata_json,
        pending_source_count=source_change_state.pending_source_count,
        removed_source_count=source_change_state.removed_source_count,
    )


async def generate_chapter_draft(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    chapter_id: str,
    req: MemoirBookChapterDraftGenerateRequest,
    ai_client: AITextClient | None = None,
) -> MemoirBookChapterDraftResponse:
    chapter = await _load_chapter(db, user_id=user_id, project_id=project_id, chapter_id=chapter_id)
    _require_confirmed_plan(chapter)
    existing = await _load_draft(db, project_id=project_id, chapter_id=chapter_id)
    source_change_state = await _chapter_source_change_state(db, chapter=chapter)
    pending_source_ids = source_change_state.pending_source_ids
    removed_source_ids = source_change_state.removed_source_ids
    if (
        existing
        and existing.versions
        and not req.force_regenerate
        and not pending_source_ids
        and not removed_source_ids
    ):
        return _draft_response(
            existing,
            chapter_metadata_json=chapter.metadata_json,
            pending_source_count=source_change_state.pending_source_count,
            removed_source_count=source_change_state.removed_source_count,
        )

    sources = await _chapter_sources(
        db,
        project_id=project_id,
        chapter=chapter,
        source_change_state=source_change_state,
    )
    removed_sources = await _removed_chapter_sources(
        db,
        project_id=project_id,
        chapter_id=chapter.id,
        removed_source_ids=removed_source_ids,
    )
    chapter_card = await _ready_chapter_card_context(db, project_id=project_id, chapter_id=chapter.id)
    client = ai_client
    try:
        if client is None:
            client = get_ai_service(req.ai_model)
        previous_content = (
            existing.versions[-1].content
            if existing and existing.versions
            else None
        )

        async def event_cluster_provider():
            return await get_or_build_chapter_event_clusters(
                db,
                client=client,
                chapter=chapter,
                sources=sources,
            )

        generated = await generate_chapter_draft_text(
            client,
            chapter=chapter,
            sources=sources,
            feedback=req.feedback,
            previous_content=previous_content,
            pending_source_utterance_ids=pending_source_ids,
            removed_sources=removed_sources,
            chapter_card=chapter_card,
            event_cluster_provider=event_cluster_provider,
            force_regenerate=req.force_regenerate,
        )
    except HTTPException as exc:
        if exc.status_code < 500:
            raise
        logger.exception("AI memoir chapter draft failed provider=%s", getattr(client, "provider_name", "unknown"))
        raise HTTPException(status_code=503, detail=AI_BOOK_DRAFT_UNAVAILABLE_MESSAGE) from exc
    except Exception as exc:
        logger.exception("AI memoir chapter draft failed provider=%s", getattr(client, "provider_name", "unknown"))
        raise HTTPException(status_code=503, detail=AI_BOOK_DRAFT_UNAVAILABLE_MESSAGE) from exc

    return await _append_version(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        content=generated.content,
        created_by=MemoirCreatedBy.AI,
        model=getattr(client, "model_name", req.ai_model),
        prompt_snapshot=generated.prompt_snapshot,
        feedback=req.feedback,
        chapter=chapter,
        clear_source_assignment_state=True,
    )


async def update_chapter_draft(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    chapter_id: str,
    req: MemoirBookChapterDraftUpdateRequest,
) -> MemoirBookChapterDraftResponse:
    chapter = await _load_chapter(db, user_id=user_id, project_id=project_id, chapter_id=chapter_id)
    _require_confirmed_plan(chapter)
    return await _append_version(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        content=req.content,
        created_by=MemoirCreatedBy.USER,
        model=None,
        prompt_snapshot="",
        chapter=chapter,
        clear_source_assignment_state=True,
    )


async def confirm_chapter_draft(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    chapter_id: str,
) -> MemoirBookChapterDraftResponse:
    chapter = await _load_chapter(db, user_id=user_id, project_id=project_id, chapter_id=chapter_id)
    _require_confirmed_plan(chapter)
    source_change_state = await _chapter_source_change_state(db, chapter=chapter)
    if source_change_state.pending_source_ids:
        raise HTTPException(status_code=400, detail="请先补写新增来源")
    draft = await _load_draft(db, project_id=project_id, chapter_id=chapter_id)
    if not draft or not draft.versions:
        raise HTTPException(status_code=400, detail="章节稿为空，无法确认")
    draft.status = MemoirBookChapterDraftStatus.CONFIRMED.value
    draft.metadata_json = _metadata_without_keys(
        draft.metadata_json,
        {DRAFT_STATUS_REASON_KEY, REMOVED_SOURCE_UTTERANCE_IDS_KEY},
    )
    chapter.metadata_json = _metadata_without_keys(chapter.metadata_json, _source_change_metadata_keys())
    db.add(chapter)
    await _mark_chapter_sources_merged(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        draft_version_id=draft.versions[-1].id,
        include_removed=True,
    )
    await db.commit()
    refreshed = await _load_draft(db, project_id=project_id, chapter_id=chapter_id)
    if not refreshed:
        raise HTTPException(status_code=500, detail="章节稿确认失败")
    return _draft_response(
        refreshed,
        chapter_metadata_json=chapter.metadata_json,
        pending_source_count=0,
        removed_source_count=0,
    )


async def synthesize_chapter_draft_audio(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    chapter_id: str,
    req: MemoirBookAudioRequest,
    tts_client: TTSClient | None = None,
) -> MemoirBookAudioResponse:
    chapter = await _load_chapter(db, user_id=user_id, project_id=project_id, chapter_id=chapter_id)
    _require_confirmed_plan(chapter)
    draft = await _load_draft(db, project_id=project_id, chapter_id=chapter_id)
    if not draft or not draft.versions:
        raise HTTPException(status_code=400, detail="章节稿为空，无法朗读")
    latest = draft.versions[-1]
    text = latest.content.strip()
    text_hash = source_text_hash(text)
    speed_ratio = effective_tts_speed_ratio(req.speed_ratio)
    cached = cached_audio_response(
        draft.metadata_json or {},
        cache_key=CHAPTER_TTS_CACHE_KEY,
        text_hash=text_hash,
        voice=req.voice,
        speed_ratio=speed_ratio,
        force_regenerate=req.force_regenerate,
    )
    if cached:
        return cached

    synthesis = await synthesize_text_audio(
        db,
        user_id=user_id,
        text=text,
        voice=req.voice,
        speed_ratio=req.speed_ratio,
        tts_client=tts_client,
    )
    try:
        draft.metadata_json = {
            **(draft.metadata_json or {}),
            CHAPTER_TTS_CACHE_KEY: {
                **audio_cache_payload(synthesis, voice=req.voice, speed_ratio=speed_ratio),
                "draft_version_id": latest.id,
                "chapter_id": chapter.id,
            },
        }
        db.add(draft)
        await db.commit()
    except Exception:
        await db.rollback()
        await discard_staged_audio_files(synthesis.staged_audio)
        raise
    return audio_response(synthesis)
