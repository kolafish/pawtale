from __future__ import annotations

from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
import logging
import time

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow_naive
from app.domain.memoir.types import MemoirCreatedBy, MemoirOutputStatus
from app.models.memoir import (
    MemoirBookOutput,
    MemoirBookOutputVersion,
)
from app.models.projects import Project
from app.services.ai_calls import generate_structured_with_client
from app.services.ai_factory import get_ai_service
from app.services.ai_references import ModelReferenceMap
from app.services.ai_timeouts import (
    ai_error_metadata,
    ai_text_timeout_seconds,
    ai_user_error_message,
)
from app.services.ai_types import AITextClient
from app.services.ai_workloads import AITextWorkload, ai_text_workload_policy
from app.services.memoir import book_review_answer_state, book_reviews
from app.services.memoir.book_output_manuscript import (
    effective_book_metadata,
    manuscript_from_snapshot,
    stored_cover_style,
)
from app.services.memoir.book_output_snapshots import (
    book_content_from_chapter_snapshots,
    sectioned_chapter_content,
)
from app.services.memoir.narrative.book_output_assembly import (
    assemble_book_output,
)


logger = logging.getLogger(__name__)
BOOK_REVIEW_ANSWER_APPLICATION_ENGINE = "llm_book_review_answer_application_v1"
BOOK_REVIEW_ANSWER_APPLICATION_SYSTEM_PROMPT = (
    "你是一位克制的中文回忆录编辑。"
    "你只根据用户明确写下的回答调整已有书稿，不推测、不补造人生事实，"
    "并保持没有关联的章节原样不动。"
)
ApplicationProgressCallback = Callable[[dict[str, object]], Awaitable[None]]


class _SectionRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_id: str
    content: str


class _ChapterRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_id: str
    title: str = ""
    section_revisions: list[_SectionRevision] = Field(default_factory=list)


class _ApplicationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    chapter_order: list[str] = Field(default_factory=list)
    chapter_revisions: list[_ChapterRevision] = Field(default_factory=list)


@dataclass(frozen=True)
class BookReviewAnswerApplicationResult:
    output_version_id: str
    output_version: int
    applied_answer_count: int
    changed_chapter_count: int
    summary: str
    requested_ai_model: str | None
    resolved_ai_model: str | None
    generation: dict[str, object]


class BookReviewAnswerApplicationError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        generation: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.generation = generation or {}


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _chapter_ids(snapshots: list[dict[str, object]]) -> list[str]:
    return [
        chapter_id
        for snapshot in snapshots
        if (chapter_id := _text(snapshot.get("chapter_id")))
    ]


def _build_prompt(
    *,
    snapshots: list[dict[str, object]],
    context: book_review_answer_state.BookReviewAnswerContext,
    chapter_refs: ModelReferenceMap,
    section_refs: ModelReferenceMap,
) -> str:
    related_ids = context.related_chapter_ids
    chapter_overview = "\n".join(
        (
            f"chapter_id: {chapter_refs.encode(_text(snapshot.get('chapter_id')))}\n"
            f"章节名: {_text(snapshot.get('chapter_title'))}\n"
            f"章节摘要: {_text(snapshot.get('chapter_summary')) or _text(snapshot.get('content'))[:500]}"
        )
        for snapshot in snapshots
        if _text(snapshot.get("chapter_id"))
    )
    related_chapters: list[str] = []
    for snapshot in snapshots:
        chapter_id = _text(snapshot.get("chapter_id"))
        if not chapter_id or chapter_id not in related_ids:
            continue
        section_parts: list[str] = []
        raw_sections = snapshot.get("sections")
        for section in raw_sections if isinstance(raw_sections, list) else []:
            if not isinstance(section, dict):
                continue
            section_id = _text(section.get("section_id"))
            if not section_id:
                continue
            section_parts.append(
                f"section_id: {section_refs.encode(section_id)}\n"
                f"小节名: {_text(section.get('title'))}\n"
                f"正文:\n{_text(section.get('content'))}"
            )
        related_chapters.append(
            f"chapter_id: {chapter_refs.encode(chapter_id)}\n"
            f"章节名: {_text(snapshot.get('chapter_title'))}\n"
            + "\n\n".join(section_parts)
        )
    answers = "\n\n".join(
        (
            f"问题 {index}：{answer.question}\n"
            f"问题原因：{answer.reason}\n"
            f"用户回答：{answer.answer}\n"
            "关联章节："
            + "、".join(chapter_refs.encode(value) for value in answer.chapter_ids)
        )
        for index, answer in enumerate(context.answers, start=1)
    )
    return f"""请把用户对全书检查问题的回答应用到现有书稿。

全书章节顺序：
{chapter_overview}

用户回答：
{answers}

允许修改的关联章节原文：
{chr(10).join(related_chapters)}

请输出严格 JSON：
{{
  "summary": "一句话说明本次实际调整；如果无需改文，明确说明已核对但正文无需调整",
  "chapter_order": ["按调整后顺序列出全部保留的 chapter_id"],
  "chapter_revisions": [
    {{
      "chapter_id": "需要修改的 chapter_id",
      "title": "只有确需改名时才填写，否则为空字符串",
      "section_revisions": [
        {{
          "section_id": "需要修改的小节 section_id",
          "content": "修改后的完整小节正文"
        }}
      ]
    }}
  ]
}}

要求：
- chapter_id 和 section_id 只能使用输入中的本次请求短编号。
- 只修改与回答关联的章节；无关章节不得改名、删减或改写。
- 回答是用户明确提供的事实或决定，可以写入相关正文；除此之外不能新增事实。
- 若用户明确要求不收录某个关联章节，可从 chapter_order 中移除；其他章节必须保留。
- 若无需调整章节顺序，chapter_order 仍按原顺序列出全部章节。
- 若修改小节，必须返回该小节修改后的完整正文，不能只返回差异片段。
- 保留原文叙述视角、语气和已有事实，避免为了润色大幅重写。
- 不需要修改的章节和小节不要出现在 chapter_revisions。
"""


def _normalize_application(
    *,
    generated: _ApplicationOutput,
    snapshots: list[dict[str, object]],
    context: book_review_answer_state.BookReviewAnswerContext,
    chapter_refs: ModelReferenceMap,
    section_refs: ModelReferenceMap,
) -> tuple[list[dict[str, object]], set[str], str]:
    original_ids = _chapter_ids(snapshots)
    order = (
        [chapter_refs.decode(value) for value in generated.chapter_order]
        if generated.chapter_order
        else list(original_ids)
    )
    if not order or len(order) != len(set(order)):
        raise ValueError("模型返回的章节顺序无效")
    original_set = set(original_ids)
    if not set(order) <= original_set:
        raise ValueError("模型返回了书稿中不存在的章节")
    omitted = original_set - set(order)
    if omitted - context.related_chapter_ids:
        raise ValueError("模型试图移除与回答无关的章节")

    snapshots_by_id = {
        _text(snapshot.get("chapter_id")): deepcopy(snapshot)
        for snapshot in snapshots
        if _text(snapshot.get("chapter_id"))
    }
    section_owner: dict[str, str] = {}
    for chapter_id, snapshot in snapshots_by_id.items():
        raw_sections = snapshot.get("sections")
        for section in raw_sections if isinstance(raw_sections, list) else []:
            if isinstance(section, dict):
                section_id = _text(section.get("section_id"))
                if section_id:
                    section_owner[section_id] = chapter_id

    changed_ids = set(omitted)
    revised_chapters: set[str] = set()
    revised_sections: set[str] = set()
    for revision in generated.chapter_revisions:
        chapter_id = chapter_refs.decode(revision.chapter_id)
        if chapter_id in revised_chapters:
            raise ValueError("模型重复修改了同一章节")
        revised_chapters.add(chapter_id)
        if chapter_id not in context.related_chapter_ids:
            raise ValueError("模型试图修改与回答无关的章节")
        if chapter_id not in snapshots_by_id or chapter_id not in order:
            raise ValueError("模型修改的章节不在调整后书稿中")
        snapshot = snapshots_by_id[chapter_id]
        title = revision.title.strip()
        if title and title != _text(snapshot.get("chapter_title")):
            snapshot["chapter_title"] = title[:200]
            changed_ids.add(chapter_id)
        raw_sections = snapshot.get("sections")
        sections = (
            [section for section in raw_sections if isinstance(section, dict)]
            if isinstance(raw_sections, list)
            else []
        )
        sections_by_id = {
            _text(section.get("section_id")): section
            for section in sections
            if _text(section.get("section_id"))
        }
        for section_revision in revision.section_revisions:
            section_id = section_refs.decode(section_revision.section_id)
            if section_id in revised_sections:
                raise ValueError("模型重复修改了同一小节")
            revised_sections.add(section_id)
            if section_owner.get(section_id) != chapter_id:
                raise ValueError("模型把小节写入了错误章节")
            content = section_revision.content.strip()
            if not content:
                raise ValueError("模型返回了空的小节正文")
            section = sections_by_id[section_id]
            if content != _text(section.get("content")):
                section["content"] = content
                section["content_char_count"] = len(content)
                changed_ids.add(chapter_id)
        snapshot["sections"] = sections
        snapshot["section_count"] = len(sections)
        content = sectioned_chapter_content(sections)
        snapshot["content"] = content
        snapshot["content_char_count"] = len(content)

    original_positions = {
        chapter_id: index
        for index, chapter_id in enumerate(original_ids, start=1)
    }
    revised_snapshots: list[dict[str, object]] = []
    for index, chapter_id in enumerate(order, start=1):
        snapshot = snapshots_by_id[chapter_id]
        if original_positions.get(chapter_id) != index:
            changed_ids.add(chapter_id)
        snapshot["chapter_index"] = index
        snapshot["chapter_sort_order"] = index - 1
        revised_snapshots.append(snapshot)
    summary = " ".join(generated.summary.split())[:400]
    return revised_snapshots, changed_ids, summary


async def _latest_output_version(
    db: AsyncSession,
    *,
    output_id: str,
) -> MemoirBookOutputVersion | None:
    return await db.scalar(
        select(MemoirBookOutputVersion)
        .where(MemoirBookOutputVersion.output_id == output_id)
        .order_by(
            MemoirBookOutputVersion.version.desc(),
            MemoirBookOutputVersion.created_at.desc(),
            MemoirBookOutputVersion.id.desc(),
        )
        .limit(1)
    )


def _stored_illustrations(
    manuscript: object,
) -> tuple[dict[str, list[dict[str, object]]], list[dict[str, object]]]:
    if not isinstance(manuscript, dict):
        return {}, []
    illustrations: dict[str, list[dict[str, object]]] = {}
    raw_chapters = manuscript.get("chapters")
    for chapter in raw_chapters if isinstance(raw_chapters, list) else []:
        if not isinstance(chapter, dict):
            continue
        chapter_id = _text(chapter.get("chapter_id"))
        raw_items = chapter.get("illustrations")
        if chapter_id and isinstance(raw_items, list):
            illustrations[chapter_id] = [
                item for item in raw_items if isinstance(item, dict)
            ]
    raw_appendix = manuscript.get("image_appendix")
    appendix = (
        [item for item in raw_appendix if isinstance(item, dict)]
        if isinstance(raw_appendix, list)
        else []
    )
    return illustrations, appendix


async def apply_book_review_answers(
    db: AsyncSession,
    *,
    project_id: str,
    expected_review_input_signature: str,
    expected_answers_signature: str,
    ai_model: str | None,
    progress_callback: ApplicationProgressCallback | None = None,
    ai_client: AITextClient | None = None,
) -> BookReviewAnswerApplicationResult:
    review = await book_reviews.get_current_book_review(db, project_id=project_id)
    if review is None or review.input_signature != expected_review_input_signature:
        raise BookReviewAnswerApplicationError("全书内容已经变化，请重新检查后再应用回答")
    context = await book_review_answer_state.load_answer_context(db, review=review)
    if context.unresolved_count:
        raise BookReviewAnswerApplicationError("请先处理完所有需要确认的问题")
    if not context.answers:
        raise BookReviewAnswerApplicationError("当前没有可以应用到书稿的回答")
    if context.answers_signature != expected_answers_signature:
        raise BookReviewAnswerApplicationError("问题回答已经变化，请重新提交")

    if progress_callback is not None:
        await progress_callback({"stage": "assembling"})
    assembled = await assemble_book_output(
        db,
        project_id=project_id,
        allow_unapplied_review_answers=True,
    )
    output = await db.scalar(
        select(MemoirBookOutput).where(MemoirBookOutput.project_id == project_id)
    )
    if output is None:
        raise BookReviewAnswerApplicationError("书稿还没有生成")
    baseline = await db.get(MemoirBookOutputVersion, assembled.output_version_id)
    if baseline is None:
        raise BookReviewAnswerApplicationError("没有找到当前书稿版本")
    if book_review_answer_state.marker_matches_context(
        output=output,
        version=baseline,
        context=context,
    ):
        marker = book_review_answer_state.application_marker(output)
        return BookReviewAnswerApplicationResult(
            output_version_id=baseline.id,
            output_version=baseline.version,
            applied_answer_count=len(context.answers),
            changed_chapter_count=int(marker.get("changed_chapter_count") or 0),
            summary=_text(marker.get("summary")) or "这些回答已经应用到书稿",
            requested_ai_model=ai_model,
            resolved_ai_model=(
                _text(marker.get("resolved_ai_model")) or ai_model
            ),
            generation={},
        )

    snapshots = [
        item
        for item in baseline.chapter_snapshot_json
        if isinstance(item, dict)
    ]
    chapter_ids = _chapter_ids(snapshots)
    section_ids = [
        section_id
        for snapshot in snapshots
        for section in (
            snapshot.get("sections")
            if isinstance(snapshot.get("sections"), list)
            else []
        )
        if isinstance(section, dict)
        and (section_id := _text(section.get("section_id")))
    ]
    chapter_refs = ModelReferenceMap("C", chapter_ids)
    section_refs = ModelReferenceMap("S", section_ids)
    prompt = _build_prompt(
        snapshots=snapshots,
        context=context,
        chapter_refs=chapter_refs,
        section_refs=section_refs,
    )
    if progress_callback is not None:
        await progress_callback({"stage": "revising"})
    client = ai_client or get_ai_service(ai_model)
    policy = ai_text_workload_policy(AITextWorkload.LONG_GENERATION_STRUCTURED)
    timeout_seconds = ai_text_timeout_seconds(policy.timeout_profile)
    started_at = time.monotonic()
    try:
        generated = await generate_structured_with_client(
            client,
            BOOK_REVIEW_ANSWER_APPLICATION_SYSTEM_PROMPT,
            prompt,
            output_model=_ApplicationOutput,
            requested_model=ai_model,
            temperature=0.2,
            max_tokens=policy.max_tokens,
            timeout=timeout_seconds,
            max_attempts=1,
        )
        revised_snapshots, changed_ids, summary = _normalize_application(
            generated=generated.value,
            snapshots=snapshots,
            context=context,
            chapter_refs=chapter_refs,
            section_refs=section_refs,
        )
        generation = {
            **generated.response.diagnostics(),
            "engine": BOOK_REVIEW_ANSWER_APPLICATION_ENGINE,
            "timeout_seconds": timeout_seconds,
        }
    except Exception as exc:
        logger.exception(
            "memoir book review answer application failed project_id=%s",
            project_id,
        )
        generation = {
            **ai_error_metadata(
                exc,
                requested_model=ai_model,
                provider=getattr(client, "provider_name", None),
                resolved_model=getattr(client, "model_name", ai_model),
                timeout_seconds=timeout_seconds,
                elapsed_ms=round((time.monotonic() - started_at) * 1000),
            ),
            "engine": BOOK_REVIEW_ANSWER_APPLICATION_ENGINE,
        }
        raise BookReviewAnswerApplicationError(
            ai_user_error_message(
                exc,
                "根据回答调整书稿时遇到问题，请稍后重试",
            ),
            generation=generation,
        ) from exc

    if progress_callback is not None:
        await progress_callback({"stage": "saving"})
    db.expire_all()
    current_review = await book_reviews.get_current_book_review(
        db,
        project_id=project_id,
    )
    if current_review is None:
        raise BookReviewAnswerApplicationError("调整期间全书内容已变化，请重试")
    current_context = await book_review_answer_state.load_answer_context(
        db,
        review=current_review,
    )
    current_output = await db.scalar(
        select(MemoirBookOutput).where(MemoirBookOutput.project_id == project_id)
    )
    latest = (
        await _latest_output_version(db, output_id=current_output.id)
        if current_output is not None
        else None
    )
    if (
        current_review.input_signature != expected_review_input_signature
        or current_context.answers_signature != expected_answers_signature
        or latest is None
        or latest.id != baseline.id
    ):
        raise BookReviewAnswerApplicationError("调整期间全书或回答已变化，请重试")

    output = await db.get(MemoirBookOutput, current_output.id)
    baseline = await db.get(MemoirBookOutputVersion, baseline.id)
    if output is None or baseline is None:
        raise BookReviewAnswerApplicationError("保存调整结果时书稿状态已变化")
    summary = summary or (
        "已根据回答调整相关章节"
        if changed_ids
        else "已核对这些回答，现有正文无需调整"
    )
    applied_version = baseline
    if changed_ids:
        project = await db.get(Project, project_id)
        if project is None:
            raise BookReviewAnswerApplicationError("项目不存在")
        content = book_content_from_chapter_snapshots(
            title=output.title,
            snapshots=revised_snapshots,
        )
        illustrations, image_appendix = _stored_illustrations(
            baseline.manuscript_json
        )
        manuscript = manuscript_from_snapshot(
            title=output.title,
            content=content,
            snapshot=revised_snapshots,
            cover_style_value=stored_cover_style(baseline),
            metadata=effective_book_metadata(
                project=project,
                stored=baseline.manuscript_json,
            ),
            chapter_illustrations_by_chapter=illustrations,
            image_appendix=image_appendix,
        )
        next_version = int(
            await db.scalar(
                select(func.max(MemoirBookOutputVersion.version)).where(
                    MemoirBookOutputVersion.output_id == output.id
                )
            )
            or 0
        ) + 1
        applied_version = MemoirBookOutputVersion(
            output_id=output.id,
            project_id=project_id,
            plan_id=output.plan_id,
            version=next_version,
            content=content,
            chapter_snapshot_json=revised_snapshots,
            manuscript_json=(
                manuscript.model_dump(mode="json") if manuscript else {}
            ),
            created_by=MemoirCreatedBy.AI.value,
        )
        db.add(applied_version)
        await db.flush()

    output.status = MemoirOutputStatus.DRAFT.value
    output.metadata_json = {
        **(output.metadata_json if isinstance(output.metadata_json, dict) else {}),
        "source": "book_review_answers",
        "book_tts": None,
        book_review_answer_state.BOOK_REVIEW_ANSWER_APPLICATION_KEY: {
            "review_input_signature": current_review.input_signature,
            "answers_signature": current_context.answers_signature,
            "output_version_id": applied_version.id,
            "applied_issue_ids": [
                answer.issue_id for answer in current_context.answers
            ],
            "applied_answer_count": len(current_context.answers),
            "changed_chapter_count": len(changed_ids),
            "summary": summary,
            "resolved_ai_model": generated.response.resolved_model,
            "engine": BOOK_REVIEW_ANSWER_APPLICATION_ENGINE,
        },
    }
    output.updated_at = utcnow_naive()
    db.add(output)
    await db.flush()
    return BookReviewAnswerApplicationResult(
        output_version_id=applied_version.id,
        output_version=applied_version.version,
        applied_answer_count=len(current_context.answers),
        changed_chapter_count=len(changed_ids),
        summary=summary,
        requested_ai_model=ai_model,
        resolved_ai_model=generated.response.resolved_model,
        generation=generation,
    )
