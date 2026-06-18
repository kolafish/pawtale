from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirBookPlanStatus,
    MemoirCreatedBy,
)
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirChatMessage,
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
from app.services import get_ai_service
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
from app.services.memoir.voice_tts import TTSClient


SYSTEM_PROMPT = "你是一位谨慎的中文回忆录整理写作者，只能把给定口述材料整理成章节正文。"
AI_BOOK_DRAFT_UNAVAILABLE_MESSAGE = "AI 服务暂时不可用，请稍后重试。"
CHAPTER_TTS_CACHE_KEY = "chapter_tts"
PENDING_SOURCE_UTTERANCE_IDS_KEY = "pending_source_utterance_ids"
SOURCE_ASSIGNMENT_UPDATED_AT_KEY = "source_assignment_updated_at"
REMOVED_SOURCE_UTTERANCE_IDS_KEY = "removed_source_utterance_ids"
SOURCE_REFS_REMOVED_AT_KEY = "source_refs_removed_at"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChapterSourceText:
    ref: MemoirBookChapterSourceRef
    text: str


def _text_or_empty(value: str | None) -> str:
    return (value or "").strip()


def _display_text(utterance: MemoirVoiceUtterance) -> str:
    return _text_or_empty(utterance.transcript_edited) or _text_or_empty(utterance.transcript_raw)


def _strip_generated_text(text: str) -> str:
    content = text.strip()
    if len(content) >= 2 and content[0] in {"“", "「", '"', "'"} and content[-1] in {"”", "」", '"', "'"}:
        content = content[1:-1].strip()
    return content


def _metadata_without_keys(metadata_json: dict | None, keys: set[str]) -> dict[str, object]:
    metadata = dict(metadata_json or {})
    for key in keys:
        metadata.pop(key, None)
    return metadata


def _pending_source_utterance_ids(chapter: MemoirBookPlanChapter) -> set[str]:
    value = (chapter.metadata_json or {}).get(PENDING_SOURCE_UTTERANCE_IDS_KEY, [])
    if not isinstance(value, list):
        return set()
    return {item.strip() for item in value if isinstance(item, str) and item.strip()}


def _source_change_metadata_keys() -> set[str]:
    return {
        PENDING_SOURCE_UTTERANCE_IDS_KEY,
        SOURCE_ASSIGNMENT_UPDATED_AT_KEY,
        REMOVED_SOURCE_UTTERANCE_IDS_KEY,
        SOURCE_REFS_REMOVED_AT_KEY,
    }


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


def _draft_response(draft: MemoirBookChapterDraft) -> MemoirBookChapterDraftResponse:
    versions = [_version_response(version) for version in draft.versions]
    return MemoirBookChapterDraftResponse(
        id=draft.id,
        project_id=draft.project_id,
        chapter_id=draft.chapter_id,
        status=MemoirBookChapterDraftStatus(draft.status),
        metadata_json=draft.metadata_json or {},
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
) -> list[ChapterSourceText]:
    refs = [
        MemoirBookChapterSourceRef.model_validate(ref)
        for ref in (chapter.source_refs_json or [])
        if isinstance(ref, dict)
    ]
    if not refs:
        raise HTTPException(status_code=400, detail="章节缺少来源转写")
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
    sources: list[ChapterSourceText] = []
    for ref in refs:
        pair = by_utterance_id.get(ref.utterance_id)
        if not pair:
            raise HTTPException(status_code=400, detail="章节来源转写不存在")
        utterance, message = pair
        text = _display_text(utterance)
        if not text:
            raise HTTPException(status_code=400, detail="章节来源转写为空")
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
                    quote=ref.quote,
                ),
                text=text,
            )
        )
    return sources


def _build_draft_prompt(
    *,
    chapter: MemoirBookPlanChapter,
    sources: list[ChapterSourceText],
    feedback: str | None,
    previous_content: str | None = None,
    pending_source_utterance_ids: set[str] | None = None,
) -> str:
    pending_ids = pending_source_utterance_ids or set()
    source_text = "\n\n".join(
        (
            f"source {index}\n"
            f"source_type: {'新增来源' if source.ref.utterance_id in pending_ids else '既有来源'}\n"
            f"utterance_id: {source.ref.utterance_id}\n"
            f"quote: {source.ref.quote}\n"
            f"transcript: {source.text}"
        )
        for index, source in enumerate(sources, start=1)
    )
    feedback_text = f"\n用户反馈：{feedback.strip()}\n" if feedback else ""
    previous_text = (
        f"\n原章节正文（作为补写基础，尽量保留原有表达和段落）：\n{previous_content.strip()}\n"
        if previous_content
        else ""
    )
    supplement_text = (
        "\n本次任务：只把 source_type 为“新增来源”的转写里用户确实讲过的内容自然补入原章节正文；"
        "没有新增来源支撑的内容不要扩写，既有段落不要无故重写。\n"
        if pending_ids and previous_content
        else ""
    )
    return f"""请把下面口述材料整理成一个回忆录章节正文。

章节标题：{chapter.title}
章节范围：{chapter.summary or "只整理本章来源转写里已经讲过的内容。"}
{feedback_text}
{previous_text}
{supplement_text}
写作要求：
- 必须以叙述者已讲述的内容为主，使用第一人称或贴近原话的叙述。
- 不新增事实、人物、时间、地点、事件、对话或情绪，不替用户补故事。
- 可以去掉明显口头填充词，调整顺序和标点，让文字更像书稿。
- 素材少就写短，不要为了篇幅扩写。
- 不要输出标题、列表、解释或 Markdown，只输出正文。

来源转写：
{source_text}
"""


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
        draft.metadata_json = _metadata_without_keys(draft.metadata_json, {"status_reason"})
        if chapter:
            chapter.metadata_json = _metadata_without_keys(
                chapter.metadata_json,
                _source_change_metadata_keys(),
            )
            db.add(chapter)

    version_number = await _latest_version_number(db, draft.id) + 1
    db.add(
        MemoirBookChapterDraftVersion(
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
    )
    await db.commit()
    refreshed = await _load_draft(db, project_id=project_id, chapter_id=chapter_id)
    if not refreshed:
        raise HTTPException(status_code=500, detail="章节稿保存失败")
    return _draft_response(refreshed)


async def get_chapter_draft(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    chapter_id: str,
) -> MemoirBookChapterDraftResponse:
    await _load_chapter(db, user_id=user_id, project_id=project_id, chapter_id=chapter_id)
    draft = await _load_draft(db, project_id=project_id, chapter_id=chapter_id)
    if not draft:
        raise HTTPException(status_code=404, detail="章节稿不存在")
    return _draft_response(draft)


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
    pending_source_ids = _pending_source_utterance_ids(chapter)
    if existing and existing.versions and not req.force_regenerate and not pending_source_ids:
        return _draft_response(existing)

    sources = await _chapter_sources(db, project_id=project_id, chapter=chapter)
    previous_content = existing.versions[-1].content if existing and existing.versions and pending_source_ids else None
    prompt = _build_draft_prompt(
        chapter=chapter,
        sources=sources,
        feedback=req.feedback,
        previous_content=previous_content,
        pending_source_utterance_ids=pending_source_ids,
    )
    client = ai_client
    try:
        if client is None:
            client = get_ai_service(req.ai_model)
        content = await client._call_llm(
            SYSTEM_PROMPT,
            prompt,
            temperature=0.25,
            max_tokens=2600,
            timeout=120.0,
        )
    except HTTPException as exc:
        if exc.status_code < 500:
            raise
        logger.exception("AI memoir chapter draft failed provider=%s", getattr(client, "provider_name", "unknown"))
        raise HTTPException(status_code=503, detail=AI_BOOK_DRAFT_UNAVAILABLE_MESSAGE) from exc
    except Exception as exc:
        logger.exception("AI memoir chapter draft failed provider=%s", getattr(client, "provider_name", "unknown"))
        raise HTTPException(status_code=503, detail=AI_BOOK_DRAFT_UNAVAILABLE_MESSAGE) from exc

    draft_text = _strip_generated_text(str(content))
    if not draft_text:
        raise HTTPException(status_code=502, detail="AI 未返回可用章节稿")
    return await _append_version(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        content=draft_text,
        created_by=MemoirCreatedBy.AI,
        model=getattr(client, "model_name", req.ai_model),
        prompt_snapshot=prompt,
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
    if _pending_source_utterance_ids(chapter):
        raise HTTPException(status_code=400, detail="请先补写新增来源")
    draft = await _load_draft(db, project_id=project_id, chapter_id=chapter_id)
    if not draft or not draft.versions:
        raise HTTPException(status_code=400, detail="章节稿为空，无法确认")
    draft.status = MemoirBookChapterDraftStatus.CONFIRMED.value
    draft.metadata_json = _metadata_without_keys(draft.metadata_json, {"status_reason", REMOVED_SOURCE_UTTERANCE_IDS_KEY})
    chapter.metadata_json = _metadata_without_keys(chapter.metadata_json, _source_change_metadata_keys())
    db.add(chapter)
    await db.commit()
    refreshed = await _load_draft(db, project_id=project_id, chapter_id=chapter_id)
    if not refreshed:
        raise HTTPException(status_code=500, detail="章节稿确认失败")
    return _draft_response(refreshed)


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
