from __future__ import annotations

from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
import logging
import re
import time

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memoir import (
    MemoirBookOutput,
    MemoirBookOutputVersion,
)
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
from app.services.memoir import (
    book_output_versions,
    book_review_answer_state,
    book_revision_proposals,
    book_reviews,
)
from app.services.memoir.book_output_snapshots import (
    sectioned_chapter_content,
)
from app.services.memoir.narrative.book_output_assembly import (
    assemble_book_output,
)


logger = logging.getLogger(__name__)
BOOK_REVIEW_ANSWER_APPLICATION_ENGINE = "llm_book_review_answer_application_v3"
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


class _ApplicationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_revisions: list[_SectionRevision] = Field(default_factory=list)


@dataclass(frozen=True)
class BookReviewAnswerApplicationResult:
    proposal_id: str | None
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


_INTERNAL_MODEL_REFERENCE_PATTERN = re.compile(r"[CS]\d+")


def user_visible_application_summary(
    summary: object,
    *,
    changed_chapter_count: object,
) -> str:
    """Keep legacy model references out of user-visible job state."""
    value = " ".join(summary.split())[:400] if isinstance(summary, str) else ""
    if not value or not _INTERNAL_MODEL_REFERENCE_PATTERN.search(value):
        return value
    return (
        "修改预览已准备，包含根据回答生成的相关章节调整。"
        if isinstance(changed_chapter_count, int) and changed_chapter_count > 0
        else "已核对这些回答，现有正文无需调整。"
    )


def _application_summary(
    *,
    snapshots: list[dict[str, object]],
    changed_ids: set[str],
) -> str:
    if not changed_ids:
        return "已核对这些回答，现有正文无需调整。"
    titles = [
        title
        for snapshot in snapshots
        if _text(snapshot.get("chapter_id")) in changed_ids
        and (title := _text(snapshot.get("chapter_title")))
    ]
    if not titles:
        return "修改预览已准备，包含相关章节的正文调整。"
    labels = [f"《{title}》" for title in titles[:3]]
    if len(titles) <= 3:
        target = "、".join(labels)
    else:
        target = f"{'、'.join(labels)}等 {len(titles)} 个章节"
    return f"修改预览已准备，包含{target}的相关正文调整。"


def _build_prompt(
    *,
    snapshots: list[dict[str, object]],
    context: book_review_answer_state.BookReviewAnswerContext,
    chapter_refs: ModelReferenceMap,
    section_refs: ModelReferenceMap,
) -> str:
    related_ids = context.related_chapter_ids
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

用户回答：
{answers}

允许修改的关联章节原文：
{chr(10).join(related_chapters)}

请输出严格 JSON：
{{
  "section_revisions": [
    {{
      "section_id": "需要修改的小节 section_id",
      "content": "修改后的完整小节正文"
    }}
  ]
}}

要求：
- chapter_id 和 section_id 只能使用输入中的本次请求短编号。
- 只修改与回答关联章节中已有小节的正文。
- 回答是用户明确提供的事实或决定，可以写入相关正文；除此之外不能新增事实。
- 不得改变章节或小节的顺序、收录状态、标题、数量及归属关系。
- 若修改小节，必须返回该小节修改后的完整正文，不能只返回差异片段。
- 保留原文叙述视角、语气和已有事实，避免为了润色大幅重写。
- 不需要修改的小节不要出现在 section_revisions。
"""


def _normalize_application(
    *,
    generated: _ApplicationOutput,
    snapshots: list[dict[str, object]],
    context: book_review_answer_state.BookReviewAnswerContext,
    section_refs: ModelReferenceMap,
) -> tuple[list[dict[str, object]], set[str]]:
    original_ids = _chapter_ids(snapshots)
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

    changed_ids: set[str] = set()
    revised_sections: set[str] = set()
    for revision in generated.section_revisions:
        section_id = section_refs.decode(revision.section_id)
        if section_id in revised_sections:
            raise ValueError("模型重复修改了同一小节")
        revised_sections.add(section_id)
        chapter_id = section_owner.get(section_id)
        if not chapter_id or chapter_id not in context.related_chapter_ids:
            raise ValueError("模型试图修改与回答无关的小节")
        snapshot = snapshots_by_id[chapter_id]
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
        content = revision.content.strip()
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

    revised_snapshots = [snapshots_by_id[chapter_id] for chapter_id in original_ids]
    return revised_snapshots, changed_ids


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
        changed_chapter_count = int(marker.get("changed_chapter_count") or 0)
        return BookReviewAnswerApplicationResult(
            proposal_id=_text(marker.get("proposal_id")) or None,
            changed_chapter_count=changed_chapter_count,
            summary=(
                user_visible_application_summary(
                    marker.get("summary"),
                    changed_chapter_count=changed_chapter_count,
                )
                or "这些回答已经应用到书稿"
            ),
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
    related_chapter_ids = [
        chapter_id
        for chapter_id in chapter_ids
        if chapter_id in context.related_chapter_ids
    ]
    if not related_chapter_ids:
        raise BookReviewAnswerApplicationError("问题没有关联到可调整的书稿章节")
    if set(related_chapter_ids) != context.related_chapter_ids:
        raise BookReviewAnswerApplicationError("问题关联的章节已经变化，请重新检查全书")
    section_ids = [
        section_id
        for snapshot in snapshots
        if _text(snapshot.get("chapter_id")) in context.related_chapter_ids
        for section in (
            snapshot.get("sections")
            if isinstance(snapshot.get("sections"), list)
            else []
        )
        if isinstance(section, dict)
        and (section_id := _text(section.get("section_id")))
    ]
    chapter_refs = ModelReferenceMap("C", related_chapter_ids)
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
        revised_snapshots, changed_ids = _normalize_application(
            generated=generated.value,
            snapshots=snapshots,
            context=context,
            section_refs=section_refs,
        )
        summary = _application_summary(
            snapshots=revised_snapshots,
            changed_ids=changed_ids,
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
        await book_output_versions.load_latest_book_output_version(
            db,
            output_id=current_output.id,
        )
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

    baseline = await db.get(MemoirBookOutputVersion, baseline.id)
    if baseline is None:
        raise BookReviewAnswerApplicationError("保存调整结果时书稿状态已变化")
    comparison = book_revision_proposals.build_comparison(
        before=snapshots,
        after=revised_snapshots,
    )
    proposal = await book_revision_proposals.create_ready_proposal(
        db,
        review=current_review,
        baseline=baseline,
        candidate_snapshots=revised_snapshots,
        selected_actions={
            "source": "book_review_answers",
            "answers_signature": current_context.answers_signature,
            "issue_ids": [
                answer.issue_id for answer in current_context.answers
            ],
        },
        comparison=comparison,
        generation=generation,
        summary=summary,
    )
    return BookReviewAnswerApplicationResult(
        proposal_id=proposal.id,
        changed_chapter_count=len(changed_ids),
        summary=summary,
        requested_ai_model=ai_model,
        resolved_ai_model=generated.response.resolved_model,
        generation=generation,
    )
