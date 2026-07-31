from __future__ import annotations

from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
import json
import logging
import re
import time

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow_naive
from app.models.memoir import (
    MemoirBookOutput,
    MemoirBookOutputVersion,
    MemoirBookReview,
)
from app.domain.memoir.types import (
    MemoirBookRevisionProposalPhase,
    MemoirBookRevisionProposalStatus,
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
    book_editorial_actions,
    book_output_versions,
    book_review_answer_state,
    book_revision_proposals,
    book_reviews,
)
from app.services.memoir.book_output_snapshots import sectioned_chapter_content
from app.services.memoir.narrative.book_output_assembly import assemble_book_output
from app.services.memoir.semantic_state import semantic_json_signature


ANSWER_ENGINE = "llm_book_review_answer_application_v3"
EDITORIAL_ENGINE = "llm_book_editorial_revision_v1"
FEEDBACK_ENGINE = "llm_book_revision_feedback_v1"
PLAN_ENGINE = "llm_book_revision_plan_v1"
PLAN_FEEDBACK_ENGINE = "llm_book_revision_plan_feedback_v1"
PLAN_DRAFT_ENGINE = "llm_book_revision_plan_draft_v1"
ANSWER_SYSTEM_PROMPT = (
    "你是一位克制的中文回忆录编辑。"
    "你只根据用户明确写下的回答调整已有书稿，不推测、不补造人生事实，"
    "并保持没有关联的章节原样不动。"
)
EDITORIAL_SYSTEM_PROMPT = (
    "你是一位克制的中文回忆录编辑。你只执行用户选中的去重复或补衔接修改，不新增、推测或改变人生事实。"
)
FEEDBACK_SYSTEM_PROMPT = (
    "你是一位克制的中文回忆录编辑。你根据用户最新反馈调整尚未确认的书稿修改预览，不推测、不补造人生事实。"
)
PLAN_SYSTEM_PROMPT = (
    "你是一位克制的中文回忆录编辑。你先制定清楚、可供用户确认的修改思路，不直接改写正文，不推测、不补造人生事实。"
)
PLAN_DRAFT_SYSTEM_PROMPT = (
    "你是一位克制的中文回忆录编辑。"
    "你只按照用户已经确认的修改思路调整现有书稿，"
    "不推测、不补造人生事实，并保持计划未涉及的内容原样不动。"
)
ProgressCallback = Callable[[dict[str, object]], Awaitable[None]]
logger = logging.getLogger(__name__)


class _SectionRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_id: str
    content: str


class _RevisionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_revisions: list[_SectionRevision] = Field(default_factory=list)


class _RevisionPlanItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    reason: str
    approach: str
    targets: list[str] = Field(default_factory=list)


class _RevisionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    items: list[_RevisionPlanItem] = Field(default_factory=list)


@dataclass(frozen=True)
class BookRevisionResult:
    proposal_id: str | None
    changed_chapter_count: int
    selected_action_count: int
    summary: str
    requested_ai_model: str | None
    resolved_ai_model: str | None
    generation: dict[str, object]
    proposal_phase: str | None = None


@dataclass(frozen=True)
class _GeneratedRevision:
    snapshots: list[dict[str, object]]
    changed_chapter_ids: set[str]
    resolved_ai_model: str | None
    generation: dict[str, object]


@dataclass(frozen=True)
class _GeneratedPlan:
    plan: _RevisionPlan
    resolved_ai_model: str | None
    generation: dict[str, object]


class BookRevisionError(ValueError):
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


def _snapshots(value: object) -> list[dict[str, object]]:
    return [deepcopy(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _sections(snapshot: dict[str, object]) -> list[dict[str, object]]:
    value = snapshot.get("sections")
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _reference_maps(
    snapshots: list[dict[str, object]],
    allowed_chapter_ids: set[str],
    *,
    stale_message: str,
) -> tuple[ModelReferenceMap, ModelReferenceMap]:
    ordered_chapter_ids = [
        chapter_id for snapshot in snapshots if (chapter_id := _text(snapshot.get("chapter_id"))) in allowed_chapter_ids
    ]
    if set(ordered_chapter_ids) != allowed_chapter_ids:
        raise BookRevisionError(stale_message)
    section_ids = [
        section_id
        for snapshot in snapshots
        if _text(snapshot.get("chapter_id")) in allowed_chapter_ids
        for section in _sections(snapshot)
        if (section_id := _text(section.get("section_id")))
    ]
    return (
        ModelReferenceMap("C", ordered_chapter_ids),
        ModelReferenceMap("S", section_ids),
    )


def _apply_section_revisions(
    *,
    snapshots: list[dict[str, object]],
    generated: _RevisionOutput,
    section_refs: ModelReferenceMap,
    allowed_chapter_ids: set[str],
) -> tuple[list[dict[str, object]], set[str]]:
    revised_snapshots = deepcopy(snapshots)
    sections_by_id: dict[str, tuple[str, dict[str, object]]] = {}
    for snapshot in revised_snapshots:
        chapter_id = _text(snapshot.get("chapter_id"))
        for section in _sections(snapshot):
            section_id = _text(section.get("section_id"))
            if section_id:
                sections_by_id[section_id] = (chapter_id, section)

    changed_chapter_ids: set[str] = set()
    revised_section_ids: set[str] = set()
    for revision in generated.section_revisions:
        section_id = section_refs.decode(revision.section_id)
        if section_id in revised_section_ids:
            raise ValueError("模型重复修改了同一小节")
        revised_section_ids.add(section_id)
        owner = sections_by_id.get(section_id)
        if owner is None or owner[0] not in allowed_chapter_ids:
            raise ValueError("模型试图修改未选择的章节")
        content = revision.content.strip()
        if not content:
            raise ValueError("模型返回了空的小节正文")
        chapter_id, section = owner
        if content != _text(section.get("content")):
            section["content"] = content
            section["content_char_count"] = len(content)
            changed_chapter_ids.add(chapter_id)

    for snapshot in revised_snapshots:
        if _text(snapshot.get("chapter_id")) not in changed_chapter_ids:
            continue
        sections = _sections(snapshot)
        snapshot["sections"] = sections
        snapshot["section_count"] = len(sections)
        content = sectioned_chapter_content(sections)
        snapshot["content"] = content
        snapshot["content_char_count"] = len(content)
    return revised_snapshots, changed_chapter_ids


async def _generate_revisions(
    *,
    project_id: str,
    snapshots: list[dict[str, object]],
    allowed_chapter_ids: set[str],
    section_refs: ModelReferenceMap,
    system_prompt: str,
    prompt: str,
    engine: str,
    error_message: str,
    ai_model: str | None,
    ai_client: AITextClient | None,
) -> _GeneratedRevision:
    client = ai_client or get_ai_service(ai_model)
    policy = ai_text_workload_policy(AITextWorkload.LONG_GENERATION_STRUCTURED)
    timeout_seconds = ai_text_timeout_seconds(policy.timeout_profile)
    started_at = time.monotonic()
    try:
        generated = await generate_structured_with_client(
            client,
            system_prompt,
            prompt,
            output_model=_RevisionOutput,
            requested_model=ai_model,
            temperature=0.2,
            max_tokens=policy.max_tokens,
            timeout=timeout_seconds,
            max_attempts=1,
        )
        revised_snapshots, changed_chapter_ids = _apply_section_revisions(
            snapshots=snapshots,
            generated=generated.value,
            section_refs=section_refs,
            allowed_chapter_ids=allowed_chapter_ids,
        )
        generation = {
            **generated.response.diagnostics(),
            "engine": engine,
            "timeout_seconds": timeout_seconds,
        }
        return _GeneratedRevision(
            snapshots=revised_snapshots,
            changed_chapter_ids=changed_chapter_ids,
            resolved_ai_model=generated.response.resolved_model,
            generation=generation,
        )
    except Exception as exc:
        logger.exception("%s failed project_id=%s", engine, project_id)
        generation = {
            **ai_error_metadata(
                exc,
                requested_model=ai_model,
                provider=getattr(client, "provider_name", None),
                resolved_model=getattr(client, "model_name", ai_model),
                timeout_seconds=timeout_seconds,
                elapsed_ms=round((time.monotonic() - started_at) * 1000),
            ),
            "engine": engine,
        }
        raise BookRevisionError(
            ai_user_error_message(exc, error_message),
            generation=generation,
        ) from exc


async def _generate_revision_plan(
    *,
    project_id: str,
    prompt: str,
    engine: str,
    error_message: str,
    ai_model: str | None,
    ai_client: AITextClient | None,
) -> _GeneratedPlan:
    client = ai_client or get_ai_service(ai_model)
    policy = ai_text_workload_policy(AITextWorkload.INTERACTIVE_STRUCTURED)
    timeout_seconds = ai_text_timeout_seconds(policy.timeout_profile)
    started_at = time.monotonic()
    try:
        generated = await generate_structured_with_client(
            client,
            PLAN_SYSTEM_PROMPT,
            prompt,
            output_model=_RevisionPlan,
            requested_model=ai_model,
            temperature=0.2,
            max_tokens=policy.max_tokens,
            timeout=timeout_seconds,
            max_attempts=1,
        )
        generation = {
            **generated.response.diagnostics(),
            "engine": engine,
            "timeout_seconds": timeout_seconds,
        }
        return _GeneratedPlan(
            plan=generated.value,
            resolved_ai_model=generated.response.resolved_model,
            generation=generation,
        )
    except Exception as exc:
        logger.exception("%s failed project_id=%s", engine, project_id)
        generation = {
            **ai_error_metadata(
                exc,
                requested_model=ai_model,
                provider=getattr(client, "provider_name", None),
                resolved_model=getattr(client, "model_name", ai_model),
                timeout_seconds=timeout_seconds,
                elapsed_ms=round((time.monotonic() - started_at) * 1000),
            ),
            "engine": engine,
        }
        raise BookRevisionError(
            ai_user_error_message(exc, error_message),
            generation=generation,
        ) from exc


async def _load_baseline(
    db: AsyncSession,
    *,
    project_id: str,
    missing_message: str,
) -> tuple[MemoirBookOutput, MemoirBookOutputVersion]:
    assembled = await assemble_book_output(
        db,
        project_id=project_id,
        allow_unapplied_review_answers=True,
    )
    output = await db.scalar(select(MemoirBookOutput).where(MemoirBookOutput.project_id == project_id))
    baseline = await db.get(MemoirBookOutputVersion, assembled.output_version_id)
    if output is None or baseline is None:
        raise BookRevisionError(missing_message)
    return output, baseline


async def _reload_baseline(
    db: AsyncSession,
    *,
    project_id: str,
    output_id: str,
    baseline_id: str,
    expected_review_input_signature: str,
    require_ready_review: bool,
    stale_message: str,
) -> tuple[MemoirBookReview, MemoirBookOutputVersion]:
    db.expire_all()
    review = await book_reviews.get_current_book_review(db, project_id=project_id)
    latest = await book_output_versions.load_latest_book_output_version(
        db,
        output_id=output_id,
    )
    if (
        review is None
        or (require_ready_review and review.status != "ready")
        or review.input_signature != expected_review_input_signature
        or latest is None
        or latest.id != baseline_id
    ):
        raise BookRevisionError(stale_message)
    baseline = await db.get(MemoirBookOutputVersion, baseline_id)
    if baseline is None:
        raise BookRevisionError(stale_message)
    return review, baseline


_INTERNAL_MODEL_REFERENCE_PATTERN = re.compile(r"[CS]\d+")


def user_visible_application_summary(
    summary: object,
    *,
    changed_chapter_count: object,
) -> str:
    value = " ".join(summary.split())[:400] if isinstance(summary, str) else ""
    if not value or not _INTERNAL_MODEL_REFERENCE_PATTERN.search(value):
        return value
    return (
        "修改预览已准备，包含根据回答生成的相关章节调整。"
        if isinstance(changed_chapter_count, int) and changed_chapter_count > 0
        else "已核对这些回答，现有正文无需调整。"
    )


def _answer_summary(
    snapshots: list[dict[str, object]],
    changed_ids: set[str],
) -> str:
    if not changed_ids:
        return "已核对这些回答，现有正文无需调整。"
    titles = [
        title
        for snapshot in snapshots
        if _text(snapshot.get("chapter_id")) in changed_ids and (title := _text(snapshot.get("chapter_title")))
    ]
    if not titles:
        return "修改预览已准备，包含相关章节的正文调整。"
    target = "、".join(f"《{title}》" for title in titles[:3])
    if len(titles) > 3:
        target = f"{target}等 {len(titles)} 个章节"
    return f"修改预览已准备，包含{target}的相关正文调整。"


def _chapter_source_text(
    *,
    snapshots: list[dict[str, object]],
    chapter_refs: ModelReferenceMap,
    section_refs: ModelReferenceMap,
    allowed_chapter_ids: set[str] | None = None,
) -> str:
    chapters: list[str] = []
    for snapshot in snapshots:
        chapter_id = _text(snapshot.get("chapter_id"))
        if not chapter_id or (allowed_chapter_ids is not None and chapter_id not in allowed_chapter_ids):
            continue
        sections = "\n\n".join(
            f"section_id: {section_refs.encode(_text(section.get('section_id')))}\n"
            f"小节名: {_text(section.get('title'))}\n"
            f"正文:\n{_text(section.get('content'))}"
            for section in _sections(snapshot)
            if _text(section.get("section_id"))
        )
        chapters.append(
            f"chapter_id: {chapter_refs.encode(chapter_id)}\n章节名: {_text(snapshot.get('chapter_title'))}\n{sections}"
        )
    return "\n\n".join(chapters)


def _plan_output_instruction() -> str:
    return """请先制定修改思路，不要输出修改后的正文。

请输出严格 JSON：
{
  "summary": "对整轮修改方向的简明概括",
  "items": [
    {
      "title": "本项修改目标",
      "reason": "为什么需要这样调整",
      "approach": "具体准备怎样调整，但不写修改后的正文",
      "targets": ["用户能看懂的章节名或小节名"]
    }
  ]
}

要求：
- 输出的是用户确认前的整体修改思路，不是修改后的书稿。
- 每一项都要说清楚修改目标、原因和做法。
- targets 使用章节名或小节名，不显示内部编号。
- 只根据输入原文、检查结论和用户明确表达的决定制定计划。
- 不新增、推测或改变人生事实。
- 不需要修改时，items 返回空数组，并在 summary 里说明原因。
"""


def _answer_plan_prompt(
    *,
    snapshots: list[dict[str, object]],
    context: book_review_answer_state.BookReviewAnswerContext,
    chapter_refs: ModelReferenceMap,
    section_refs: ModelReferenceMap,
) -> str:
    answers = "\n\n".join(
        f"问题 {index}：{answer.question}\n"
        f"问题原因：{answer.reason}\n"
        f"用户回答：{answer.answer}\n"
        "关联章节：" + "、".join(chapter_refs.encode(value) for value in answer.chapter_ids)
        for index, answer in enumerate(context.answers, start=1)
    )
    chapters = _chapter_source_text(
        snapshots=snapshots,
        chapter_refs=chapter_refs,
        section_refs=section_refs,
        allowed_chapter_ids=context.related_chapter_ids,
    )
    return f"""请根据全书检查问题和用户回答，制定书稿修改思路。

用户回答：
{answers}

关联章节原文：
{chapters}

{_plan_output_instruction()}"""


def _answer_prompt(
    *,
    snapshots: list[dict[str, object]],
    context: book_review_answer_state.BookReviewAnswerContext,
    chapter_refs: ModelReferenceMap,
    section_refs: ModelReferenceMap,
) -> str:
    chapters = _chapter_source_text(
        snapshots=snapshots,
        chapter_refs=chapter_refs,
        section_refs=section_refs,
        allowed_chapter_ids=context.related_chapter_ids,
    )
    answers = "\n\n".join(
        f"问题 {index}：{answer.question}\n"
        f"问题原因：{answer.reason}\n"
        f"用户回答：{answer.answer}\n"
        "关联章节：" + "、".join(chapter_refs.encode(value) for value in answer.chapter_ids)
        for index, answer in enumerate(context.answers, start=1)
    )
    return f"""请把用户对全书检查问题的回答应用到现有书稿。

用户回答：
{answers}

允许修改的关联章节原文：
{chapters}

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


async def apply_book_review_answers(
    db: AsyncSession,
    *,
    project_id: str,
    expected_review_input_signature: str,
    expected_answers_signature: str,
    ai_model: str | None,
    workflow_mode: str = "direct_draft",
    progress_callback: ProgressCallback | None = None,
    ai_client: AITextClient | None = None,
) -> BookRevisionResult:
    review = await book_reviews.get_current_book_review(db, project_id=project_id)
    if review is None or review.input_signature != expected_review_input_signature:
        raise BookRevisionError("全书内容已经变化，请重新检查后再应用回答")
    context = await book_review_answer_state.load_answer_context(db, review=review)
    if context.unresolved_count:
        raise BookRevisionError("请先处理完所有需要确认的问题")
    if not context.answers:
        raise BookRevisionError("当前没有可以应用到书稿的回答")
    if context.answers_signature != expected_answers_signature:
        raise BookRevisionError("问题回答已经变化，请重新提交")

    if progress_callback is not None:
        await progress_callback({"stage": "assembling"})
    output, baseline = await _load_baseline(
        db,
        project_id=project_id,
        missing_message="书稿还没有生成",
    )
    if book_review_answer_state.marker_matches_context(
        output=output,
        version=baseline,
        context=context,
    ):
        marker = book_review_answer_state.application_marker(output)
        changed_count = int(marker.get("changed_chapter_count") or 0)
        return BookRevisionResult(
            proposal_id=_text(marker.get("proposal_id")) or None,
            changed_chapter_count=changed_count,
            selected_action_count=0,
            summary=(
                user_visible_application_summary(
                    marker.get("summary"),
                    changed_chapter_count=changed_count,
                )
                or "这些回答已经应用到书稿"
            ),
            requested_ai_model=ai_model,
            resolved_ai_model=_text(marker.get("resolved_ai_model")) or ai_model,
            generation={},
        )

    snapshots = _snapshots(baseline.chapter_snapshot_json)
    chapter_refs, section_refs = _reference_maps(
        snapshots,
        context.related_chapter_ids,
        stale_message="问题关联的章节已经变化，请重新检查全书",
    )
    if not context.related_chapter_ids:
        raise BookRevisionError("问题没有关联到可调整的书稿章节")
    if progress_callback is not None:
        await progress_callback({"stage": "revising"})
    plan_first = workflow_mode == "plan_first"
    generated_plan = (
        await _generate_revision_plan(
            project_id=project_id,
            prompt=_answer_plan_prompt(
                snapshots=snapshots,
                context=context,
                chapter_refs=chapter_refs,
                section_refs=section_refs,
            ),
            engine=PLAN_ENGINE,
            error_message="生成修改思路时遇到问题，请稍后重试",
            ai_model=ai_model,
            ai_client=ai_client,
        )
        if plan_first
        else None
    )
    generated_revision = (
        None
        if plan_first
        else await _generate_revisions(
            project_id=project_id,
            snapshots=snapshots,
            allowed_chapter_ids=context.related_chapter_ids,
            section_refs=section_refs,
            system_prompt=ANSWER_SYSTEM_PROMPT,
            prompt=_answer_prompt(
                snapshots=snapshots,
                context=context,
                chapter_refs=chapter_refs,
                section_refs=section_refs,
            ),
            engine=ANSWER_ENGINE,
            error_message="根据回答调整书稿时遇到问题，请稍后重试",
            ai_model=ai_model,
            ai_client=ai_client,
        )
    )
    summary = (
        generated_plan.plan.summary.strip()
        if generated_plan is not None
        else _answer_summary(
            generated_revision.snapshots,
            generated_revision.changed_chapter_ids,
        )
    )
    generation = generated_plan.generation if generated_plan is not None else generated_revision.generation

    if progress_callback is not None:
        await progress_callback({"stage": "saving"})
    current_review, baseline = await _reload_baseline(
        db,
        project_id=project_id,
        output_id=output.id,
        baseline_id=baseline.id,
        expected_review_input_signature=expected_review_input_signature,
        require_ready_review=False,
        stale_message="调整期间全书或回答已变化，请重试",
    )
    current_context = await book_review_answer_state.load_answer_context(
        db,
        review=current_review,
    )
    if current_context.answers_signature != expected_answers_signature:
        raise BookRevisionError("调整期间全书或回答已变化，请重试")
    proposal = await book_revision_proposals.create_ready_proposal(
        db,
        review=current_review,
        baseline=baseline,
        candidate_snapshots=([] if generated_revision is None else generated_revision.snapshots),
        selected_actions={
            "source": "book_review_answers",
            "answers_signature": current_context.answers_signature,
            "issue_ids": [answer.issue_id for answer in current_context.answers],
        },
        generation=generation,
        summary=summary,
        phase=(
            MemoirBookRevisionProposalPhase.PLAN_READY
            if generated_plan is not None
            else MemoirBookRevisionProposalPhase.DRAFT_READY
        ),
        revision_plan=(generated_plan.plan.model_dump(mode="json") if generated_plan is not None else None),
    )
    return BookRevisionResult(
        proposal_id=proposal.id,
        changed_chapter_count=(0 if generated_revision is None else len(generated_revision.changed_chapter_ids)),
        selected_action_count=0,
        summary=summary,
        requested_ai_model=ai_model,
        resolved_ai_model=(
            generated_plan.resolved_ai_model if generated_plan is not None else generated_revision.resolved_ai_model
        ),
        generation=generation,
        proposal_phase=proposal.phase,
    )


def _book_outline(snapshots: list[dict[str, object]]) -> str:
    return "\n".join(
        f"- {_text(chapter.get('chapter_title'))}："
        + "、".join(_text(section.get("title")) for section in _sections(chapter) if _text(section.get("title")))
        for chapter in snapshots
        if _text(chapter.get("chapter_title"))
    )


def _plan_feedback_prompt(
    *,
    plan: _RevisionPlan,
    feedback: str,
    snapshots: list[dict[str, object]],
) -> str:
    return f"""请根据用户反馈更新完整修改思路。

当前修改思路：
{json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, indent=2)}

用户最新意见：
{feedback}

当前书稿目录：
{_book_outline(snapshots)}

请吸收用户意见后返回一份完整的新思路，不要只返回增量说明。
{_plan_output_instruction()}"""


def _plan_draft_prompt(
    *,
    plan: _RevisionPlan,
    snapshots: list[dict[str, object]],
    chapter_refs: ModelReferenceMap,
    section_refs: ModelReferenceMap,
) -> str:
    chapters = _chapter_source_text(
        snapshots=snapshots,
        chapter_refs=chapter_refs,
        section_refs=section_refs,
    )
    return f"""请按照已确认的修改思路生成书稿修改稿。

已确认的完整修改思路：
{json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, indent=2)}

当前书稿：
{chapters}

请输出严格 JSON：
{{
  "section_revisions": [
    {{
      "section_id": "需要修改的小节短编号",
      "content": "修改后的完整小节正文"
    }}
  ]
}}

要求：
- 只执行已确认修改思路中明确列出的调整。
- section_id 只能使用输入中的短编号。
- 修改小节时返回修改后的完整正文；不需要修改的小节不要返回。
- 不改变章节和小节的标题、数量、收录状态及归属关系。
- 只使用当前书稿和修改思路中已有的事实，不得推测或补造。
- 保留原文叙述视角和语气，计划未涉及的内容保持原样。
"""


async def _refine_book_revision_plan(
    db: AsyncSession,
    *,
    project_id: str,
    proposal_id: str,
    feedback: str,
    expected_plan_signature: str,
    ai_model: str | None,
    progress_callback: ProgressCallback | None,
    ai_client: AITextClient | None,
) -> BookRevisionResult:
    proposal = await book_revision_proposals.load_proposal(
        db,
        project_id=project_id,
        proposal_id=proposal_id,
    )
    response = await book_revision_proposals.proposal_response(
        db,
        proposal=proposal,
        preview="none",
    )
    plan = _RevisionPlan.model_validate(proposal.revision_plan_json)
    if (
        response.status != MemoirBookRevisionProposalStatus.READY
        or semantic_json_signature(proposal.revision_plan_json) != expected_plan_signature
    ):
        raise BookRevisionError("修改思路已经变化，请重新查看后再提交反馈")
    baseline = await db.get(
        MemoirBookOutputVersion,
        proposal.base_output_version_id,
    )
    if baseline is None:
        raise BookRevisionError("修改前的书稿版本已不可用")
    snapshots = _snapshots(baseline.chapter_snapshot_json)
    if progress_callback is not None:
        await progress_callback({"stage": "revising"})
    generated = await _generate_revision_plan(
        project_id=project_id,
        prompt=_plan_feedback_prompt(
            plan=plan,
            feedback=feedback,
            snapshots=snapshots,
        ),
        engine=PLAN_FEEDBACK_ENGINE,
        error_message="按反馈更新修改思路时遇到问题，请稍后重试",
        ai_model=ai_model,
        ai_client=ai_client,
    )

    if progress_callback is not None:
        await progress_callback({"stage": "saving"})
    db.expire_all()
    current = await book_revision_proposals.load_proposal(
        db,
        project_id=project_id,
        proposal_id=proposal_id,
    )
    current_response = await book_revision_proposals.proposal_response(
        db,
        proposal=current,
        preview="none",
    )
    if (
        current_response.status != MemoirBookRevisionProposalStatus.READY
        or semantic_json_signature(current.revision_plan_json) != expected_plan_signature
    ):
        raise BookRevisionError("调整期间修改思路已经变化，请重新提交反馈")
    current.phase = MemoirBookRevisionProposalPhase.PLAN_READY.value
    current.revision_plan_json = generated.plan.model_dump(mode="json")
    current.candidate_chapter_snapshot_json = []
    current.comparison_json = {}
    current.generation_json = generated.generation
    current.summary = generated.plan.summary.strip()
    current.updated_at = utcnow_naive()
    db.add(current)
    await db.flush()
    return BookRevisionResult(
        proposal_id=current.id,
        changed_chapter_count=0,
        selected_action_count=0,
        summary=current.summary,
        requested_ai_model=ai_model,
        resolved_ai_model=generated.resolved_ai_model,
        generation=generated.generation,
        proposal_phase=current.phase,
    )


async def generate_book_revision_draft(
    db: AsyncSession,
    *,
    project_id: str,
    proposal_id: str,
    expected_plan_signature: str,
    ai_model: str | None,
    progress_callback: ProgressCallback | None = None,
    ai_client: AITextClient | None = None,
) -> BookRevisionResult:
    proposal = await book_revision_proposals.load_proposal(
        db,
        project_id=project_id,
        proposal_id=proposal_id,
    )
    response = await book_revision_proposals.proposal_response(
        db,
        proposal=proposal,
        preview="none",
    )
    plan = _RevisionPlan.model_validate(proposal.revision_plan_json)
    if (
        response.status != MemoirBookRevisionProposalStatus.READY
        or proposal.phase != MemoirBookRevisionProposalPhase.PLAN_READY.value
        or semantic_json_signature(proposal.revision_plan_json) != expected_plan_signature
    ):
        raise BookRevisionError("修改思路已经变化，请重新查看后再生成修改稿")
    baseline = await db.get(
        MemoirBookOutputVersion,
        proposal.base_output_version_id,
    )
    if baseline is None:
        raise BookRevisionError("修改前的书稿版本已不可用")
    snapshots = _snapshots(baseline.chapter_snapshot_json)
    source = proposal.selected_actions_json.get("source")
    if source == book_editorial_actions.BOOK_EDITORIAL_ACTION_SOURCE:
        review = await db.get(MemoirBookReview, proposal.review_id)
        if review is None:
            raise BookRevisionError("全书检查结果已经变化，请重新检查")
        raw_action_ids = proposal.selected_actions_json.get("action_ids")
        actions = book_editorial_actions.selected_review_actions(
            review.review_json,
            ([str(value) for value in raw_action_ids] if isinstance(raw_action_ids, list) else []),
        )
        snapshots = _reorder_chapters(snapshots, actions)

    allowed_chapter_ids = {chapter_id for snapshot in snapshots if (chapter_id := _text(snapshot.get("chapter_id")))}
    chapter_refs, section_refs = _reference_maps(
        snapshots,
        allowed_chapter_ids,
        stale_message="书稿章节已经变化，请重新生成修改思路",
    )
    if progress_callback is not None:
        await progress_callback({"stage": "revising"})
    generated = await _generate_revisions(
        project_id=project_id,
        snapshots=snapshots,
        allowed_chapter_ids=allowed_chapter_ids,
        section_refs=section_refs,
        system_prompt=PLAN_DRAFT_SYSTEM_PROMPT,
        prompt=_plan_draft_prompt(
            plan=plan,
            snapshots=snapshots,
            chapter_refs=chapter_refs,
            section_refs=section_refs,
        ),
        engine=PLAN_DRAFT_ENGINE,
        error_message="按修改思路生成修改稿时遇到问题，请稍后重试",
        ai_model=ai_model,
        ai_client=ai_client,
    )
    comparison = book_revision_proposals.build_comparison(
        before=_snapshots(baseline.chapter_snapshot_json),
        after=generated.snapshots,
    )
    summary = (
        "修改稿已按确认思路生成，涉及 "
        f"{comparison['changed_chapter_count']} 章、"
        f"{comparison['changed_section_count']} 个小节。"
    )

    if progress_callback is not None:
        await progress_callback({"stage": "saving"})
    db.expire_all()
    current = await book_revision_proposals.load_proposal(
        db,
        project_id=project_id,
        proposal_id=proposal_id,
    )
    current_response = await book_revision_proposals.proposal_response(
        db,
        proposal=current,
        preview="none",
    )
    if (
        current_response.status != MemoirBookRevisionProposalStatus.READY
        or current.phase != MemoirBookRevisionProposalPhase.PLAN_READY.value
        or semantic_json_signature(current.revision_plan_json) != expected_plan_signature
    ):
        raise BookRevisionError("生成期间修改思路已经变化，请重新生成修改稿")
    current.phase = MemoirBookRevisionProposalPhase.DRAFT_READY.value
    current.candidate_chapter_snapshot_json = generated.snapshots
    current.comparison_json = {}
    current.generation_json = generated.generation
    current.summary = summary
    current.updated_at = utcnow_naive()
    db.add(current)
    await db.flush()
    return BookRevisionResult(
        proposal_id=current.id,
        changed_chapter_count=int(comparison["changed_chapter_count"]),
        selected_action_count=0,
        summary=summary,
        requested_ai_model=ai_model,
        resolved_ai_model=generated.resolved_ai_model,
        generation=generated.generation,
        proposal_phase=current.phase,
    )


def _feedback_prompt(
    *,
    baseline: list[dict[str, object]],
    candidate: list[dict[str, object]],
    feedback: str,
    chapter_refs: ModelReferenceMap,
    section_refs: ModelReferenceMap,
) -> str:
    baseline_sections = {
        _text(section.get("section_id")): _text(section.get("content"))
        for chapter in baseline
        for section in _sections(chapter)
        if _text(section.get("section_id"))
    }
    chapters: list[str] = []
    for chapter in candidate:
        chapter_id = _text(chapter.get("chapter_id"))
        section_texts: list[str] = []
        for section in _sections(chapter):
            section_id = _text(section.get("section_id"))
            if not section_id:
                continue
            current_content = _text(section.get("content"))
            original_content = baseline_sections.get(section_id, "")
            original = f"\n修改前原文：\n{original_content}" if original_content != current_content else ""
            section_texts.append(
                f"section_id: {section_refs.encode(section_id)}\n"
                f"小节名：{_text(section.get('title'))}"
                f"{original}\n当前预览正文：\n{current_content}"
            )
        chapters.append(
            f"chapter_id: {chapter_refs.encode(chapter_id)}\n"
            f"章节名：{_text(chapter.get('chapter_title'))}\n" + "\n\n".join(section_texts)
        )
    return f"""请根据用户反馈调整当前尚未确认的书稿修改预览。

用户最新反馈：
{feedback}

当前修改预览：
{chr(10).join(chapters)}

请输出严格 JSON：
{{
  "section_revisions": [
    {{
      "section_id": "需要继续调整的小节短编号",
      "content": "调整后的完整小节正文"
    }}
  ]
}}

要求：
- 以用户最新反馈为准，只调整反馈涉及的正文。
- 保留当前预览中未被反馈否定的修改。
- 涉及跨章人物、时间或称谓时，在相关章节中保持一致。
- 只使用修改前原文、当前预览和用户反馈中已有的事实，不得推测或补造。
- 不得改变章节或小节的顺序、标题、数量、收录状态及归属关系。
- section_id 只能使用输入中的短编号；返回修改后的完整小节正文。
- 不需要继续调整的小节不要返回。
"""


async def refine_book_revision_proposal(
    db: AsyncSession,
    *,
    project_id: str,
    proposal_id: str,
    feedback: str,
    expected_candidate_signature: str,
    ai_model: str | None,
    progress_callback: ProgressCallback | None = None,
    ai_client: AITextClient | None = None,
) -> BookRevisionResult:
    proposal = await book_revision_proposals.load_proposal(
        db,
        project_id=project_id,
        proposal_id=proposal_id,
    )
    if proposal.revision_plan_json:
        return await _refine_book_revision_plan(
            db,
            project_id=project_id,
            proposal_id=proposal_id,
            feedback=feedback,
            expected_plan_signature=expected_candidate_signature,
            ai_model=ai_model,
            progress_callback=progress_callback,
            ai_client=ai_client,
        )
    response = await book_revision_proposals.proposal_response(
        db,
        proposal=proposal,
        preview="none",
    )
    candidate = _snapshots(proposal.candidate_chapter_snapshot_json)
    if not response.can_apply or semantic_json_signature(candidate) != expected_candidate_signature:
        raise BookRevisionError("修改预览已经变化，请重新查看后再提交反馈")
    baseline = await db.get(
        MemoirBookOutputVersion,
        proposal.base_output_version_id,
    )
    if baseline is None:
        raise BookRevisionError("修改前的书稿版本已不可用")
    baseline_snapshots = _snapshots(baseline.chapter_snapshot_json)
    allowed_chapter_ids = {chapter_id for snapshot in candidate if (chapter_id := _text(snapshot.get("chapter_id")))}
    chapter_refs, section_refs = _reference_maps(
        candidate,
        allowed_chapter_ids,
        stale_message="当前修改预览已经变化，请重新打开",
    )
    if progress_callback is not None:
        await progress_callback({"stage": "revising"})
    generated = await _generate_revisions(
        project_id=project_id,
        snapshots=candidate,
        allowed_chapter_ids=allowed_chapter_ids,
        section_refs=section_refs,
        system_prompt=FEEDBACK_SYSTEM_PROMPT,
        prompt=_feedback_prompt(
            baseline=baseline_snapshots,
            candidate=candidate,
            feedback=feedback,
            chapter_refs=chapter_refs,
            section_refs=section_refs,
        ),
        engine=FEEDBACK_ENGINE,
        error_message="按反馈调整修改预览时遇到问题，请稍后重试",
        ai_model=ai_model,
        ai_client=ai_client,
    )
    comparison = book_revision_proposals.build_comparison(
        before=baseline_snapshots,
        after=generated.snapshots,
    )
    changed_chapter_count = int(comparison["changed_chapter_count"])
    changed_section_count = int(comparison["changed_section_count"])
    summary = (
        f"已按反馈更新修改预览，当前涉及 {changed_chapter_count} 章、{changed_section_count} 个小节。"
        if changed_chapter_count
        else "已核对反馈，当前预览无需修改正文。"
    )

    if progress_callback is not None:
        await progress_callback({"stage": "saving"})
    db.expire_all()
    current = await book_revision_proposals.load_proposal(
        db,
        project_id=project_id,
        proposal_id=proposal_id,
    )
    current_response = await book_revision_proposals.proposal_response(
        db,
        proposal=current,
        preview="none",
    )
    if (
        not current_response.can_apply
        or semantic_json_signature(_snapshots(current.candidate_chapter_snapshot_json)) != expected_candidate_signature
    ):
        raise BookRevisionError("调整期间修改预览已经变化，请重新提交反馈")
    current.candidate_chapter_snapshot_json = generated.snapshots
    current.comparison_json = {}
    current.generation_json = generated.generation
    current.summary = summary
    current.updated_at = utcnow_naive()
    db.add(current)
    await db.flush()
    return BookRevisionResult(
        proposal_id=current.id,
        changed_chapter_count=changed_chapter_count,
        selected_action_count=0,
        summary=summary,
        requested_ai_model=ai_model,
        resolved_ai_model=generated.resolved_ai_model,
        generation=generated.generation,
    )


def _reorder_chapters(
    snapshots: list[dict[str, object]],
    actions: list[dict[str, object]],
) -> list[dict[str, object]]:
    result = deepcopy(snapshots)
    for action in actions:
        if action.get("action_type") != "reorder_chapters":
            continue
        raw_ids = action.get("chapter_ids")
        desired = [str(value) for value in raw_ids] if isinstance(raw_ids, list) else []
        by_id = {_text(snapshot.get("chapter_id")): snapshot for snapshot in result}
        positions = [index for index, snapshot in enumerate(result) if _text(snapshot.get("chapter_id")) in desired]
        if len(positions) != len(desired) or any(value not in by_id for value in desired):
            raise BookRevisionError("调整顺序涉及的章节已经变化，请重新检查全书")
        for position, chapter_id in zip(positions, desired):
            result[position] = by_id[chapter_id]
    for index, snapshot in enumerate(result, start=1):
        snapshot["chapter_index"] = index
    return result


def _editorial_prompt(
    *,
    snapshots: list[dict[str, object]],
    actions: list[dict[str, object]],
    chapter_refs: ModelReferenceMap,
    section_refs: ModelReferenceMap,
) -> str:
    action_text = "\n\n".join(
        f"类型：{action.get('action_type')}\n"
        f"修改目标：{_text(action.get('title'))}\n"
        f"原因：{_text(action.get('reason'))}\n"
        "涉及章节：" + "、".join(chapter_refs.encode(str(chapter_id)) for chapter_id in action.get("chapter_ids", []))
        for action in actions
    )
    allowed_ids = {str(chapter_id) for action in actions for chapter_id in action.get("chapter_ids", [])}
    chapters: list[str] = []
    for snapshot in snapshots:
        chapter_id = _text(snapshot.get("chapter_id"))
        if chapter_id not in allowed_ids:
            continue
        sections = "\n\n".join(
            f"section_id: {section_refs.encode(_text(section.get('section_id')))}\n"
            f"小节名：{_text(section.get('title'))}\n"
            f"正文：\n{_text(section.get('content'))}"
            for section in _sections(snapshot)
            if _text(section.get("section_id"))
        )
        chapters.append(
            f"chapter_id: {chapter_refs.encode(chapter_id)}\n章节名：{_text(snapshot.get('chapter_title'))}\n{sections}"
        )
    return f"""请执行用户选中的全书编辑修改。

选中的修改：
{action_text}

允许修改的章节正文：
{chr(10).join(chapters)}

请输出严格 JSON：
{{
  "section_revisions": [
    {{
      "section_id": "需要修改的小节短编号",
      "content": "修改后的完整小节正文"
    }}
  ]
}}

要求：
- remove_repetition 只删除或压缩跨章重复表达，重要事实至少保留一次。
- improve_transition 只调整承接语句，让已有时间、人物或主题关系更清楚。
- 不新增事实、人物、时间、地点、评价或因果。
- 不改变章节和小节的标题、数量、归属及顺序。
- section_id 只能使用输入中的短编号；返回修改后的完整小节正文。
- 不需要修改的小节不要返回。
"""


def _editorial_summary(actions: list[dict[str, object]]) -> str:
    labels = {
        "remove_repetition": "去除重复内容",
        "improve_transition": "改善章节衔接",
        "reorder_chapters": "调整章节顺序",
    }
    selected = list(
        dict.fromkeys(
            labels[str(action.get("action_type"))] for action in actions if str(action.get("action_type")) in labels
        )
    )
    return f"修改预览已准备，包含{'、'.join(selected)}。"


def _editorial_plan_prompt(
    *,
    snapshots: list[dict[str, object]],
    actions: list[dict[str, object]],
    chapter_refs: ModelReferenceMap,
    section_refs: ModelReferenceMap,
) -> str:
    action_text = "\n\n".join(
        f"修改目标：{_text(action.get('title'))}\n"
        f"原因：{_text(action.get('reason'))}\n"
        "涉及章节：" + "、".join(chapter_refs.encode(str(chapter_id)) for chapter_id in action.get("chapter_ids", []))
        for action in actions
    )
    related_ids = {str(chapter_id) for action in actions for chapter_id in action.get("chapter_ids", [])}
    chapters = _chapter_source_text(
        snapshots=snapshots,
        chapter_refs=chapter_refs,
        section_refs=section_refs,
        allowed_chapter_ids=related_ids,
    )
    return f"""请根据用户选中的全书编辑建议，制定书稿修改思路。

用户选中的建议：
{action_text}

相关章节原文：
{chapters}

{_plan_output_instruction()}"""


async def generate_editorial_revision_proposal(
    db: AsyncSession,
    *,
    project_id: str,
    expected_review_input_signature: str,
    expected_actions_signature: str,
    action_ids: list[str],
    ai_model: str | None,
    workflow_mode: str = "direct_draft",
    progress_callback: ProgressCallback | None = None,
    ai_client: AITextClient | None = None,
) -> BookRevisionResult:
    review = await book_reviews.get_current_book_review(db, project_id=project_id)
    if review is None or review.status != "ready" or review.input_signature != expected_review_input_signature:
        raise BookRevisionError("全书检查结果已经变化，请重新检查")
    actions = book_editorial_actions.selected_review_actions(
        review.review_json,
        action_ids,
    )
    if book_editorial_actions.actions_signature(actions) != expected_actions_signature:
        raise BookRevisionError("所选编辑建议已经变化，请重新选择")

    if progress_callback is not None:
        await progress_callback({"stage": "assembling"})
    output, baseline = await _load_baseline(
        db,
        project_id=project_id,
        missing_message="当前书稿还没有准备好",
    )
    answer_context = await book_review_answer_state.load_answer_context(
        db,
        review=review,
    )
    answers_applied = book_review_answer_state.marker_matches_context(
        output=output,
        version=baseline,
        context=answer_context,
    ) or book_editorial_actions.marker_matches_review(
        output=output,
        version=baseline,
        review=review,
        answers_signature=answer_context.answers_signature,
    )
    if answer_context.answers and not answers_applied:
        raise BookRevisionError("请先确认根据问题回答生成的修改预览")

    original_snapshots = _snapshots(baseline.chapter_snapshot_json)
    plan_first = workflow_mode == "plan_first"
    revised_snapshots = original_snapshots if plan_first else _reorder_chapters(original_snapshots, actions)
    content_actions = [
        action for action in actions if action.get("action_type") in {"remove_repetition", "improve_transition"}
    ]
    generation: dict[str, object] = {"engine": EDITORIAL_ENGINE}
    resolved_ai_model: str | None = None
    generated_plan: _GeneratedPlan | None = None
    if plan_first:
        related_ids = {str(chapter_id) for action in actions for chapter_id in action.get("chapter_ids", [])}
        chapter_refs, section_refs = _reference_maps(
            original_snapshots,
            related_ids,
            stale_message="编辑建议涉及的章节已经变化，请重新检查全书",
        )
        if progress_callback is not None:
            await progress_callback({"stage": "revising"})
        generated_plan = await _generate_revision_plan(
            project_id=project_id,
            prompt=_editorial_plan_prompt(
                snapshots=original_snapshots,
                actions=actions,
                chapter_refs=chapter_refs,
                section_refs=section_refs,
            ),
            engine=PLAN_ENGINE,
            error_message="生成修改思路时遇到问题，请稍后重试",
            ai_model=ai_model,
            ai_client=ai_client,
        )
        generation = generated_plan.generation
        resolved_ai_model = generated_plan.resolved_ai_model
    elif content_actions:
        related_ids = {str(chapter_id) for action in content_actions for chapter_id in action.get("chapter_ids", [])}
        chapter_refs, section_refs = _reference_maps(
            revised_snapshots,
            related_ids,
            stale_message="编辑建议涉及的章节已经变化，请重新检查全书",
        )
        if progress_callback is not None:
            await progress_callback({"stage": "revising"})
        generated = await _generate_revisions(
            project_id=project_id,
            snapshots=revised_snapshots,
            allowed_chapter_ids=related_ids,
            section_refs=section_refs,
            system_prompt=EDITORIAL_SYSTEM_PROMPT,
            prompt=_editorial_prompt(
                snapshots=revised_snapshots,
                actions=content_actions,
                chapter_refs=chapter_refs,
                section_refs=section_refs,
            ),
            engine=EDITORIAL_ENGINE,
            error_message="生成全书修改预览时遇到问题，请稍后重试",
            ai_model=ai_model,
            ai_client=ai_client,
        )
        revised_snapshots = generated.snapshots
        generation = generated.generation
        resolved_ai_model = generated.resolved_ai_model

    if progress_callback is not None:
        await progress_callback({"stage": "saving"})
    current_review, baseline = await _reload_baseline(
        db,
        project_id=project_id,
        output_id=output.id,
        baseline_id=baseline.id,
        expected_review_input_signature=expected_review_input_signature,
        require_ready_review=True,
        stale_message="生成期间全书内容已经变化，请重试",
    )
    current_actions = book_editorial_actions.selected_review_actions(
        current_review.review_json,
        action_ids,
    )
    current_answers = await book_review_answer_state.load_answer_context(
        db,
        review=current_review,
    )
    if (
        book_editorial_actions.actions_signature(current_actions) != expected_actions_signature
        or current_answers.answers_signature != answer_context.answers_signature
    ):
        raise BookRevisionError("生成期间编辑建议或问题回答已经变化，请重试")
    proposal = await book_revision_proposals.create_ready_proposal(
        db,
        review=current_review,
        baseline=baseline,
        candidate_snapshots=[] if generated_plan is not None else revised_snapshots,
        selected_actions={
            "source": book_editorial_actions.BOOK_EDITORIAL_ACTION_SOURCE,
            "action_ids": action_ids,
            "actions_signature": expected_actions_signature,
            "answers_signature": current_answers.answers_signature,
        },
        generation=generation,
        summary=(generated_plan.plan.summary.strip() if generated_plan is not None else _editorial_summary(actions)),
        phase=(
            MemoirBookRevisionProposalPhase.PLAN_READY
            if generated_plan is not None
            else MemoirBookRevisionProposalPhase.DRAFT_READY
        ),
        revision_plan=(generated_plan.plan.model_dump(mode="json") if generated_plan is not None else None),
    )
    summary = generated_plan.plan.summary.strip() if generated_plan is not None else _editorial_summary(actions)
    return BookRevisionResult(
        proposal_id=proposal.id,
        changed_chapter_count=0,
        selected_action_count=len(actions),
        summary=summary,
        requested_ai_model=ai_model,
        resolved_ai_model=resolved_ai_model,
        generation=generation,
        proposal_phase=proposal.phase,
    )
