from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import bindparam, delete, false, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirChatContentStatus,
    MemoirChatMessageRole,
    MemoirCreatedBy,
    MemoirTranscriptVersionType,
)
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirChatMessage,
    MemoirTranscriptVersion,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.models.projects import Project
from app.schemas.memoir_voice import (
    MemoirVoiceTranscriptIncludedChapterResponse,
    MemoirVoiceTranscriptCleanupRequest,
    MemoirVoiceTranscriptItemResponse,
    MemoirVoiceTranscriptListResponse,
    MemoirVoiceTranscriptUpdateRequest,
)
from app.services.ai_factory import get_ai_service
from app.services.ai_types import AITextClient
from app.services.memoir import automation_events as memoir_automation_events
from app.services.memoir import source_cards
from app.services.memoir.asr_text_normalization import normalize_memoir_asr_text
from app.services.memoir.book_plan_sources import (
    REMOVED_SOURCE_UTTERANCE_IDS_KEY,
    SOURCES_CHANGED_STATUS_REASON,
    SOURCES_DELETED_STATUS_REASON,
    draft_status_metadata as _draft_status_metadata,
    pending_source_ids as _pending_source_ids,
    removed_source_ids as _removed_source_ids,
    safe_quote as _safe_quote,
    set_deleted_source_metadata as _set_deleted_source_metadata,
    set_source_change_metadata as _set_source_change_metadata,
    source_ref_utterance_id as _source_ref_utterance_id,
)
from app.services.memoir.book_auto_revision import schedule_chapter_auto_revisions
from app.services.memoir.book_source_links import (
    plan_source_ref_dicts_by_chapter as _plan_source_ref_dicts_by_chapter,
    source_ref_utterance_ids as _source_ref_utterance_ids,
)
from app.services.memoir.chapter_source_links import (
    mark_utterance_links_removed as _mark_utterance_links_removed,
    update_utterance_link_ref_fields as _update_utterance_link_ref_fields,
)
from app.services.memoir.utterance_editing_pipeline import schedule_source_routing_for_ready_utterance


SYSTEM_PROMPT = "你是一位谨慎的中文口述稿校对助手，只做忠于原话的识别文字校正和口头语清理。"
AI_CLEANUP_UNAVAILABLE_MESSAGE = "AI 服务暂时不可用，请稍后重试。"
logger = logging.getLogger(__name__)
MAX_TRANSCRIPT_VERSIONS_PER_UTTERANCE_TYPE = 3
TRANSCRIPT_FTS_MIN_TERM_LENGTH = 3
_FILLER = r"(?:嗯+|啊+|呃+|额+|那个|这个|就是)"
_REPEATED_FILLERS_RE = re.compile(rf"(?:{_FILLER}[\s，,、]*){{2,}}")
_BOUNDARY_FILLER_RE = re.compile(rf"(^|[。！？!?；;，,、]\s*)(?:嗯+|啊+|呃+|额+|就是)[\s，,、]*")
_BOUNDARY_DEICTIC_RE = re.compile(
    rf"(^|[。！？!?；;，,、]\s*)(?:那个|这个)(?=(?:就是|就|是|那个|这个|嗯|啊|呃|额|[\s，,、]))[\s，,、]*"
)
_DUPLICATE_PUNCTUATION_RE = re.compile(r"([，,。！？!?；;])\1+")
_SPACE_RE = re.compile(r"\s+")
INCLUDED_FILTER_VALUES = {"all", "included", "not_included"}


@dataclass(frozen=True)
class CleanedTranscriptText:
    content: str
    provider: str | None
    created_by: MemoirCreatedBy


@dataclass(frozen=True)
class MemoirTranscriptCleanupContext:
    scene: str | None = None
    guidance_mode: str | None = None
    current_guidance_module: str | None = None
    recent_user_utterances: tuple[str, ...] = ()
    recent_assistant_messages: tuple[str, ...] = ()


def _text_or_empty(value: str | None) -> str:
    return (value or "").strip()


def _current_text(utterance: MemoirVoiceUtterance) -> str:
    return _text_or_empty(utterance.transcript_edited) or _text_or_empty(utterance.transcript_raw)


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _cleanup_context_block(context: MemoirTranscriptCleanupContext | None) -> str:
    if context is None:
        return ""

    lines: list[str] = ["近期上下文（仅供校对参考，不得扩写当前句）："]
    if context.scene:
        lines.append(f"- 场景：{context.scene}")
    if context.guidance_mode:
        lines.append(f"- 引导模式：{context.guidance_mode}")
    if context.current_guidance_module:
        lines.append(f"- 当前引导模块：{context.current_guidance_module}")
    user_utterances = _compact_context_texts(context.recent_user_utterances, limit=5)
    if user_utterances:
        lines.append("- 最近用户原话：")
        lines.extend(f"  {index}. {text}" for index, text in enumerate(user_utterances, start=1))
    assistant_messages = _compact_context_texts(context.recent_assistant_messages, limit=3)
    if assistant_messages:
        lines.append("- 最近 AI 问题/回复：")
        lines.extend(f"  {index}. {text}" for index, text in enumerate(assistant_messages, start=1))
    return "\n".join(lines) + "\n"


def _compact_context_texts(values: tuple[str, ...], *, limit: int) -> list[str]:
    compacted: list[str] = []
    for value in values[-limit:]:
        text = _SPACE_RE.sub(" ", value).strip()
        if not text:
            continue
        compacted.append(text[:120])
    return compacted


def _build_cleanup_prompt(
    source_text: str,
    *,
    context: MemoirTranscriptCleanupContext | None = None,
) -> str:
    context_block = _cleanup_context_block(context)
    return f"""请对下面这段实时语音识别出来的中文口述内容做一次忠于原文的清理。

要求：
- 只修正 ASR 可能因方言、口音、同音词造成的明显错字或错词；无法确定时保留原文
- 成书、目录、归章语境中，“张杰”“张洁”“张节”“章杰”很可能是“章节”；如果明显不是人名，要改成“章节”
- 可参考下方近期上下文判断同音词、领域词和指代；上下文只用于校对判断，不得把当前句没有说出的信息补进输出
- 必须去掉明显的口头填充词，比如句首或重复出现的“嗯”“啊”“呃”“那个”“这个”“就是”
- 必须去掉同一句话里明显无信息增量的重复表达，例如“那个那个那个”“就是就是”
- 可以整理基础标点和极少量语序，使句子可读
- 不新增事实、人物、时间、地点、事件或情绪
- 不扩写、不总结、不文学化、不替用户补充细节
- 输出长度应接近原文；短句保持短
- 直接输出清理后的第一人称正文，不要标题、列表、解释或引号

{context_block}
原始口述内容：
{source_text}
"""


def _strip_llm_wrapping(text: str) -> str:
    cleaned = text.strip()
    if len(cleaned) >= 2 and cleaned[0] in {"“", "「", '"', "'"} and cleaned[-1] in {"”", "」", '"', "'"}:
        return cleaned[1:-1].strip()
    return cleaned


def _remove_obvious_oral_fillers(text: str) -> str:
    cleaned = _strip_llm_wrapping(text)
    cleaned = _REPEATED_FILLERS_RE.sub("", cleaned)
    cleaned = _BOUNDARY_FILLER_RE.sub(r"\1", cleaned)
    cleaned = _BOUNDARY_DEICTIC_RE.sub(r"\1", cleaned)
    cleaned = _DUPLICATE_PUNCTUATION_RE.sub(r"\1", cleaned)
    cleaned = _SPACE_RE.sub(" ", cleaned)
    cleaned = cleaned.strip(" ，,、")
    cleaned = cleaned.replace("，。", "。").replace(",。", "。")
    cleaned = normalize_memoir_asr_text(cleaned)
    return cleaned.strip()


def _fallback_cleaned_transcript_text(source_text: str) -> str:
    return _remove_obvious_oral_fillers(source_text) or source_text.strip()


async def generate_cleaned_transcript_text(
    source_text: str,
    *,
    ai_model: str | None = None,
    ai_client: AITextClient | None = None,
    context: MemoirTranscriptCleanupContext | None = None,
    strict: bool = True,
) -> CleanedTranscriptText:
    source_text = source_text.strip()
    if not source_text:
        raise HTTPException(status_code=400, detail="讲述内容为空，无法整理")

    client: AITextClient | None = ai_client
    try:
        if client is None:
            client = get_ai_service(ai_model)
        content = await client._call_llm(
            SYSTEM_PROMPT,
            _build_cleanup_prompt(source_text, context=context),
            temperature=0.2,
            max_tokens=1200,
            timeout=90.0,
        )
    except HTTPException as exc:
        if strict and exc.status_code < 500:
            raise
        if strict:
            logger.exception(
                "AI voice transcript cleanup failed provider=%s model=%s",
                getattr(client, "provider_name", "unknown"),
                getattr(client, "model_name", ai_model or "default"),
            )
            raise HTTPException(status_code=503, detail=AI_CLEANUP_UNAVAILABLE_MESSAGE) from exc
        logger.warning("AI voice transcript cleanup unavailable; using local fallback", exc_info=True)
        return CleanedTranscriptText(
            content=_fallback_cleaned_transcript_text(source_text),
            provider=None,
            created_by=MemoirCreatedBy.SYSTEM,
        )
    except Exception as exc:
        if strict:
            logger.exception(
                "AI voice transcript cleanup failed provider=%s model=%s",
                getattr(client, "provider_name", "unknown"),
                getattr(client, "model_name", ai_model or "default"),
            )
            raise HTTPException(status_code=503, detail=AI_CLEANUP_UNAVAILABLE_MESSAGE) from exc
        logger.warning("AI voice transcript cleanup failed; using local fallback", exc_info=True)
        return CleanedTranscriptText(
            content=_fallback_cleaned_transcript_text(source_text),
            provider=None,
            created_by=MemoirCreatedBy.SYSTEM,
        )

    cleaned = _remove_obvious_oral_fillers(str(content))
    if cleaned == source_text:
        cleaned = _fallback_cleaned_transcript_text(source_text)
    if not cleaned:
        if strict:
            raise HTTPException(status_code=502, detail="AI 未返回可用清理结果")
        cleaned = source_text
    return CleanedTranscriptText(
        content=cleaned,
        provider=client.provider_name,
        created_by=MemoirCreatedBy.AI,
    )


async def load_voice_transcript_cleanup_context(
    db: AsyncSession,
    voice_session: MemoirVoiceSession,
    *,
    exclude_message_id: str | None = None,
) -> MemoirTranscriptCleanupContext:
    conditions = [
        MemoirChatMessage.voice_session_id == voice_session.id,
        func.trim(MemoirChatMessage.content_text) != "",
    ]
    if exclude_message_id:
        conditions.append(MemoirChatMessage.id != exclude_message_id)
    result = await db.execute(
        select(MemoirChatMessage.role, MemoirChatMessage.content_text)
        .where(*conditions)
        .order_by(MemoirChatMessage.turn_index.desc(), MemoirChatMessage.created_at.desc())
        .limit(8)
    )
    recent_user: list[str] = []
    recent_assistant: list[str] = []
    for role, content_text in reversed(result.all()):
        if role == MemoirChatMessageRole.USER.value:
            recent_user.append(content_text)
        elif role == MemoirChatMessageRole.ASSISTANT.value:
            recent_assistant.append(content_text)
    return MemoirTranscriptCleanupContext(
        scene="voice_chat",
        guidance_mode=voice_session.guidance_mode,
        current_guidance_module=voice_session.current_guidance_module,
        recent_user_utterances=tuple(recent_user[-5:]),
        recent_assistant_messages=tuple(recent_assistant[-3:]),
    )


def _transcript_filter(user_id: str, project_id: str | None = None):
    clauses = [
        Project.user_id == user_id,
        or_(
            func.trim(MemoirVoiceUtterance.transcript_raw) != "",
            func.trim(MemoirVoiceUtterance.transcript_edited) != "",
        ),
    ]
    if project_id:
        clauses.append(Project.id == project_id)
    return clauses


def _like_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _search_terms(search_query: str | None) -> list[str]:
    if not search_query:
        return []
    return [term for term in re.split(r"\s+", search_query.strip()) if term][:8]


def _fts_match_query(term: str) -> str:
    escaped = term.replace('"', '""')
    return f'"{escaped}"'


def _transcript_text_search_filter(term: str):
    if len(term) < TRANSCRIPT_FTS_MIN_TERM_LENGTH:
        pattern = _like_pattern(term)
        return or_(
            MemoirVoiceUtterance.transcript_raw.ilike(pattern, escape="\\"),
            MemoirVoiceUtterance.transcript_edited.ilike(pattern, escape="\\"),
        )
    return text(
        """
        EXISTS (
            SELECT 1
            FROM memoir_voice_utterances_fts
            WHERE memoir_voice_utterances_fts.rowid = memoir_voice_utterances.rowid
                AND memoir_voice_utterances_fts MATCH :fts_query
        )
        """
    ).bindparams(bindparam("fts_query", _fts_match_query(term), unique=True))


def _question_search_filter(pattern: str):
    user_message = aliased(MemoirChatMessage)
    question_message = aliased(MemoirChatMessage)
    later_question = aliased(MemoirChatMessage)
    later_question_exists = (
        select(later_question.id)
        .where(
            later_question.voice_session_id == MemoirVoiceUtterance.voice_session_id,
            later_question.role == MemoirChatMessageRole.ASSISTANT.value,
            func.trim(later_question.content_text) != "",
            later_question.turn_index > question_message.turn_index,
            later_question.turn_index < user_message.turn_index,
        )
        .exists()
    )
    return (
        select(question_message.id)
        .select_from(question_message)
        .join(user_message, user_message.id == MemoirVoiceUtterance.chat_message_id)
        .where(
            question_message.voice_session_id == MemoirVoiceUtterance.voice_session_id,
            question_message.role == MemoirChatMessageRole.ASSISTANT.value,
            func.trim(question_message.content_text) != "",
            question_message.content_text.ilike(pattern, escape="\\"),
            question_message.turn_index < user_message.turn_index,
            ~later_question_exists,
        )
        .exists()
    )


def _search_filter(term: str):
    pattern = _like_pattern(term)
    return or_(
        _transcript_text_search_filter(term),
        _question_search_filter(pattern),
    )


async def prune_transcript_versions_for_utterance(
    db: AsyncSession,
    *,
    utterance_id: str,
    version_type: MemoirTranscriptVersionType | str,
    keep: int = MAX_TRANSCRIPT_VERSIONS_PER_UTTERANCE_TYPE,
) -> int:
    version_value = version_type.value if isinstance(version_type, MemoirTranscriptVersionType) else version_type
    stale_ids = (
        await db.execute(
            select(MemoirTranscriptVersion.id)
            .where(
                MemoirTranscriptVersion.source_artifact_id == utterance_id,
                MemoirTranscriptVersion.version_type == version_value,
            )
            .order_by(MemoirTranscriptVersion.created_at.desc(), MemoirTranscriptVersion.id.desc())
            .offset(max(1, keep))
        )
    ).scalars().all()
    if not stale_ids:
        return 0
    result = await db.execute(delete(MemoirTranscriptVersion).where(MemoirTranscriptVersion.id.in_(stale_ids)))
    return int(result.rowcount or 0)


async def _included_utterance_ids_for_filter(
    db: AsyncSession,
    user_id: str,
    *,
    project_id: str | None = None,
) -> set[str]:
    query = (
        select(MemoirBookPlan)
        .join(Project, MemoirBookPlan.project_id == Project.id)
        .where(Project.user_id == user_id)
        .options(selectinload(MemoirBookPlan.chapters))
    )
    if project_id:
        query = query.where(Project.id == project_id)
    plans = (await db.execute(query)).scalars().all()
    utterance_ids: set[str] = set()
    for plan in plans:
        refs_by_chapter = await _plan_source_ref_dicts_by_chapter(db, plan)
        for refs in refs_by_chapter.values():
            utterance_ids.update(_source_ref_utterance_ids(refs))
    return utterance_ids


def _item_response(
    utterance: MemoirVoiceUtterance,
    included_chapters: list[MemoirVoiceTranscriptIncludedChapterResponse] | None = None,
) -> MemoirVoiceTranscriptItemResponse:
    voice_session = utterance.voice_session
    chat_message = utterance.chat_message
    audio_file = utterance.audio_file
    question_message = _question_message_for_utterance(utterance)
    raw = utterance.transcript_raw or ""
    edited = utterance.transcript_edited or ""
    display_text = edited.strip() or raw.strip()
    inclusions = included_chapters or []
    return MemoirVoiceTranscriptItemResponse(
        id=utterance.id,
        voice_session_id=utterance.voice_session_id,
        project_id=voice_session.project_id,
        project_title=voice_session.project.title if voice_session.project else "",
        chat_message_id=utterance.chat_message_id,
        audio_file_id=utterance.audio_file_id,
        audio_mime_type=audio_file.mime_type if audio_file else None,
        audio_size_bytes=audio_file.size_bytes if audio_file else None,
        audio_duration_seconds=audio_file.duration_seconds if audio_file else None,
        transcript_version_id=chat_message.transcript_version_id if chat_message else None,
        question_message_id=question_message.id if question_message else None,
        question_text=question_message.content_text if question_message else "",
        question_audio_file_id=question_message.audio_file_id if question_message else None,
        transcript_raw=raw,
        transcript_edited=edited,
        display_text=display_text,
        raw_char_count=len(raw.strip()),
        edited_char_count=len(edited.strip()),
        display_char_count=len(display_text),
        start_ms=utterance.start_ms,
        end_ms=utterance.end_ms,
        provider=utterance.provider,
        session_status=voice_session.status,
        guidance_mode=voice_session.guidance_mode,
        current_guidance_module=voice_session.current_guidance_module,
        is_included_in_book=bool(inclusions),
        included_chapters=inclusions,
        created_at=_utc_datetime(utterance.created_at),
        session_created_at=_utc_datetime(voice_session.created_at),
    )


def _question_message_for_utterance(utterance: MemoirVoiceUtterance) -> MemoirChatMessage | None:
    user_message = utterance.chat_message
    if not user_message:
        return None
    candidates = [
        message
        for message in utterance.voice_session.messages
        if (
            message.role == MemoirChatMessageRole.ASSISTANT.value
            and message.content_text.strip()
            and message.turn_index < user_message.turn_index
        )
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda message: (message.turn_index, message.created_at, message.id))[-1]


def _detail_options():
    return (
        selectinload(MemoirVoiceUtterance.voice_session).selectinload(MemoirVoiceSession.messages),
        selectinload(MemoirVoiceUtterance.voice_session).selectinload(MemoirVoiceSession.project),
        selectinload(MemoirVoiceUtterance.chat_message),
        selectinload(MemoirVoiceUtterance.audio_file),
    )


async def _book_inclusions_by_utterance_ids(
    db: AsyncSession,
    utterance_ids: list[str],
    *,
    project_ids: list[str] | None = None,
) -> dict[str, list[MemoirVoiceTranscriptIncludedChapterResponse]]:
    clean_utterance_ids = list(dict.fromkeys(item for item in utterance_ids if item))
    if not clean_utterance_ids:
        return {}
    query = select(MemoirBookPlan).options(selectinload(MemoirBookPlan.chapters))
    if project_ids:
        query = query.where(MemoirBookPlan.project_id.in_(list(dict.fromkeys(project_ids))))
    plans = (await db.execute(query)).scalars().all()
    utterance_id_set = set(clean_utterance_ids)
    inclusions: dict[str, list[MemoirVoiceTranscriptIncludedChapterResponse]] = {}
    for plan in plans:
        refs_by_chapter = await _plan_source_ref_dicts_by_chapter(db, plan)
        chapters = sorted(plan.chapters, key=lambda item: (item.sort_order, item.created_at, item.id))
        for chapter in chapters:
            included_chapter = MemoirVoiceTranscriptIncludedChapterResponse(
                chapter_id=chapter.id,
                title=chapter.title,
                sort_order=chapter.sort_order,
            )
            for utterance_id in _source_ref_utterance_ids(refs_by_chapter.get(chapter.id, [])):
                if utterance_id in utterance_id_set:
                    inclusions.setdefault(utterance_id, []).append(included_chapter)
    return inclusions


async def _load_owned_utterance(
    db: AsyncSession,
    utterance_id: str,
    user_id: str,
) -> MemoirVoiceUtterance:
    result = await db.execute(
        select(MemoirVoiceUtterance)
        .join(MemoirVoiceSession, MemoirVoiceUtterance.voice_session_id == MemoirVoiceSession.id)
        .join(Project, MemoirVoiceSession.project_id == Project.id)
        .where(MemoirVoiceUtterance.id == utterance_id, Project.user_id == user_id)
        .options(*_detail_options())
    )
    utterance = result.scalar_one_or_none()
    if not utterance:
        raise HTTPException(status_code=404, detail="讲述记录不存在")
    return utterance


async def list_voice_transcripts(
    db: AsyncSession,
    user_id: str,
    *,
    project_id: str | None = None,
    search_query: str | None = None,
    included: str = "all",
    guidance_mode: str | None = None,
    current_guidance_module: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> MemoirVoiceTranscriptListResponse:
    if included not in INCLUDED_FILTER_VALUES:
        raise HTTPException(status_code=400, detail="不支持的讲述记录收录筛选条件")
    filters = _transcript_filter(user_id, project_id)
    filters.extend(_search_filter(term) for term in _search_terms(search_query))
    if guidance_mode:
        filters.append(MemoirVoiceSession.guidance_mode == guidance_mode)
    if current_guidance_module:
        filters.append(MemoirVoiceSession.current_guidance_module == current_guidance_module)
    if included != "all":
        included_utterance_ids = await _included_utterance_ids_for_filter(
            db,
            user_id,
            project_id=project_id,
        )
        if included == "included":
            filters.append(
                MemoirVoiceUtterance.id.in_(included_utterance_ids)
                if included_utterance_ids
                else false()
            )
        else:
            if included_utterance_ids:
                filters.append(~MemoirVoiceUtterance.id.in_(included_utterance_ids))
    total = await db.scalar(
        select(func.count(MemoirVoiceUtterance.id))
        .join(MemoirVoiceSession, MemoirVoiceUtterance.voice_session_id == MemoirVoiceSession.id)
        .join(Project, MemoirVoiceSession.project_id == Project.id)
        .where(*filters)
    )
    result = await db.execute(
        select(MemoirVoiceUtterance)
        .join(MemoirVoiceSession, MemoirVoiceUtterance.voice_session_id == MemoirVoiceSession.id)
        .join(Project, MemoirVoiceSession.project_id == Project.id)
        .where(*filters)
        .options(*_detail_options())
        .order_by(MemoirVoiceUtterance.created_at.desc(), MemoirVoiceUtterance.id.desc())
        .offset(offset)
        .limit(limit)
    )
    utterances = result.scalars().all()
    inclusions = await _book_inclusions_by_utterance_ids(
        db,
        [utterance.id for utterance in utterances],
        project_ids=[utterance.voice_session.project_id for utterance in utterances],
    )
    items = [
        _item_response(utterance, inclusions.get(utterance.id, []))
        for utterance in utterances
    ]
    return MemoirVoiceTranscriptListResponse(
        items=items,
        total=total or 0,
        limit=limit,
        offset=offset,
    )


async def _sync_included_transcript_edit(
    db: AsyncSession,
    *,
    utterance: MemoirVoiceUtterance,
    content: str,
    transcript_version_id: str,
) -> set[str]:
    project_id = utterance.voice_session.project_id
    chapters = (
        await db.execute(
            select(MemoirBookPlanChapter).where(MemoirBookPlanChapter.project_id == project_id)
        )
    ).scalars().all()
    changed_chapter_ids: set[str] = set()
    for chapter in chapters:
        refs: list[dict] = []
        changed = False
        for ref in chapter.source_refs_json or []:
            if not isinstance(ref, dict):
                refs.append(ref)
                continue
            if _source_ref_utterance_id(ref) != utterance.id:
                refs.append(ref)
                continue
            next_ref = {
                **ref,
                "transcript_version_id": transcript_version_id,
                "quote": _safe_quote(content),
            }
            refs.append(next_ref)
            changed = changed or next_ref != ref
        if not changed:
            continue
        pending_ids = _pending_source_ids(chapter.metadata_json)
        pending_ids.add(utterance.id)
        removed_ids = _removed_source_ids(chapter.metadata_json)
        removed_ids.discard(utterance.id)
        chapter.source_refs_json = refs
        chapter.metadata_json = _set_source_change_metadata(
            chapter.metadata_json,
            added_ids=pending_ids,
            removed_ids=removed_ids,
        )
        db.add(chapter)
        changed_chapter_ids.add(chapter.id)

    if not changed_chapter_ids:
        return set()
    await _update_utterance_link_ref_fields(
        db,
        project_id=project_id,
        utterance_id=utterance.id,
        transcript_version_id=transcript_version_id,
        quote=_safe_quote(content),
    )
    drafts = (
        await db.execute(
            select(MemoirBookChapterDraft).where(
                MemoirBookChapterDraft.project_id == project_id,
                MemoirBookChapterDraft.chapter_id.in_(changed_chapter_ids),
            )
        )
    ).scalars().all()
    for draft in drafts:
        draft.status = MemoirBookChapterDraftStatus.DRAFT.value
        draft.metadata_json = _draft_status_metadata(draft.metadata_json, SOURCES_CHANGED_STATUS_REASON)
        db.add(draft)
    return changed_chapter_ids


async def _apply_edited_text(
    db: AsyncSession,
    utterance: MemoirVoiceUtterance,
    content: str,
    *,
    version_type: MemoirTranscriptVersionType,
    created_by: MemoirCreatedBy,
    provider: str | None,
    ai_model: str | None = None,
) -> MemoirVoiceTranscriptItemResponse:
    version = MemoirTranscriptVersion(
        voice_session_id=utterance.voice_session_id,
        source_artifact_id=utterance.id,
        version_type=version_type.value,
        content=content,
        provider=provider,
        created_by=created_by.value,
    )
    db.add(version)
    await db.flush()
    utterance.transcript_edited = content
    if utterance.chat_message:
        utterance.chat_message.content_text = content
        utterance.chat_message.content_status = MemoirChatContentStatus.CORRECTED.value
        utterance.chat_message.transcript_version_id = version.id
    await prune_transcript_versions_for_utterance(
        db,
        utterance_id=utterance.id,
        version_type=version_type,
    )
    changed_chapter_ids = await _sync_included_transcript_edit(
        db,
        utterance=utterance,
        content=content,
        transcript_version_id=version.id,
    )
    await source_cards.schedule_source_card_generation_job(
        db,
        project_id=utterance.voice_session.project_id,
        utterance_id=utterance.id,
        ai_model=ai_model,
        reason=f"transcript_{version_type.value}",
    )
    if changed_chapter_ids:
        await schedule_chapter_auto_revisions(
            db,
            project_id=utterance.voice_session.project_id,
            chapter_ids=changed_chapter_ids,
            ai_model=ai_model,
        )
    else:
        await schedule_source_routing_for_ready_utterance(
            db,
            project_id=utterance.voice_session.project_id,
            utterance=utterance,
            source=f"transcript_{version_type.value}",
            ai_model=ai_model,
        )
    await db.commit()
    inclusions = await _book_inclusions_by_utterance_ids(
        db,
        [utterance.id],
        project_ids=[utterance.voice_session.project_id],
    )
    return _item_response(utterance, inclusions.get(utterance.id, []))


async def update_voice_transcript(
    db: AsyncSession,
    utterance_id: str,
    user_id: str,
    req: MemoirVoiceTranscriptUpdateRequest,
) -> MemoirVoiceTranscriptItemResponse:
    content = req.transcript_edited.strip()
    if not content:
        raise HTTPException(status_code=400, detail="整理文字不能为空")
    utterance = await _load_owned_utterance(db, utterance_id, user_id)
    return await _apply_edited_text(
        db,
        utterance,
        content,
        version_type=MemoirTranscriptVersionType.USER_EDITED,
        created_by=MemoirCreatedBy.USER,
        provider=None,
    )


async def cleanup_voice_transcript(
    db: AsyncSession,
    utterance_id: str,
    user_id: str,
    req: MemoirVoiceTranscriptCleanupRequest,
    ai_client: AITextClient | None = None,
) -> MemoirVoiceTranscriptItemResponse:
    utterance = await _load_owned_utterance(db, utterance_id, user_id)
    source_text = _current_text(utterance)
    cleaned = await generate_cleaned_transcript_text(
        source_text,
        ai_model=req.ai_model,
        ai_client=ai_client,
        strict=True,
    )
    return await _apply_edited_text(
        db,
        utterance,
        cleaned.content,
        version_type=MemoirTranscriptVersionType.DIALECT_CORRECTED,
        created_by=cleaned.created_by,
        provider=cleaned.provider,
        ai_model=req.ai_model,
    )


async def _message_reference_counts(
    db: AsyncSession,
    message: MemoirChatMessage,
    utterance_id: str,
) -> tuple[int, int]:
    other_utterances = await db.scalar(
        select(func.count(MemoirVoiceUtterance.id)).where(
            MemoirVoiceUtterance.chat_message_id == message.id,
            MemoirVoiceUtterance.id != utterance_id,
        )
    )
    child_messages = await db.scalar(
        select(func.count(MemoirChatMessage.id)).where(
            MemoirChatMessage.parent_message_id == message.id,
        )
    )
    return other_utterances or 0, child_messages or 0


async def _mark_book_sources_deleted(
    db: AsyncSession,
    *,
    project_id: str,
    utterance_id: str,
) -> None:
    chapters = (
        await db.execute(
            select(MemoirBookPlanChapter)
            .where(MemoirBookPlanChapter.project_id == project_id)
            .options(selectinload(MemoirBookPlanChapter.draft))
        )
    ).scalars().all()
    removed_at_dt = datetime.utcnow()
    removed_at = removed_at_dt.replace(tzinfo=timezone.utc).isoformat()
    for chapter in chapters:
        refs = [ref for ref in (chapter.source_refs_json or []) if isinstance(ref, dict)]
        kept_refs = [ref for ref in refs if _source_ref_utterance_id(ref) != utterance_id]
        removed_ref_count = len(refs) - len(kept_refs)

        pending_ids = _pending_source_ids(chapter.metadata_json)
        removed_ids = _removed_source_ids(chapter.metadata_json)
        pending_changed = utterance_id in pending_ids
        if pending_changed:
            pending_ids.discard(utterance_id)

        if not removed_ref_count and not pending_changed:
            continue

        if removed_ref_count:
            removed_ids.add(utterance_id)
            chapter.source_refs_json = kept_refs

        chapter.metadata_json = _set_deleted_source_metadata(
            chapter.metadata_json,
            pending_ids=pending_ids,
            removed_ids=removed_ids,
            removed_at=removed_at,
        )
        db.add(chapter)

        if chapter.draft:
            chapter.draft.status = MemoirBookChapterDraftStatus.DRAFT.value
            draft_metadata = _draft_status_metadata(
                chapter.draft.metadata_json,
                SOURCES_DELETED_STATUS_REASON,
            )
            draft_removed_ids = _removed_source_ids(draft_metadata)
            draft_removed_ids.add(utterance_id)
            draft_metadata[REMOVED_SOURCE_UTTERANCE_IDS_KEY] = sorted(draft_removed_ids)
            chapter.draft.metadata_json = draft_metadata
            db.add(chapter.draft)
    await _mark_utterance_links_removed(
        db,
        project_id=project_id,
        utterance_id=utterance_id,
        removed_at=removed_at_dt,
    )


async def delete_voice_transcript(
    db: AsyncSession,
    utterance_id: str,
    user_id: str,
) -> None:
    utterance = await _load_owned_utterance(db, utterance_id, user_id)
    project_id = utterance.voice_session.project_id
    message = utterance.chat_message
    delete_message = False
    clear_message = False

    if message and message.role == MemoirChatMessageRole.USER.value:
        other_utterances, child_messages = await _message_reference_counts(db, message, utterance.id)
        delete_message = other_utterances == 0 and child_messages == 0
        clear_message = other_utterances == 0 and child_messages > 0

    await memoir_automation_events.cleanup_automation_events_for_deleted_utterances(
        db,
        project_id=project_id,
        utterance_ids=[utterance.id],
    )
    await _mark_book_sources_deleted(db, project_id=project_id, utterance_id=utterance.id)
    await db.delete(utterance)
    await db.flush()

    if message and delete_message:
        await db.delete(message)
    elif message and clear_message:
        message.content_text = ""
        message.audio_file_id = None
        message.transcript_version_id = None
        message.content_status = MemoirChatContentStatus.CORRECTED.value

    await db.commit()
