from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import MemoirBookPlanStatus, MemoirCreatedBy
from app.models.memoir import (
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirBookChapterDraft,
    MemoirChatMessage,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.schemas.memoir import (
    MemoirBookChapterSourceRef,
    MemoirBookPlanChapterInput,
    MemoirBookPlanChapterResponse,
    MemoirBookPlanConfirmRequest,
    MemoirBookPlanGenerateRequest,
    MemoirBookPlanResponse,
)
from app.services import get_ai_service
from app.services.ai_types import AITextClient
from app.services.json_utils import extract_json_payload
from app.services.memoir.access import load_memoir_project


SYSTEM_PROMPT = "你是一位谨慎的中文回忆录章节规划编辑，只能根据给定口述转写整理章节。"
AI_BOOK_PLAN_UNAVAILABLE_MESSAGE = "AI 服务暂时不可用，请稍后重试。"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SourceSegment:
    source_index: int
    voice_session_id: str
    message_id: str
    utterance_id: str
    transcript_version_id: str | None
    audio_file_id: str | None
    start_ms: int | None
    end_ms: int | None
    text: str

    def ref(self, quote: str = "") -> MemoirBookChapterSourceRef:
        return MemoirBookChapterSourceRef(
            voice_session_id=self.voice_session_id,
            message_id=self.message_id,
            utterance_id=self.utterance_id,
            transcript_version_id=self.transcript_version_id,
            audio_file_id=self.audio_file_id,
            start_ms=self.start_ms,
            end_ms=self.end_ms,
            quote=_safe_quote(self.text, quote),
        )


def _text_or_empty(value: str | None) -> str:
    return (value or "").strip()


def _display_text(utterance: MemoirVoiceUtterance) -> str:
    return _text_or_empty(utterance.transcript_edited) or _text_or_empty(utterance.transcript_raw)


def _safe_quote(source_text: str, quote: str = "") -> str:
    cleaned = _text_or_empty(quote)
    if cleaned and cleaned in source_text:
        return cleaned[:180]
    return source_text[:180]


def _as_dict(ref: MemoirBookChapterSourceRef) -> dict[str, object]:
    return ref.model_dump()


def _chapter_response(chapter: MemoirBookPlanChapter) -> MemoirBookPlanChapterResponse:
    return MemoirBookPlanChapterResponse(
        id=chapter.id,
        plan_id=chapter.plan_id,
        project_id=chapter.project_id,
        title=chapter.title,
        summary=chapter.summary,
        sort_order=chapter.sort_order,
        source_refs=[
            MemoirBookChapterSourceRef.model_validate(ref)
            for ref in (chapter.source_refs_json or [])
            if isinstance(ref, dict)
        ],
        metadata_json=chapter.metadata_json or {},
        created_at=chapter.created_at,
        updated_at=chapter.updated_at,
    )


def _plan_response(plan: MemoirBookPlan) -> MemoirBookPlanResponse:
    return MemoirBookPlanResponse(
        id=plan.id,
        project_id=plan.project_id,
        title=plan.title,
        status=MemoirBookPlanStatus(plan.status),
        created_by=MemoirCreatedBy(plan.created_by),
        model=plan.model,
        metadata_json=plan.metadata_json or {},
        chapters=[_chapter_response(chapter) for chapter in plan.chapters],
        created_at=plan.created_at,
        updated_at=plan.updated_at,
    )


async def _source_segments(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
) -> list[SourceSegment]:
    await load_memoir_project(db, project_id, user_id)
    result = await db.execute(
        select(MemoirVoiceUtterance, MemoirChatMessage)
        .join(MemoirVoiceSession, MemoirVoiceUtterance.voice_session_id == MemoirVoiceSession.id)
        .join(MemoirChatMessage, MemoirVoiceUtterance.chat_message_id == MemoirChatMessage.id)
        .where(
            MemoirVoiceSession.project_id == project_id,
            or_(
                MemoirVoiceUtterance.transcript_raw != "",
                MemoirVoiceUtterance.transcript_edited != "",
            ),
        )
        .order_by(
            MemoirVoiceSession.created_at,
            MemoirVoiceUtterance.created_at,
            MemoirVoiceUtterance.id,
        )
    )
    segments: list[SourceSegment] = []
    for utterance, message in result.all():
        text = _display_text(utterance)
        if not text:
            continue
        segments.append(
            SourceSegment(
                source_index=len(segments) + 1,
                voice_session_id=utterance.voice_session_id,
                message_id=message.id,
                utterance_id=utterance.id,
                transcript_version_id=message.transcript_version_id,
                audio_file_id=utterance.audio_file_id or message.audio_file_id,
                start_ms=utterance.start_ms,
                end_ms=utterance.end_ms,
                text=text,
            )
        )
    return segments


def _build_plan_prompt(project_title: str, sources: list[SourceSegment], max_chapters: int) -> str:
    source_text = "\n\n".join(
        (
            f"source_index: {source.source_index}\n"
            f"utterance_id: {source.utterance_id}\n"
            f"message_id: {source.message_id}\n"
            f"text: {source.text}"
        )
        for source in sources
    )
    return f"""请把下面的口述转写整理成回忆录章节规划。

当前回忆录：{project_title}
最多章节数：{max_chapters}

请输出严格 JSON，不要 Markdown，不要解释。格式：
{{
  "title": "章节规划标题",
  "chapters": [
    {{
      "title": "章节标题",
      "summary": "只概括该章节会整理哪些已讲述内容",
      "source_indices": [1, 2],
      "evidence_quote": "必须来自 source text 的短句"
    }}
  ]
}}

要求：
- 只根据 source text 已经讲过的事实划分章节，不新增人物、时间、地点、事件或情绪。
- 章节标题要短，像书稿目录，不要像 AI 总结标题。
- summary 只能说明用户已经讲述的内容范围，不能扩写正文。
- source_indices 必须来自给定 source_index，且每章至少包含一个来源。
- evidence_quote 必须逐字来自对应 source text。
- 如果素材很少，可以只返回 1 到 2 章。

口述转写：
{source_text}
"""


def _string_value(item: dict[str, Any], key: str) -> str:
    value = item.get(key)
    return value.strip() if isinstance(value, str) else ""


def _source_indices(item: dict[str, Any]) -> list[int]:
    raw = item.get("source_indices", [])
    if not isinstance(raw, list):
        return []
    indices: list[int] = []
    for value in raw:
        if isinstance(value, int):
            indices.append(value)
        elif isinstance(value, str) and value.isdigit():
            indices.append(int(value))
    return indices


def _chapters_from_payload(
    payload: Any,
    *,
    sources: list[SourceSegment],
    max_chapters: int,
) -> tuple[str, list[MemoirBookPlanChapterInput]]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="AI 未返回有效章节规划 JSON")
    source_by_index = {source.source_index: source for source in sources}
    raw_chapters = payload.get("chapters", [])
    if not isinstance(raw_chapters, list):
        raise HTTPException(status_code=502, detail="AI 未返回有效章节列表")

    chapters: list[MemoirBookPlanChapterInput] = []
    for item in raw_chapters[:max_chapters]:
        if not isinstance(item, dict):
            continue
        title = _string_value(item, "title")
        if not title:
            continue
        summary = _string_value(item, "summary")
        quote = _string_value(item, "evidence_quote")
        refs: list[MemoirBookChapterSourceRef] = []
        seen_utterances: set[str] = set()
        for source_index in _source_indices(item):
            source = source_by_index.get(source_index)
            if not source or source.utterance_id in seen_utterances:
                continue
            refs.append(source.ref(quote))
            seen_utterances.add(source.utterance_id)
        if not refs:
            continue
        chapters.append(
            MemoirBookPlanChapterInput(
                title=title,
                summary=summary,
                source_refs=refs,
            )
        )

    if not chapters:
        raise HTTPException(status_code=502, detail="AI 未返回可用章节规划")
    title = _string_value(payload, "title") or "章节规划"
    return title, chapters


async def _load_plan(db: AsyncSession, project_id: str) -> MemoirBookPlan | None:
    return await db.scalar(
        select(MemoirBookPlan)
        .where(MemoirBookPlan.project_id == project_id)
        .options(selectinload(MemoirBookPlan.chapters))
        .execution_options(populate_existing=True)
    )


async def _delete_plan_chapters(db: AsyncSession, plan: MemoirBookPlan) -> None:
    chapters = list(plan.chapters)
    if not chapters:
        return
    chapter_ids = [chapter.id for chapter in chapters]
    drafts = (
        await db.execute(
            select(MemoirBookChapterDraft)
            .where(MemoirBookChapterDraft.chapter_id.in_(chapter_ids))
            .options(selectinload(MemoirBookChapterDraft.versions))
        )
    ).scalars().all()
    for draft in drafts:
        for version in list(draft.versions):
            await db.delete(version)
        await db.delete(draft)
    for chapter in chapters:
        await db.delete(chapter)
    await db.flush()
    plan.chapters = []


async def _replace_plan(
    db: AsyncSession,
    *,
    project_id: str,
    title: str,
    chapters: list[MemoirBookPlanChapterInput],
    status: MemoirBookPlanStatus,
    created_by: MemoirCreatedBy,
    model: str | None,
    prompt_snapshot: str,
    metadata_json: dict[str, object] | None = None,
) -> MemoirBookPlanResponse:
    plan = await _load_plan(db, project_id)
    if not plan:
        plan = MemoirBookPlan(project_id=project_id)
        db.add(plan)
        await db.flush()
    else:
        await _delete_plan_chapters(db, plan)

    plan.title = title[:200]
    plan.status = status.value
    plan.created_by = created_by.value
    plan.model = model
    plan.prompt_snapshot = prompt_snapshot
    plan.metadata_json = metadata_json or {}

    for index, chapter in enumerate(chapters):
        db.add(
            MemoirBookPlanChapter(
                plan_id=plan.id,
                project_id=project_id,
                title=chapter.title.strip()[:200],
                summary=chapter.summary.strip(),
                sort_order=index,
                source_refs_json=[_as_dict(ref) for ref in chapter.source_refs],
            )
        )
    await db.commit()
    refreshed = await _load_plan(db, project_id)
    if not refreshed:
        raise HTTPException(status_code=500, detail="章节规划保存失败")
    return _plan_response(refreshed)


async def get_book_plan(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
) -> MemoirBookPlanResponse:
    await load_memoir_project(db, project_id, user_id)
    plan = await _load_plan(db, project_id)
    if not plan:
        raise HTTPException(status_code=404, detail="章节规划不存在")
    return _plan_response(plan)


async def generate_book_plan(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookPlanGenerateRequest,
    ai_client: AITextClient | None = None,
) -> MemoirBookPlanResponse:
    project = await load_memoir_project(db, project_id, user_id)
    sources = await _source_segments(db, user_id=user_id, project_id=project_id)
    if not sources:
        raise HTTPException(status_code=400, detail="暂无转写内容，无法规划章节")
    prompt = _build_plan_prompt(project.title, sources, req.max_chapters)
    client = ai_client
    try:
        if client is None:
            client = get_ai_service(req.ai_model)
        content = await client._call_llm(
            SYSTEM_PROMPT,
            prompt,
            temperature=0.2,
            max_tokens=2400,
            timeout=120.0,
        )
    except HTTPException as exc:
        if exc.status_code < 500:
            raise
        logger.exception("AI memoir book plan failed provider=%s", getattr(client, "provider_name", "unknown"))
        raise HTTPException(status_code=503, detail=AI_BOOK_PLAN_UNAVAILABLE_MESSAGE) from exc
    except Exception as exc:
        logger.exception("AI memoir book plan failed provider=%s", getattr(client, "provider_name", "unknown"))
        raise HTTPException(status_code=503, detail=AI_BOOK_PLAN_UNAVAILABLE_MESSAGE) from exc

    payload = extract_json_payload(str(content))
    title, chapters = _chapters_from_payload(payload, sources=sources, max_chapters=req.max_chapters)
    return await _replace_plan(
        db,
        project_id=project_id,
        title=title,
        chapters=chapters,
        status=MemoirBookPlanStatus.DRAFT,
        created_by=MemoirCreatedBy.AI,
        model=getattr(client, "model_name", req.ai_model),
        prompt_snapshot=prompt,
        metadata_json={
            "source_utterance_count": len(sources),
            "prompt_version": "memoir_book_plan_v1",
        },
    )


def _validated_chapters(
    req: MemoirBookPlanConfirmRequest,
    sources: list[SourceSegment],
) -> list[MemoirBookPlanChapterInput]:
    source_by_utterance = {source.utterance_id: source for source in sources}
    chapters: list[MemoirBookPlanChapterInput] = []
    for chapter in req.chapters:
        refs: list[MemoirBookChapterSourceRef] = []
        seen_utterances: set[str] = set()
        for ref in chapter.source_refs:
            source = source_by_utterance.get(ref.utterance_id)
            if not source:
                raise HTTPException(status_code=400, detail="章节来源不属于当前回忆录")
            if source.utterance_id in seen_utterances:
                continue
            refs.append(source.ref(ref.quote))
            seen_utterances.add(source.utterance_id)
        if not refs:
            raise HTTPException(status_code=400, detail="每个章节都需要至少一个来源")
        chapters.append(
            MemoirBookPlanChapterInput(
                title=chapter.title,
                summary=chapter.summary,
                source_refs=refs,
            )
        )
    if not chapters:
        raise HTTPException(status_code=400, detail="章节规划不能为空")
    return chapters


async def confirm_book_plan(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookPlanConfirmRequest,
) -> MemoirBookPlanResponse:
    project = await load_memoir_project(db, project_id, user_id)
    sources = await _source_segments(db, user_id=user_id, project_id=project_id)
    chapters = _validated_chapters(req, sources)
    return await _replace_plan(
        db,
        project_id=project_id,
        title=req.title or f"{project.title}章节规划",
        chapters=chapters,
        status=MemoirBookPlanStatus.CONFIRMED,
        created_by=MemoirCreatedBy.USER,
        model=None,
        prompt_snapshot="",
        metadata_json={
            "source_utterance_count": len(sources),
            "confirmed_by": "user",
        },
    )
