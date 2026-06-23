from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import MemoirBookChapterDraftStatus, MemoirBookPlanStatus, MemoirCreatedBy
from app.models.memoir import (
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirBookChapterDraft,
    MemoirChatMessage,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.schemas.memoir_book import (
    MemoirBookChapterTitleGenerateRequest,
    MemoirBookChapterTitleUpdateRequest,
    MemoirBookChapterSourceRef,
    MemoirBookPlanChapterInput,
    MemoirBookPlanChapterResponse,
    MemoirBookPlanConfirmRequest,
    MemoirBookPlanGenerateRequest,
    MemoirBookPlanUpdateRequest,
    MemoirBookPlanResponse,
    MemoirBookSourceAssignmentRequest,
    MemoirBookSourceCoverageItem,
    MemoirBookSourceCoverageResponse,
    MemoirBookSourceIncludedChapter,
)
from app.services import get_ai_service
from app.services.ai_types import AITextClient
from app.services.json_utils import extract_json_payload
from app.services.memoir.access import load_memoir_project


SYSTEM_PROMPT = "你是一位谨慎的中文回忆录章节规划编辑，只能根据给定口述转写整理章节。"
CHAPTER_TITLE_SYSTEM_PROMPT = "你是一位谨慎的中文回忆录目录编辑，只为已有章节重新拟标题。"
AI_BOOK_PLAN_UNAVAILABLE_MESSAGE = "AI 服务暂时不可用，请稍后重试。"
logger = logging.getLogger(__name__)
GENERIC_CHAPTER_TITLES = {"新收录转写", "新增转写", "转写记录", "新章节", "未命名章节"}


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
    created_at: datetime

    def ref(self, quote: str = "") -> MemoirBookChapterSourceRef:
        return MemoirBookChapterSourceRef(
            voice_session_id=self.voice_session_id,
            message_id=self.message_id,
            utterance_id=self.utterance_id,
            transcript_version_id=self.transcript_version_id,
            audio_file_id=self.audio_file_id,
            start_ms=self.start_ms,
            end_ms=self.end_ms,
            created_at=self.created_at,
            quote=_safe_quote(self.text, quote),
        )


@dataclass(frozen=True)
class ValidatedChapterUpdate:
    id: str | None
    title: str
    source_refs: list[MemoirBookChapterSourceRef]


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
    return ref.model_dump(mode="json")


def _merge_metadata(metadata_json: dict | None, patch: dict[str, object]) -> dict[str, object]:
    return {**(metadata_json or {}), **patch}


def _source_ref_utterance_ids(refs: list[dict] | list[MemoirBookChapterSourceRef]) -> list[str]:
    ids: list[str] = []
    for ref in refs:
        if isinstance(ref, MemoirBookChapterSourceRef):
            ids.append(ref.utterance_id)
        elif isinstance(ref, dict) and isinstance(ref.get("utterance_id"), str):
            ids.append(ref["utterance_id"])
    return ids


def _set_source_change_metadata(
    metadata_json: dict | None,
    *,
    added_ids: set[str],
    removed_ids: set[str],
) -> dict[str, object]:
    metadata = dict(metadata_json or {})
    if added_ids:
        metadata["pending_source_utterance_ids"] = sorted(added_ids)
    else:
        metadata.pop("pending_source_utterance_ids", None)
    if removed_ids:
        metadata["removed_source_utterance_ids"] = sorted(removed_ids)
    else:
        metadata.pop("removed_source_utterance_ids", None)
    metadata["source_refs_updated_at"] = datetime.now(UTC).isoformat()
    return metadata


def _source_ref_response(
    ref: dict[str, object],
    created_at_by_utterance: dict[str, datetime],
) -> MemoirBookChapterSourceRef:
    data = dict(ref)
    utterance_id = data.get("utterance_id")
    if not data.get("created_at") and isinstance(utterance_id, str):
        created_at = created_at_by_utterance.get(utterance_id)
        if created_at:
            data["created_at"] = created_at
    return MemoirBookChapterSourceRef.model_validate(data)


async def _source_ref_created_at_by_utterance(
    db: AsyncSession,
    plan: MemoirBookPlan,
) -> dict[str, datetime]:
    utterance_ids = {
        ref["utterance_id"]
        for chapter in plan.chapters
        for ref in (chapter.source_refs_json or [])
        if isinstance(ref, dict) and isinstance(ref.get("utterance_id"), str)
    }
    if not utterance_ids:
        return {}
    result = await db.execute(
        select(MemoirVoiceUtterance.id, MemoirVoiceUtterance.created_at).where(
            MemoirVoiceUtterance.id.in_(utterance_ids)
        )
    )
    return {utterance_id: created_at for utterance_id, created_at in result.all()}


def _chapter_response(
    chapter: MemoirBookPlanChapter,
    *,
    created_at_by_utterance: dict[str, datetime] | None = None,
) -> MemoirBookPlanChapterResponse:
    source_created_at = created_at_by_utterance or {}
    return MemoirBookPlanChapterResponse(
        id=chapter.id,
        plan_id=chapter.plan_id,
        project_id=chapter.project_id,
        title=chapter.title,
        sort_order=chapter.sort_order,
        source_refs=[
            _source_ref_response(ref, source_created_at)
            for ref in (chapter.source_refs_json or [])
            if isinstance(ref, dict)
        ],
        metadata_json=chapter.metadata_json or {},
        created_at=chapter.created_at,
        updated_at=chapter.updated_at,
    )


async def _plan_response(db: AsyncSession, plan: MemoirBookPlan) -> MemoirBookPlanResponse:
    created_at_by_utterance = await _source_ref_created_at_by_utterance(db, plan)
    return MemoirBookPlanResponse(
        id=plan.id,
        project_id=plan.project_id,
        title=plan.title,
        status=MemoirBookPlanStatus(plan.status),
        created_by=MemoirCreatedBy(plan.created_by),
        model=plan.model,
        metadata_json=plan.metadata_json or {},
        chapters=[
            _chapter_response(chapter, created_at_by_utterance=created_at_by_utterance)
            for chapter in plan.chapters
        ],
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
                created_at=utterance.created_at,
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
      "source_indices": [1, 2],
      "evidence_quote": "必须来自 source text 的短句"
    }}
  ]
}}

要求：
- 只根据 source text 已经讲过的事实划分章节，不新增人物、时间、地点、事件或情绪。
- 章节标题要短，像书稿目录，不要像 AI 总结标题。
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
                sort_order=index,
                source_refs_json=[_as_dict(ref) for ref in chapter.source_refs],
            )
        )
    await db.commit()
    refreshed = await _load_plan(db, project_id)
    if not refreshed:
        raise HTTPException(status_code=500, detail="章节规划保存失败")
    return await _plan_response(db, refreshed)


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
    return await _plan_response(db, plan)


def _covered_utterance_ids(plan: MemoirBookPlan | None) -> set[str]:
    if not plan:
        return set()
    covered: set[str] = set()
    for chapter in plan.chapters:
        for ref in chapter.source_refs_json or []:
            if isinstance(ref, dict) and isinstance(ref.get("utterance_id"), str):
                covered.add(ref["utterance_id"])
    return covered


def _included_chapters_by_utterance(
    plan: MemoirBookPlan | None,
) -> dict[str, list[MemoirBookSourceIncludedChapter]]:
    if not plan:
        return {}
    included: dict[str, list[MemoirBookSourceIncludedChapter]] = {}
    for chapter in sorted(plan.chapters, key=lambda item: item.sort_order):
        chapter_item = MemoirBookSourceIncludedChapter(
            chapter_id=chapter.id,
            title=chapter.title,
            sort_order=chapter.sort_order,
        )
        for ref in chapter.source_refs_json or []:
            if not isinstance(ref, dict) or not isinstance(ref.get("utterance_id"), str):
                continue
            included.setdefault(ref["utterance_id"], []).append(chapter_item)
    return included


def _source_coverage_item(
    source: SourceSegment,
    included_chapters: list[MemoirBookSourceIncludedChapter] | None = None,
) -> MemoirBookSourceCoverageItem:
    included = included_chapters or []
    return MemoirBookSourceCoverageItem(
        source_index=source.source_index,
        voice_session_id=source.voice_session_id,
        message_id=source.message_id,
        utterance_id=source.utterance_id,
        transcript_version_id=source.transcript_version_id,
        audio_file_id=source.audio_file_id,
        start_ms=source.start_ms,
        end_ms=source.end_ms,
        text=source.text,
        created_at=source.created_at,
        is_included=bool(included),
        included_in_chapters=included,
    )


async def get_book_source_coverage(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
) -> MemoirBookSourceCoverageResponse:
    sources = await _source_segments(db, user_id=user_id, project_id=project_id)
    plan = await _load_plan(db, project_id)
    included_by_utterance = _included_chapters_by_utterance(plan)
    covered_ids = set(included_by_utterance)
    uncovered_sources = [
        source for source in sources
        if source.utterance_id not in covered_ids
    ]
    covered_source_count = len(sources) - len(uncovered_sources)
    return MemoirBookSourceCoverageResponse(
        project_id=project_id,
        plan_id=plan.id if plan else None,
        total_source_count=len(sources),
        covered_source_count=covered_source_count,
        uncovered_source_count=len(uncovered_sources),
        sources=[
            _source_coverage_item(source, included_by_utterance.get(source.utterance_id, []))
            for source in sources
        ],
        uncovered_sources=[_source_coverage_item(source) for source in uncovered_sources],
    )


async def assign_book_sources_to_chapters(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookSourceAssignmentRequest,
) -> MemoirBookPlanResponse:
    await load_memoir_project(db, project_id, user_id)
    plan = await _load_plan(db, project_id)
    if not plan:
        raise HTTPException(status_code=404, detail="章节规划不存在")
    if plan.status != MemoirBookPlanStatus.CONFIRMED.value:
        raise HTTPException(status_code=400, detail="请先确认章节规划")

    sources = await _source_segments(db, user_id=user_id, project_id=project_id)
    source_by_utterance = {source.utterance_id: source for source in sources}
    covered_ids = _covered_utterance_ids(plan)
    chapter_by_id = {chapter.id: chapter for chapter in plan.chapters}

    affected_chapter_ids: set[str] = set()
    for assignment in req.assignments:
        chapter = chapter_by_id.get(assignment.chapter_id)
        if not chapter:
            raise HTTPException(status_code=404, detail="章节不存在")

        refs = list(chapter.source_refs_json or [])
        pending_ids = set(
            item for item in (chapter.metadata_json or {}).get("pending_source_utterance_ids", [])
            if isinstance(item, str)
        )
        for utterance_id in assignment.utterance_ids:
            source = source_by_utterance.get(utterance_id)
            if not source:
                raise HTTPException(status_code=400, detail="转写不属于当前回忆录")
            if utterance_id in covered_ids:
                raise HTTPException(status_code=400, detail="转写已收录到章节")
            refs.append(_as_dict(source.ref()))
            covered_ids.add(utterance_id)
            pending_ids.add(utterance_id)
            affected_chapter_ids.add(chapter.id)

        chapter.source_refs_json = refs
        chapter.metadata_json = _merge_metadata(
            chapter.metadata_json,
            {
                "pending_source_utterance_ids": sorted(pending_ids),
                "source_assignment_updated_at": datetime.now(UTC).isoformat(),
            },
        )
        db.add(chapter)

    if affected_chapter_ids:
        drafts = (
            await db.execute(
                select(MemoirBookChapterDraft).where(
                    MemoirBookChapterDraft.project_id == project_id,
                    MemoirBookChapterDraft.chapter_id.in_(affected_chapter_ids),
                )
            )
        ).scalars().all()
        for draft in drafts:
            if draft.status == MemoirBookChapterDraftStatus.CONFIRMED.value:
                draft.status = MemoirBookChapterDraftStatus.DRAFT.value
                draft.metadata_json = _merge_metadata(
                    draft.metadata_json,
                    {"status_reason": "new_sources_assigned"},
                )
                db.add(draft)

    await db.commit()
    refreshed = await _load_plan(db, project_id)
    if not refreshed:
        raise HTTPException(status_code=500, detail="章节规划保存失败")
    return await _plan_response(db, refreshed)


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
                source_refs=refs,
            )
        )
    if not chapters:
        raise HTTPException(status_code=400, detail="章节规划不能为空")
    return chapters


def _validated_chapter_updates(
    req: MemoirBookPlanUpdateRequest,
    sources: list[SourceSegment],
) -> list[ValidatedChapterUpdate]:
    source_by_utterance = {source.utterance_id: source for source in sources}
    chapters: list[ValidatedChapterUpdate] = []
    seen_plan_utterances: set[str] = set()
    for chapter in req.chapters:
        refs: list[MemoirBookChapterSourceRef] = []
        seen_chapter_utterances: set[str] = set()
        for ref in chapter.source_refs:
            source = source_by_utterance.get(ref.utterance_id)
            if not source:
                raise HTTPException(status_code=400, detail="章节来源不属于当前回忆录")
            if source.utterance_id in seen_chapter_utterances:
                continue
            if source.utterance_id in seen_plan_utterances:
                raise HTTPException(status_code=400, detail="同一条转写不能重复归入多个章节")
            refs.append(source.ref(ref.quote))
            seen_chapter_utterances.add(source.utterance_id)
            seen_plan_utterances.add(source.utterance_id)
        if not refs:
            raise HTTPException(status_code=400, detail="每个章节都需要至少一个来源")
        chapters.append(
            ValidatedChapterUpdate(
                id=chapter.id,
                title=chapter.title,
                source_refs=refs,
            )
        )
    if not chapters:
        raise HTTPException(status_code=400, detail="章节规划不能为空")
    return chapters


def _chapter_by_id(plan: MemoirBookPlan, chapter_id: str) -> MemoirBookPlanChapter:
    for chapter in plan.chapters:
        if chapter.id == chapter_id:
            return chapter
    raise HTTPException(status_code=404, detail="章节不存在")


def _chapter_source_texts(chapter: MemoirBookPlanChapter, sources: list[SourceSegment]) -> list[str]:
    source_by_utterance = {source.utterance_id: source for source in sources}
    texts: list[str] = []
    for ref in chapter.source_refs_json or []:
        if not isinstance(ref, dict):
            continue
        utterance_id = ref.get("utterance_id")
        source = source_by_utterance.get(utterance_id) if isinstance(utterance_id, str) else None
        text = source.text if source else str(ref.get("quote") or "")
        text = _text_or_empty(text)
        if text:
            texts.append(text)
    return texts


def _build_chapter_title_prompt(chapter: MemoirBookPlanChapter, source_texts: list[str]) -> str:
    source_text = "\n\n".join(f"- {text}" for text in source_texts[:8])
    return f"""请为下面这一个回忆录章节重新拟一个正式章节名。

当前章节名：{chapter.title}

章节来源口述：
{source_text or "无"}

要求：
- 只根据章节来源口述，不新增事实、人物、地点或情绪。
- 标题要像书稿目录，短而正式，建议 6 到 14 个汉字。
- 不要使用“转写”“新收录”“记录”“素材”“章节”等占位词。
- 直接输出标题，不要解释，不要引号，不要编号。
"""


def _clean_generated_chapter_title(content: str) -> str:
    payload = extract_json_payload(content)
    title = ""
    if isinstance(payload, dict):
        value = payload.get("title")
        title = value.strip() if isinstance(value, str) else ""
    if not title:
        lines = [line.strip() for line in str(content).splitlines() if line.strip()]
        title = lines[0] if lines else ""
    title = title.strip().strip("“”\"'「」《》")
    for prefix in ("章节标题：", "标题：", "章名：", "章节名："):
        if title.startswith(prefix):
            title = title[len(prefix):].strip()
    title = title.strip("。；;，, ")
    if title in GENERIC_CHAPTER_TITLES or "转写" in title or "新收录" in title:
        raise HTTPException(status_code=502, detail="AI 未返回可用章节标题")
    if not title:
        raise HTTPException(status_code=502, detail="AI 未返回可用章节标题")
    return title[:80]


async def _save_chapter_title(
    db: AsyncSession,
    *,
    project_id: str,
    plan: MemoirBookPlan,
    chapter: MemoirBookPlanChapter,
    title: str,
    updated_by: MemoirCreatedBy,
    model: str | None = None,
    prompt_snapshot: str = "",
) -> MemoirBookPlanResponse:
    now = datetime.now(UTC).isoformat()
    previous_title = chapter.title
    chapter.title = title.strip()[:200]
    chapter.metadata_json = _merge_metadata(
        chapter.metadata_json,
        {
            "title_updated_at": now,
            "title_updated_by": updated_by.value,
            "previous_title": previous_title,
            **({"title_model": model} if model else {}),
            **({"title_prompt_snapshot": prompt_snapshot} if prompt_snapshot else {}),
        },
    )
    plan.metadata_json = _merge_metadata(
        plan.metadata_json,
        {
            "chapter_title_updated_at": now,
            "chapter_title_updated_by": updated_by.value,
        },
    )
    db.add(chapter)
    db.add(plan)
    await db.commit()
    refreshed = await _load_plan(db, project_id)
    if not refreshed:
        raise HTTPException(status_code=500, detail="章节标题保存失败")
    return await _plan_response(db, refreshed)


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


async def update_book_plan(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookPlanUpdateRequest,
) -> MemoirBookPlanResponse:
    await load_memoir_project(db, project_id, user_id)
    plan = await _load_plan(db, project_id)
    if not plan:
        raise HTTPException(status_code=404, detail="章节规划不存在")

    sources = await _source_segments(db, user_id=user_id, project_id=project_id)
    chapter_updates = _validated_chapter_updates(req, sources)
    existing_by_id = {chapter.id: chapter for chapter in plan.chapters}
    retained_ids = {chapter.id for chapter in chapter_updates if chapter.id}
    unknown_ids = retained_ids - set(existing_by_id)
    if unknown_ids:
        raise HTTPException(status_code=404, detail="章节不存在")

    for index, chapter in enumerate(plan.chapters):
        chapter.sort_order = 10000 + index
        db.add(chapter)
    await db.flush()

    changed_chapter_ids: set[str] = set()
    now = datetime.now(UTC).isoformat()
    for index, chapter_update in enumerate(chapter_updates):
        if chapter_update.id:
            chapter = existing_by_id[chapter_update.id]
            old_ids = set(_source_ref_utterance_ids(chapter.source_refs_json or []))
            new_ids = set(_source_ref_utterance_ids(chapter_update.source_refs))
            added_ids = new_ids - old_ids
            removed_ids = old_ids - new_ids
            chapter.title = chapter_update.title.strip()[:200]
            chapter.sort_order = index
            chapter.source_refs_json = [_as_dict(ref) for ref in chapter_update.source_refs]
            if added_ids or removed_ids:
                chapter.metadata_json = _set_source_change_metadata(
                    chapter.metadata_json,
                    added_ids=added_ids,
                    removed_ids=removed_ids,
                )
                changed_chapter_ids.add(chapter.id)
            db.add(chapter)
        else:
            db.add(
                MemoirBookPlanChapter(
                    plan_id=plan.id,
                    project_id=project_id,
                    title=chapter_update.title.strip()[:200],
                    sort_order=index,
                    source_refs_json=[_as_dict(ref) for ref in chapter_update.source_refs],
                    metadata_json={"created_from_plan_update_at": now},
                )
            )

    removed_chapters = [
        chapter for chapter_id, chapter in existing_by_id.items()
        if chapter_id not in retained_ids
    ]
    for chapter in removed_chapters:
        if chapter in plan.chapters:
            plan.chapters.remove(chapter)
        else:
            await db.delete(chapter)

    if changed_chapter_ids:
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
            draft.metadata_json = _merge_metadata(
                draft.metadata_json,
                {"status_reason": "sources_changed"},
            )
            db.add(draft)

    plan.title = (req.title or plan.title).strip()[:200]
    plan.created_by = MemoirCreatedBy.USER.value
    plan.metadata_json = _merge_metadata(
        plan.metadata_json,
        {
            "structure_updated_at": now,
            "structure_updated_by": "user",
        },
    )
    db.add(plan)
    await db.commit()
    refreshed = await _load_plan(db, project_id)
    if not refreshed:
        raise HTTPException(status_code=500, detail="章节规划保存失败")
    return await _plan_response(db, refreshed)


async def update_book_chapter_title(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    chapter_id: str,
    req: MemoirBookChapterTitleUpdateRequest,
) -> MemoirBookPlanResponse:
    await load_memoir_project(db, project_id, user_id)
    plan = await _load_plan(db, project_id)
    if not plan:
        raise HTTPException(status_code=404, detail="章节规划不存在")
    chapter = _chapter_by_id(plan, chapter_id)
    return await _save_chapter_title(
        db,
        project_id=project_id,
        plan=plan,
        chapter=chapter,
        title=req.title,
        updated_by=MemoirCreatedBy.USER,
    )


async def generate_book_chapter_title(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    chapter_id: str,
    req: MemoirBookChapterTitleGenerateRequest,
    ai_client: AITextClient | None = None,
) -> MemoirBookPlanResponse:
    await load_memoir_project(db, project_id, user_id)
    plan = await _load_plan(db, project_id)
    if not plan:
        raise HTTPException(status_code=404, detail="章节规划不存在")
    chapter = _chapter_by_id(plan, chapter_id)
    sources = await _source_segments(db, user_id=user_id, project_id=project_id)
    prompt = _build_chapter_title_prompt(chapter, _chapter_source_texts(chapter, sources))

    client = ai_client
    try:
        if client is None:
            client = get_ai_service(req.ai_model)
        content = await client._call_llm(
            CHAPTER_TITLE_SYSTEM_PROMPT,
            prompt,
            temperature=0.25,
            max_tokens=160,
            timeout=60.0,
        )
    except HTTPException as exc:
        if exc.status_code < 500:
            raise
        logger.exception("AI memoir chapter title failed provider=%s", getattr(client, "provider_name", "unknown"))
        raise HTTPException(status_code=503, detail=AI_BOOK_PLAN_UNAVAILABLE_MESSAGE) from exc
    except Exception as exc:
        logger.exception("AI memoir chapter title failed provider=%s", getattr(client, "provider_name", "unknown"))
        raise HTTPException(status_code=503, detail=AI_BOOK_PLAN_UNAVAILABLE_MESSAGE) from exc

    title = _clean_generated_chapter_title(str(content))
    return await _save_chapter_title(
        db,
        project_id=project_id,
        plan=plan,
        chapter=chapter,
        title=title,
        updated_by=MemoirCreatedBy.AI,
        model=getattr(client, "model_name", req.ai_model),
        prompt_snapshot=prompt,
    )
