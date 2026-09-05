from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import delete, false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirChatContentStatus,
    MemoirChatMessageRole,
    MemoirCreatedBy,
    MemoirInterviewType,
    MemoirTranscriptPolishLevel,
    MemoirTranscriptVersionType,
)
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirChatMessage,
    MemoirImageAsset,
    MemoirMaterialShareVideo,
    MemoirSection,
    MemoirSectionMaterial,
    MemoirTranscriptVersion,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.models.projects import Project
from app.schemas.memoir_voice import (
    MemoirMaterialOfflineASRStateResponse,
    MemoirVoiceTranscriptIncludedChapterResponse,
    MemoirVoiceTranscriptCleanupRequest,
    MemoirVoiceTranscriptItemResponse,
    MemoirVoiceTranscriptListResponse,
    MemoirVoiceTranscriptSectionAssignmentResponse,
    MemoirVoiceTranscriptUpdateRequest,
)
from app.services.ai_calls import generate_text_with_client
from app.services.ai_factory import get_ai_service
from app.services.ai_timeouts import ai_text_timeout_seconds
from app.services.ai_types import AITextClient
from app.services.ai_workloads import AITextWorkload, ai_text_workload_policy
from app.services.memoir.memory_activation import delete_source_memory_traces
from app.services.memoir.material_offline_asr_state import material_offline_asr_states
from app.services.memoir.asr_text_normalization import normalize_memoir_asr_text
from app.services.memoir.book_source_links import (
    plan_source_ref_dicts_by_chapter as _plan_source_ref_dicts_by_chapter,
    source_ref_utterance_ids as _source_ref_utterance_ids,
)
from app.services.memoir.project_ai_preferences import (
    normalize_transcript_polish_level,
    project_transcript_polish_level,
    select_project_ai_model,
    text_polish_requirements,
)
from app.services.memoir.section_digests import mark_section_digests_stale
from app.services.memoir.section_roles import requires_user_assignment
from app.services.memoir.sections import refresh_material_transcript_version
from app.services.memoir.transcript_projection import current_utterance_text
from app.services.memoir.utterance_editing_pipeline import (
    schedule_ready_utterance_background_work,
)


SYSTEM_PROMPT = "你是一位谨慎的中文口述稿编辑，按指定强度润色，但绝不改变或编造事实。"
AI_CLEANUP_UNAVAILABLE_MESSAGE = "AI 服务暂时不可用，请稍后重试。"
logger = logging.getLogger(__name__)
MAX_TRANSCRIPT_VERSIONS_PER_UTTERANCE_TYPE = 3
_FILLER = r"(?:嗯+|啊+|呃+|额+|那个|这个|就是)"
_REPEATED_FILLERS_RE = re.compile(rf"(?:{_FILLER}[\s，,、]*){{2,}}")
_BOUNDARY_FILLER_RE = re.compile(r"(^|[。！？!?；;，,、]\s*)(?:嗯+|啊+|呃+|额+|就是)[\s，,、]*")
_BOUNDARY_DEICTIC_RE = re.compile(
    r"(^|[。！？!?；;，,、]\s*)(?:那个|这个)(?=(?:就是|就|是|那个|这个|嗯|啊|呃|额|[\s，,、]))[\s，,、]*"
)
_DUPLICATE_PUNCTUATION_RE = re.compile(r"([，,。！？!?；;])\1+")
_SPACE_RE = re.compile(r"\s+")
INCLUDED_FILTER_VALUES = {"all", "included", "not_included"}
FALLBACK_SUMMARY_TITLE_LIMIT = 32
FALLBACK_SUMMARY_TEXT_LIMIT = 120
TRANSCRIPTION_STATUS_READY = "ready"
TRANSCRIPTION_STATUS_PENDING = "pending_transcription"
TRANSCRIPTION_STATUS_EMPTY = "empty"
PENDING_TRANSCRIPTION_TITLE = "音频已保存，等待转写"
PENDING_TRANSCRIPTION_SUMMARY = "这段录音已经保存，但还没有识别成文字。"
TRANSCRIPT_DELETE_CONFLICT_DETAIL = (
    "这条讲述已用于回忆录证据、追问结果或衍生内容，不能单独物理删除；"
    "如需彻底移除其可溯源数据，请删除整个回忆录项目。"
)
@dataclass(frozen=True)
class CleanedTranscriptText:
    content: str
    provider: str | None
    created_by: MemoirCreatedBy
    requested_model: str | None = None
    resolved_model: str | None = None
    response_model: str | None = None


@dataclass(frozen=True)
class MemoirTranscriptCleanupContext:
    scene: str | None = None
    guidance_mode: str | None = None
    current_guidance_module: str | None = None
    dialect_glossary: tuple[str, ...] = ()
    recent_user_utterances: tuple[str, ...] = ()
    recent_assistant_messages: tuple[str, ...] = ()


def _has_transcript_text_clause(utterance_model=MemoirVoiceUtterance):
    return or_(
        func.trim(utterance_model.transcript_raw) != "",
        func.trim(utterance_model.transcript_edited) != "",
    )


def _has_recoverable_audio_clause(utterance_model=MemoirVoiceUtterance):
    return utterance_model.audio_file_id.is_not(None)


def _transcription_status(utterance: MemoirVoiceUtterance) -> str:
    if current_utterance_text(utterance):
        return TRANSCRIPTION_STATUS_READY
    if utterance.audio_file_id:
        return TRANSCRIPTION_STATUS_PENDING
    return TRANSCRIPTION_STATUS_EMPTY


def _compact_display_text(value: str) -> str:
    return _SPACE_RE.sub(" ", value or "").strip()


def _preview_sentence(value: str, *, limit: int) -> str:
    text = _compact_display_text(value)
    if len(text) <= limit:
        return text
    for marker in ("。", "！", "？", "；", ";", "!", "?"):
        index = text.find(marker)
        if 0 < index + 1 <= limit:
            return text[: index + 1].strip()
    return text[:limit].rstrip()


def _summary_fields(display_text: str) -> tuple[str, str, str]:
    title = _preview_sentence(display_text, limit=FALLBACK_SUMMARY_TITLE_LIMIT) or "讲述记录"
    summary = _preview_sentence(display_text, limit=FALLBACK_SUMMARY_TEXT_LIMIT) or title
    return title, summary, "fallback"


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
    glossary = _compact_context_texts(context.dialect_glossary, limit=20)
    if glossary:
        lines.append("- 方言/家族词表（优先用于同音词判断）：")
        lines.extend(f"  {index}. {text}" for index, text in enumerate(glossary, start=1))
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
    transcript_polish_level: MemoirTranscriptPolishLevel = MemoirTranscriptPolishLevel.LOW,
) -> str:
    context_block = _cleanup_context_block(context)
    polish_requirements = text_polish_requirements(transcript_polish_level)
    return f"""请按“{transcript_polish_level.value}”档对下面的中文口述识别文本做清理和润色。

共同边界：
- 只修正 ASR 可能因方言、口音、同音词造成的明显错字或错词；无法确定时保留原文
- 成书、目录、归章语境中，“张杰”“张洁”“张节”“章杰”很可能是“章节”；如果明显不是人名，要改成“章节”
- 可参考下方近期上下文判断同音词、领域词和指代；上下文只用于校对判断，不得把当前句没有说出的信息补进输出
- 不新增事实、人物、关系、时间、地点、事件、因果或情绪，不替讲述者补充细节
- 保留原文的不确定性、实质信息和有意义的具体细节
- 档位只控制语言改写强度，不允许改变事实含义
- 直接输出清理后的第一人称正文，不要标题、列表、解释或引号

本档要求：
{polish_requirements}

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
    transcript_polish_level: MemoirTranscriptPolishLevel = MemoirTranscriptPolishLevel.LOW,
    strict: bool = True,
) -> CleanedTranscriptText:
    source_text = source_text.strip()
    if not source_text:
        raise HTTPException(status_code=400, detail="讲述内容为空，无法整理")

    client: AITextClient | None = ai_client
    try:
        if client is None:
            client = get_ai_service(ai_model)
        policy = ai_text_workload_policy(AITextWorkload.GENERATION_TEXT)
        generated = await generate_text_with_client(
            client,
            SYSTEM_PROMPT,
            _build_cleanup_prompt(
                source_text,
                context=context,
                transcript_polish_level=transcript_polish_level,
            ),
            requested_model=ai_model,
            temperature=0.2,
            max_tokens=policy.max_tokens,
            timeout=ai_text_timeout_seconds(policy.timeout_profile),
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

    cleaned = _remove_obvious_oral_fillers(generated.content)
    if cleaned == source_text:
        cleaned = _fallback_cleaned_transcript_text(source_text)
    if not cleaned:
        if strict:
            raise HTTPException(status_code=502, detail="AI 未返回可用清理结果")
        cleaned = source_text
    return CleanedTranscriptText(
        content=cleaned,
        provider=generated.provider,
        created_by=MemoirCreatedBy.AI,
        requested_model=generated.requested_model,
        resolved_model=generated.resolved_model,
        response_model=generated.response_model,
    )


async def load_voice_transcript_cleanup_context(
    db: AsyncSession,
    voice_session: MemoirVoiceSession,
    *,
    exclude_message_id: str | None = None,
    dialect_glossary: tuple[str, ...] = (),
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
        elif role in {MemoirChatMessageRole.ASSISTANT.value, MemoirChatMessageRole.INTERVIEWER.value}:
            recent_assistant.append(content_text)
    return MemoirTranscriptCleanupContext(
        scene="voice_chat",
        guidance_mode=voice_session.guidance_mode,
        current_guidance_module=voice_session.current_guidance_module,
        dialect_glossary=dialect_glossary,
        recent_user_utterances=tuple(recent_user[-5:]),
        recent_assistant_messages=tuple(recent_assistant[-3:]),
    )


def _transcript_filter(
    user_id: str,
    project_id: str | None = None,
    *,
    allow_admin: bool = False,
):
    clauses = [
        or_(
            _has_transcript_text_clause(),
            _has_recoverable_audio_clause(),
        ),
        ~_paired_human_interviewer_prompt_exists(),
    ]
    if not allow_admin:
        clauses.append(Project.user_id == user_id)
    if project_id:
        clauses.append(Project.id == project_id)
    return clauses


def _narrator_material_clause():
    return MemoirVoiceUtterance.chat_message.has(
        MemoirChatMessage.role == MemoirChatMessageRole.USER.value
    )


def _manual_correction_exists():
    return (
        select(MemoirTranscriptVersion.id)
        .where(
            MemoirTranscriptVersion.source_artifact_id == MemoirVoiceUtterance.id,
            MemoirTranscriptVersion.version_type == MemoirTranscriptVersionType.USER_EDITED.value,
        )
        .exists()
    )


def _paired_human_interviewer_prompt_exists():
    interviewer_message = aliased(MemoirChatMessage)
    answer_message = aliased(MemoirChatMessage)
    answer_utterance = aliased(MemoirVoiceUtterance)
    return (
        select(answer_utterance.id)
        .select_from(interviewer_message)
        .join(answer_message, answer_message.parent_message_id == interviewer_message.id)
        .join(answer_utterance, answer_utterance.chat_message_id == answer_message.id)
        .where(
            MemoirVoiceSession.interview_type == MemoirInterviewType.HUMAN_INTERVIEW.value,
            interviewer_message.id == MemoirVoiceUtterance.chat_message_id,
            interviewer_message.role == MemoirChatMessageRole.INTERVIEWER.value,
            answer_message.role == MemoirChatMessageRole.USER.value,
            _has_transcript_text_clause(answer_utterance),
        )
        .exists()
    )


def _like_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _search_terms(search_query: str | None) -> list[str]:
    if not search_query:
        return []
    return [term for term in re.split(r"\s+", search_query.strip()) if term][:8]


def _question_search_filter(pattern: str):
    user_message = aliased(MemoirChatMessage)
    question_message = aliased(MemoirChatMessage)
    later_question = aliased(MemoirChatMessage)
    later_question_exists = (
        select(later_question.id)
        .where(
            later_question.voice_session_id == MemoirVoiceUtterance.voice_session_id,
            later_question.role.in_(
                [MemoirChatMessageRole.ASSISTANT.value, MemoirChatMessageRole.INTERVIEWER.value]
            ),
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
            user_message.role == MemoirChatMessageRole.USER.value,
            user_message.parent_message_id.is_(None),
            question_message.voice_session_id == MemoirVoiceUtterance.voice_session_id,
            question_message.role.in_(
                [MemoirChatMessageRole.ASSISTANT.value, MemoirChatMessageRole.INTERVIEWER.value]
            ),
            func.trim(question_message.content_text) != "",
            question_message.content_text.ilike(pattern, escape="\\"),
            question_message.turn_index < user_message.turn_index,
            ~later_question_exists,
        )
        .exists()
    )


def _parent_question_search_filter(pattern: str):
    user_message = aliased(MemoirChatMessage)
    parent_question = aliased(MemoirChatMessage)
    return (
        select(parent_question.id)
        .select_from(parent_question)
        .join(user_message, user_message.parent_message_id == parent_question.id)
        .where(
            user_message.id == MemoirVoiceUtterance.chat_message_id,
            user_message.role == MemoirChatMessageRole.USER.value,
            parent_question.role.in_(
                [MemoirChatMessageRole.ASSISTANT.value, MemoirChatMessageRole.INTERVIEWER.value]
            ),
            func.trim(parent_question.content_text) != "",
            parent_question.content_text.ilike(pattern, escape="\\"),
        )
        .exists()
    )


def _search_filter(term: str):
    pattern = _like_pattern(term)
    return or_(
        MemoirVoiceUtterance.transcript_raw.ilike(pattern, escape="\\"),
        MemoirVoiceUtterance.transcript_edited.ilike(pattern, escape="\\"),
        _parent_question_search_filter(pattern),
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
                ~MemoirTranscriptVersion.id.in_(
                    select(MemoirSectionMaterial.transcript_version_id).where(
                        MemoirSectionMaterial.transcript_version_id.is_not(None)
                    )
                ),
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
    allow_admin: bool = False,
) -> set[str]:
    query = (
        select(MemoirBookPlan)
        .join(Project, MemoirBookPlan.project_id == Project.id)
        .options(selectinload(MemoirBookPlan.chapters))
    )
    if not allow_admin:
        query = query.where(Project.user_id == user_id)
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
    section_assignment: MemoirVoiceTranscriptSectionAssignmentResponse | None = None,
    offline_asr: MemoirMaterialOfflineASRStateResponse | None = None,
) -> MemoirVoiceTranscriptItemResponse:
    voice_session = utterance.voice_session
    chat_message = utterance.chat_message
    audio_file = utterance.audio_file
    question_message = _question_message_for_utterance(utterance)
    raw = utterance.transcript_raw or ""
    edited = utterance.transcript_edited or ""
    display_text = edited.strip() or raw.strip()
    inclusions = included_chapters or []
    transcription_status = _transcription_status(utterance)
    can_recover_transcription = transcription_status == TRANSCRIPTION_STATUS_PENDING
    if can_recover_transcription:
        summary_title, summary_text, summary_source = (
            PENDING_TRANSCRIPTION_TITLE,
            PENDING_TRANSCRIPTION_SUMMARY,
            "pending_transcription",
        )
    else:
        summary_title, summary_text, summary_source = _summary_fields(display_text)
    return MemoirVoiceTranscriptItemResponse(
        id=utterance.id,
        voice_session_id=utterance.voice_session_id,
        project_id=voice_session.project_id,
        project_title=voice_session.project.title if voice_session.project else "",
        chat_message_id=utterance.chat_message_id,
        chat_message_role=MemoirChatMessageRole(chat_message.role) if chat_message else None,
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
        summary_title=summary_title,
        summary_text=summary_text,
        summary_source=summary_source,
        transcription_status=transcription_status,
        can_recover_transcription=can_recover_transcription,
        raw_char_count=len(raw.strip()),
        edited_char_count=len(edited.strip()),
        display_char_count=len(display_text),
        start_ms=utterance.start_ms,
        end_ms=utterance.end_ms,
        provider=utterance.provider,
        session_status=voice_session.status,
        guidance_mode=voice_session.guidance_mode,
        interview_type=voice_session.interview_type,
        current_guidance_module=voice_session.current_guidance_module,
        is_included_in_book=bool(inclusions),
        included_chapters=inclusions,
        section_assignment=section_assignment,
        offline_asr=offline_asr or MemoirMaterialOfflineASRStateResponse(),
        created_at=_utc_datetime(utterance.created_at),
        session_created_at=_utc_datetime(voice_session.created_at),
        session_started_at=(
            _utc_datetime(voice_session.started_at) if voice_session.started_at is not None else None
        ),
    )


def _question_message_for_utterance(utterance: MemoirVoiceUtterance) -> MemoirChatMessage | None:
    user_message = utterance.chat_message
    if not user_message:
        return None
    if user_message.role != MemoirChatMessageRole.USER.value:
        return None
    parent_message = user_message.parent_message
    if (
        parent_message
        and parent_message.role in {
            MemoirChatMessageRole.ASSISTANT.value,
            MemoirChatMessageRole.INTERVIEWER.value,
        }
        and parent_message.content_text.strip()
    ):
        return parent_message
    candidates = [
        message
        for message in utterance.voice_session.messages
        if (
            message.role in {
                MemoirChatMessageRole.ASSISTANT.value,
                MemoirChatMessageRole.INTERVIEWER.value,
            }
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
        selectinload(MemoirVoiceUtterance.chat_message).selectinload(MemoirChatMessage.parent_message),
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


async def _section_assignments_by_utterance_ids(
    db: AsyncSession,
    utterance_ids: list[str],
    *,
    project_ids: list[str] | None = None,
) -> dict[str, MemoirVoiceTranscriptSectionAssignmentResponse]:
    clean_utterance_ids = list(dict.fromkeys(item for item in utterance_ids if item))
    if not clean_utterance_ids:
        return {}
    query = (
        select(
            MemoirSectionMaterial.utterance_id,
            MemoirBookPlanChapter.id,
            MemoirBookPlanChapter.title,
            MemoirBookPlanChapter.sort_order,
            MemoirSection.id,
            MemoirSection.title,
            MemoirSection.sort_order,
            MemoirSection.metadata_json,
        )
        .join(
            MemoirSection,
            MemoirSection.id == MemoirSectionMaterial.section_id,
        )
        .join(
            MemoirBookPlanChapter,
            MemoirBookPlanChapter.id == MemoirSection.chapter_id,
        )
        .where(MemoirSectionMaterial.utterance_id.in_(clean_utterance_ids))
        .order_by(
            MemoirBookPlanChapter.sort_order,
            MemoirSection.sort_order,
            MemoirSectionMaterial.sort_order,
        )
    )
    if project_ids:
        query = query.where(
            MemoirSectionMaterial.project_id.in_(
                list(dict.fromkeys(project_ids))
            )
        )
    assignments: dict[str, MemoirVoiceTranscriptSectionAssignmentResponse] = {}
    for row in (await db.execute(query)).all():
        (
            utterance_id,
            chapter_id,
            chapter_title,
            chapter_sort_order,
            section_id,
            section_title,
            section_sort_order,
            section_metadata,
        ) = row
        assignments.setdefault(
            utterance_id,
            MemoirVoiceTranscriptSectionAssignmentResponse(
                chapter_id=chapter_id,
                chapter_title=chapter_title,
                chapter_sort_order=chapter_sort_order,
                section_id=section_id,
                section_title=section_title,
                section_sort_order=section_sort_order,
                requires_user_assignment=requires_user_assignment(
                    section_metadata
                ),
            ),
        )
    return assignments


async def manual_corrections_by_utterance_ids(
    db: AsyncSession,
    utterance_ids: list[str],
) -> dict[str, datetime]:
    clean_ids = list(dict.fromkeys(item for item in utterance_ids if item))
    if not clean_ids:
        return {}
    rows = (
        await db.execute(
            select(
                MemoirTranscriptVersion.source_artifact_id,
                func.max(MemoirTranscriptVersion.created_at),
            )
            .where(
                MemoirTranscriptVersion.source_artifact_id.in_(clean_ids),
                MemoirTranscriptVersion.version_type == MemoirTranscriptVersionType.USER_EDITED.value,
            )
            .group_by(MemoirTranscriptVersion.source_artifact_id)
        )
    ).all()
    return {
        utterance_id: corrected_at
        for utterance_id, corrected_at in rows
        if utterance_id and corrected_at
    }


async def _load_owned_utterance(
    db: AsyncSession,
    utterance_id: str,
    user_id: str,
    *,
    allow_admin: bool = False,
    project_id: str | None = None,
    narrator_only: bool = False,
) -> MemoirVoiceUtterance:
    filters = [MemoirVoiceUtterance.id == utterance_id]
    if not allow_admin:
        filters.append(Project.user_id == user_id)
    if project_id:
        filters.append(Project.id == project_id)
    if narrator_only:
        filters.append(_narrator_material_clause())
    result = await db.execute(
        select(MemoirVoiceUtterance)
        .join(MemoirVoiceSession, MemoirVoiceUtterance.voice_session_id == MemoirVoiceSession.id)
        .join(Project, MemoirVoiceSession.project_id == Project.id)
        .where(*filters)
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
    voice_session_id: str | None = None,
    manual_corrected: bool | None = None,
    narrator_only: bool = False,
    oldest_first: bool = False,
    interview_time_order: bool = False,
    limit: int = 50,
    offset: int = 0,
    allow_admin: bool = False,
) -> MemoirVoiceTranscriptListResponse:
    if included not in INCLUDED_FILTER_VALUES:
        raise HTTPException(status_code=400, detail="不支持的讲述记录收录筛选条件")
    filters = _transcript_filter(user_id, project_id, allow_admin=allow_admin)
    filters.extend(_search_filter(term) for term in _search_terms(search_query))
    if guidance_mode:
        filters.append(MemoirVoiceSession.guidance_mode == guidance_mode)
    if current_guidance_module:
        filters.append(MemoirVoiceSession.current_guidance_module == current_guidance_module)
    if voice_session_id:
        filters.append(MemoirVoiceSession.id == voice_session_id)
    if manual_corrected is not None:
        correction_exists = _manual_correction_exists()
        filters.append(correction_exists if manual_corrected else ~correction_exists)
    if narrator_only:
        filters.append(_narrator_material_clause())
    if included != "all":
        included_utterance_ids = await _included_utterance_ids_for_filter(
            db,
            user_id,
            project_id=project_id,
            allow_admin=allow_admin,
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
    if interview_time_order:
        interview_time = func.coalesce(MemoirVoiceSession.started_at, MemoirVoiceSession.created_at)
        ordering = (
            (
                interview_time.asc(),
                MemoirVoiceUtterance.created_at.asc(),
                MemoirVoiceUtterance.id.asc(),
            )
            if oldest_first
            else (
                interview_time.desc(),
                MemoirVoiceUtterance.created_at.desc(),
                MemoirVoiceUtterance.id.desc(),
            )
        )
    else:
        ordering = (MemoirVoiceUtterance.created_at.desc(), MemoirVoiceUtterance.id.desc())
    result = await db.execute(
        select(MemoirVoiceUtterance)
        .join(MemoirVoiceSession, MemoirVoiceUtterance.voice_session_id == MemoirVoiceSession.id)
        .join(Project, MemoirVoiceSession.project_id == Project.id)
        .where(*filters)
        .options(*_detail_options())
        .order_by(*ordering)
        .offset(offset)
        .limit(limit)
    )
    utterances = result.scalars().all()
    inclusions = await _book_inclusions_by_utterance_ids(
        db,
        [utterance.id for utterance in utterances],
        project_ids=[utterance.voice_session.project_id for utterance in utterances],
    )
    assignments = await _section_assignments_by_utterance_ids(
        db,
        [utterance.id for utterance in utterances],
        project_ids=[utterance.voice_session.project_id for utterance in utterances],
    )
    offline_asr_states = await material_offline_asr_states(db, utterances)
    items = [
        _item_response(
            utterance,
            inclusions.get(utterance.id, []),
            assignments.get(utterance.id),
            offline_asr_states.get(utterance.id),
        )
        for utterance in utterances
    ]
    return MemoirVoiceTranscriptListResponse(
        items=items,
        total=total or 0,
        limit=limit,
        offset=offset,
    )


async def get_voice_transcript(
    db: AsyncSession,
    utterance_id: str,
    user_id: str,
    *,
    allow_admin: bool = False,
    project_id: str | None = None,
    narrator_only: bool = False,
) -> MemoirVoiceTranscriptItemResponse:
    utterance = await _load_owned_utterance(
        db,
        utterance_id,
        user_id,
        allow_admin=allow_admin,
        project_id=project_id,
        narrator_only=narrator_only,
    )
    inclusions = await _book_inclusions_by_utterance_ids(
        db,
        [utterance.id],
        project_ids=[utterance.voice_session.project_id],
    )
    assignments = await _section_assignments_by_utterance_ids(
        db,
        [utterance.id],
        project_ids=[utterance.voice_session.project_id],
    )
    offline_asr_states = await material_offline_asr_states(db, [utterance])
    return _item_response(
        utterance,
        inclusions.get(utterance.id, []),
        assignments.get(utterance.id),
        offline_asr_states.get(utterance.id),
    )


async def _sync_included_transcript_edit(
    db: AsyncSession,
    *,
    utterance: MemoirVoiceUtterance,
    content: str,
    transcript_version_id: str,
) -> set[str]:
    project_id = utterance.voice_session.project_id
    material = await refresh_material_transcript_version(
        db,
        project_id=project_id,
        utterance_id=utterance.id,
        transcript_version_id=transcript_version_id,
    )
    if material is None:
        return set()
    chapter_id = await db.scalar(
        select(MemoirSection.chapter_id).where(
            MemoirSection.project_id == project_id,
            MemoirSection.id == material.section_id,
        )
    )
    if chapter_id is None:
        return set()

    drafts = (
        await db.execute(
            select(MemoirBookChapterDraft).where(
                MemoirBookChapterDraft.project_id == project_id,
                MemoirBookChapterDraft.chapter_id == chapter_id,
            )
        )
    ).scalars().all()
    for draft in drafts:
        draft.status = MemoirBookChapterDraftStatus.DRAFT.value
        draft.metadata_json = {
            **(draft.metadata_json or {}),
            "status_reason": "materials_changed",
        }
        db.add(draft)
    return {chapter_id}


async def _apply_edited_text(
    db: AsyncSession,
    utterance: MemoirVoiceUtterance,
    content: str,
    *,
    version_type: MemoirTranscriptVersionType,
    created_by: MemoirCreatedBy,
    provider: str | None,
    ai_model: str | None = None,
    commit: bool = True,
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
    await _sync_included_transcript_edit(
        db,
        utterance=utterance,
        content=content,
        transcript_version_id=version.id,
    )
    await schedule_ready_utterance_background_work(
        db,
        project_id=utterance.voice_session.project_id,
        utterance=utterance,
        ai_model=ai_model,
        source=f"transcript_{version_type.value}",
    )
    if commit:
        await db.commit()
    inclusions = await _book_inclusions_by_utterance_ids(
        db,
        [utterance.id],
        project_ids=[utterance.voice_session.project_id],
    )
    assignments = await _section_assignments_by_utterance_ids(
        db,
        [utterance.id],
        project_ids=[utterance.voice_session.project_id],
    )
    offline_asr_states = await material_offline_asr_states(db, [utterance])
    return _item_response(
        utterance,
        inclusions.get(utterance.id, []),
        assignments.get(utterance.id),
        offline_asr=offline_asr_states.get(utterance.id),
    )


async def apply_cleaned_voice_transcript_text(
    db: AsyncSession,
    utterance: MemoirVoiceUtterance,
    content: str,
    *,
    created_by: MemoirCreatedBy,
    provider: str | None,
    ai_model: str | None = None,
    commit: bool = True,
) -> MemoirVoiceTranscriptItemResponse:
    cleaned_content = content.strip()
    if not cleaned_content:
        raise HTTPException(status_code=400, detail="整理文字不能为空")
    return await _apply_edited_text(
        db,
        utterance,
        cleaned_content,
        version_type=MemoirTranscriptVersionType.DIALECT_CORRECTED,
        created_by=created_by,
        provider=provider,
        ai_model=ai_model,
        commit=commit,
    )


async def update_voice_transcript(
    db: AsyncSession,
    utterance_id: str,
    user_id: str,
    req: MemoirVoiceTranscriptUpdateRequest,
    *,
    allow_admin: bool = False,
    project_id: str | None = None,
    narrator_only: bool = False,
) -> MemoirVoiceTranscriptItemResponse:
    content = req.transcript_edited.strip()
    if not content:
        raise HTTPException(status_code=400, detail="整理文字不能为空")
    utterance = await _load_owned_utterance(
        db,
        utterance_id,
        user_id,
        allow_admin=allow_admin,
        project_id=project_id,
        narrator_only=narrator_only,
    )
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
    *,
    allow_admin: bool = False,
) -> MemoirVoiceTranscriptItemResponse:
    utterance = await _load_owned_utterance(db, utterance_id, user_id, allow_admin=allow_admin)
    source_text = current_utterance_text(utterance)
    selected_ai_model = select_project_ai_model(
        utterance.voice_session.project,
        req.ai_model,
    )
    polish_level = (
        normalize_transcript_polish_level(req.transcript_polish_level)
        if req.transcript_polish_level is not None
        else project_transcript_polish_level(utterance.voice_session.project)
    )
    cleaned = await generate_cleaned_transcript_text(
        source_text,
        ai_model=selected_ai_model,
        ai_client=ai_client,
        transcript_polish_level=polish_level,
        strict=True,
    )
    return await _apply_edited_text(
        db,
        utterance,
        cleaned.content,
        version_type=MemoirTranscriptVersionType.DIALECT_CORRECTED,
        created_by=cleaned.created_by,
        provider=cleaned.provider,
        ai_model=selected_ai_model,
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
    rows = (
        await db.execute(
            select(MemoirSectionMaterial, MemoirSection)
            .join(
                MemoirSection,
                MemoirSection.id == MemoirSectionMaterial.section_id,
            )
            .where(
                MemoirSectionMaterial.project_id == project_id,
                MemoirSectionMaterial.utterance_id == utterance_id,
            )
        )
    ).all()
    affected_section_ids = {section.id for _, section in rows}
    affected_chapter_ids = {section.chapter_id for _, section in rows}
    if not affected_chapter_ids:
        return
    for material, section in rows:
        await db.delete(material)
        section.version += 1
        db.add(section)
    await mark_section_digests_stale(
        db,
        project_id=project_id,
        section_ids=affected_section_ids,
    )
    drafts = (
        await db.execute(
            select(MemoirBookChapterDraft).where(
                MemoirBookChapterDraft.project_id == project_id,
                MemoirBookChapterDraft.chapter_id.in_(affected_chapter_ids),
            )
        )
    ).scalars().all()
    for draft in drafts:
        draft.status = MemoirBookChapterDraftStatus.DRAFT.value
        draft.metadata_json = {
            **(draft.metadata_json or {}),
            "status_reason": "materials_deleted",
        }
        db.add(draft)


async def _ensure_voice_transcript_can_be_deleted(
    db: AsyncSession,
    *,
    utterance_id: str,
) -> None:
    protected_references = (
        (MemoirMaterialShareVideo.id, MemoirMaterialShareVideo.utterance_id),
        (MemoirImageAsset.id, MemoirImageAsset.source_utterance_id),
    )
    for reference_id, utterance_column in protected_references:
        if await db.scalar(
            select(reference_id).where(utterance_column == utterance_id).limit(1)
        ):
            raise HTTPException(status_code=409, detail=TRANSCRIPT_DELETE_CONFLICT_DETAIL)


async def delete_voice_transcript(
    db: AsyncSession,
    utterance_id: str,
    user_id: str,
) -> None:
    utterance = await _load_owned_utterance(db, utterance_id, user_id)
    await _ensure_voice_transcript_can_be_deleted(
        db,
        utterance_id=utterance.id,
    )
    project_id = utterance.voice_session.project_id
    message = utterance.chat_message
    delete_message = False
    clear_message = False

    if message and message.role == MemoirChatMessageRole.USER.value:
        other_utterances, child_messages = await _message_reference_counts(db, message, utterance.id)
        delete_message = other_utterances == 0 and child_messages == 0
        clear_message = other_utterances == 0 and child_messages > 0

    await _mark_book_sources_deleted(db, project_id=project_id, utterance_id=utterance.id)
    await delete_source_memory_traces(db, project_id=project_id, utterance_id=utterance.id)
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
