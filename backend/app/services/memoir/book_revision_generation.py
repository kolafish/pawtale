from __future__ import annotations

from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
import json
import logging
import time

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow_naive
from app.domain.memoir.types import (
    MemoirBookRevisionProposalPhase,
    MemoirBookRevisionProposalStatus,
    MemoirCompletionIssueChannel,
    MemoirCompletionIssueScope,
    MemoirCompletionIssueStatus,
)
from app.models.memoir import (
    MemoirBookOutput,
    MemoirBookOutputVersion,
    MemoirBookReview,
    MemoirBookRevisionProposal,
    MemoirCompletionIssue,
)
from app.schemas.memoir_book_review import MemoirBookRevisionPlan
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
    completion_issues,
)
from app.services.memoir.book_output_snapshots import sectioned_chapter_content
from app.services.memoir.book_output_manuscript import (
    book_information_text,
    stored_manuscript_metadata,
)
from app.services.memoir.narrative.book_output_assembly import assemble_book_output
from app.services.memoir.material_roles import ANSWER_CONTEXT_GUIDANCE
from app.services.memoir.prompt_budget import checked_review_prompt
from app.schemas.memoir_section_digests import MemoirBodyDigest
from app.services.memoir.section_digests import digest_snapshot, BODY_DIGEST_INSTRUCTION


PLAN_ENGINE = "llm_book_revision_plan_v1"
PLAN_FEEDBACK_ENGINE = "llm_book_revision_plan_feedback_v1"
PLAN_DRAFT_ENGINE = "llm_book_revision_plan_draft_v1"
PLAN_SYSTEM_PROMPT = (
    "你是一位克制的中文回忆录编辑。"
    "你先判断是否需要修改，确有必要时再给出可供用户确认的修改思路；"
    "不直接改写正文，不推测、不补造人生事实。"
)
PLAN_DRAFT_SYSTEM_PROMPT = (
    "你是一位克制的中文回忆录编辑。"
    "你只按照用户已经确认的修改思路调整现有书稿，"
    "不推测、不补造人生事实，并保持计划未涉及的内容原样不动。"
)
ProgressCallback = Callable[[dict[str, object]], Awaitable[None]]
logger = logging.getLogger(__name__)
_BOOK_METADATA_LABELS = {
    "title": "书名",
    "subtitle": "副标题",
    "narrator_name": "口述人",
    "prepared_by": "整理人",
    "preface": "前言",
    "afterword": "后记",
    "back_cover_quote": "封底文案",
}


class _SectionRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_id: str
    content: str
    body_digest: MemoirBodyDigest


class _RevisionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_revisions: list[_SectionRevision] = Field(default_factory=list)


_RevisionPlan = MemoirBookRevisionPlan


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
class GeneratedRevisionPlan:
    plan: _RevisionPlan
    resolved_ai_model: str | None
    generation: dict[str, object]


class RevisionUserInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    question: str = ""
    chapter_ids: list[str] = Field(default_factory=list)


def _user_input_text(inputs: list[RevisionUserInput], snapshots: list[dict[str, object]]) -> str:
    titles = {_text(item.get("chapter_id")): _text(item.get("chapter_title")) for item in snapshots}
    return "\n\n".join(
        (f"对应提问（只作上下文）：{item.question}\n" if item.question else "")
        + f"用户原话：{item.text}"
        + ("\n涉及章节：" + "、".join(titles.get(key, "相关章节") for key in item.chapter_ids) if item.chapter_ids else "")
        for item in inputs
    ) or "（没有另行提供的用户事实或反馈）"


async def _proposal_user_inputs(db: AsyncSession, proposal: MemoirBookRevisionProposal) -> list[RevisionUserInput]:
    stored = proposal.generation_json or {}
    if "user_inputs" in stored:
        return TypeAdapter(list[RevisionUserInput]).validate_python(stored["user_inputs"])
    # Older plans may still have their explicitly selected answers. Never reconstruct
    # user facts from AI-written plan text when their original feedback is unavailable.
    ids = (proposal.selected_actions_json or {}).get("issue_ids", [])
    issues = (await db.scalars(select(MemoirCompletionIssue).where(
        MemoirCompletionIssue.project_id == proposal.project_id, MemoirCompletionIssue.id.in_(ids),
    ))).all() if ids else []
    return [RevisionUserInput(text=item.outcome_note, question=item.question,
        chapter_ids=[item.chapter_id] if item.chapter_id else list(item.related_chapter_ids_json or []))
        for item in issues if item.outcome_note and book_review_answer_state.has_explicit_answer(item.outcome_note)]


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


async def selected_revision_has_pending_decision(
    db: AsyncSession,
    *,
    project_id: str,
    review_input_signature: str,
    chapter_ids: set[str],
) -> bool:
    """A pending decision limits only edits that involve its chapter."""
    issues = (await db.scalars(select(MemoirCompletionIssue).where(
        MemoirCompletionIssue.project_id == project_id,
        MemoirCompletionIssue.channel == MemoirCompletionIssueChannel.MANUAL_REVIEW.value,
        MemoirCompletionIssue.status != MemoirCompletionIssueStatus.CLOSED.value,
    ))).all()
    for issue in issues:
        if (
            issue.issue_type not in completion_issues.BLOCKING_BOOK_ISSUE_TYPES
            and (
                issue.scope != MemoirCompletionIssueScope.BOOK.value
                or issue.input_signature != review_input_signature
            )
        ):
            continue
        related_ids = (
            {issue.chapter_id}
            if issue.chapter_id
            else set(issue.related_chapter_ids_json or [])
        )
        if not related_ids or related_ids.intersection(chapter_ids):
            return True
    return False


def _sections(snapshot: dict[str, object]) -> list[dict[str, object]]:
    value = snapshot.get("sections")
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _reference_maps(
    snapshots: list[dict[str, object]],
    allowed_chapter_ids: set[str],
    *,
    stale_message: str,
) -> tuple[ModelReferenceMap, ModelReferenceMap]:
    ordered_chapter_ids: list[str] = []
    section_ids: list[str] = []
    chapter_labels: dict[str, str] = {}
    section_labels: dict[str, str] = {}
    for chapter_index, snapshot in enumerate(snapshots, start=1):
        chapter_id = _text(snapshot.get("chapter_id"))
        if chapter_id not in allowed_chapter_ids:
            continue
        chapter_title = _text(snapshot.get("chapter_title"))
        ordered_chapter_ids.append(chapter_id)
        chapter_labels[chapter_id] = f"第{chapter_index}章《{chapter_title}》"
        for section_index, section in enumerate(_sections(snapshot), start=1):
            section_id = _text(section.get("section_id"))
            if not section_id:
                continue
            section_ids.append(section_id)
            section_labels[section_id] = (
                f"第{chapter_index}章《{chapter_title}》的"
                f"第{section_index}小节《{_text(section.get('title'))}》"
            )
    if set(ordered_chapter_ids) != allowed_chapter_ids:
        raise BookRevisionError(stale_message)
    return (
        ModelReferenceMap(
            "C",
            ordered_chapter_ids,
            labels=chapter_labels,
            fallback_label="相关章节",
        ),
        ModelReferenceMap(
            "S",
            section_ids,
            labels=section_labels,
            fallback_label="相关小节",
        ),
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
            if any(point.material_ids for point in revision.body_digest.points):
                raise ValueError("全书正文改稿未提供素材编号，摘要不能猜测来源")
            section.update(digest_snapshot(content, revision.body_digest))
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


async def generate_revision_plan(
    *,
    project_id: str,
    prompt: str,
    engine: str,
    error_message: str,
    ai_model: str | None,
    ai_client: AITextClient | None,
    reference_maps: tuple[ModelReferenceMap, ...],
) -> GeneratedRevisionPlan:
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
        plan_value: object = generated.value.model_dump()
        for references in reference_maps:
            plan_value = _render_reference_labels(plan_value, references)
        return GeneratedRevisionPlan(
            plan=_RevisionPlan.model_validate(plan_value),
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


def _render_reference_labels(
    value: object,
    references: ModelReferenceMap,
) -> object:
    if isinstance(value, str):
        return references.render_text(value)
    if isinstance(value, list):
        return [_render_reference_labels(item, references) for item in value]
    if isinstance(value, dict):
        return {
            key: _render_reference_labels(item, references)
            for key, item in value.items()
        }
    return value


async def _load_baseline(
    db: AsyncSession,
    *,
    project_id: str,
    missing_message: str,
) -> tuple[MemoirBookOutput, MemoirBookOutputVersion]:
    assembled = await assemble_book_output(
        db,
        project_id=project_id,
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
    await db.execute(select(MemoirBookOutput.id).where(MemoirBookOutput.id == output_id).with_for_update())
    baseline = await db.get(MemoirBookOutputVersion, baseline_id)
    latest = await book_output_versions.revision_compatible_version(db, baseline) if baseline else None
    if (
        review is None
        or (require_ready_review and review.status != "ready")
        or review.input_signature != expected_review_input_signature
        or latest is None
    ):
        raise BookRevisionError(stale_message)
    return review, latest


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


def _book_information(
    output: MemoirBookOutput,
    version: MemoirBookOutputVersion,
) -> dict[str, str]:
    return {
        "title": book_output_versions.book_output_version_title(
            version,
            fallback=output.title,
        ),
        **stored_manuscript_metadata(version.manuscript_json),
    }


def _plan_output_instruction() -> str:
    return """请先判断是否需要修改，不要输出修改后的正文。

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
  ],
  "book_metadata_patch": {
    "title": "调整后的完整书名；不调整则省略",
    "subtitle": "调整后的副标题；不调整则省略",
    "narrator_name": "调整后的口述人；不调整则省略",
    "prepared_by": "调整后的整理人；不调整则省略",
    "preface": "调整后的完整前言；不调整则省略",
    "afterword": "调整后的完整后记；不调整则省略",
    "back_cover_quote": "调整后的封底文案；不调整则省略"
  }
}

要求：
- 输出的是用户确认前的整体修改思路，不是修改后的书稿。
- items 有内容时，每一项都要说清楚修改目标、原因和做法。
- targets 使用章节名或小节名。
- 书名或成书信息需要调整时，在 book_metadata_patch 给出供用户确认的完整新值，并在 items 说明理由和做法。
- book_metadata_patch 只包含需要修改的字段；没有成书信息调整时返回空对象。
- 事实只来自当前书稿及单独列出的用户原话。检查结论和旧修改思路是编辑建议，不能作为事实证据；确认计划不等于确认新增事实。
- 用户后续原话明确纠正前述事实时，只替换涉及部分，保留否定、疑义、视角和转述来源。
- 不新增、推测或改变人生事实。
- 不需要修改时，items 返回空数组，并在 summary 里说明原因。
"""


def _plan_feedback_prompt(
    *,
    plan: _RevisionPlan,
    feedback: str,
    snapshots: list[dict[str, object]],
    book_information: dict[str, str],
    user_inputs: list[RevisionUserInput] | None = None,
) -> str:
    ids = {_text(item.get("chapter_id")) for item in snapshots}
    chapter_refs, section_refs = _reference_maps(snapshots, ids, stale_message="书稿章节已变化")
    body = _chapter_source_text(snapshots=snapshots, chapter_refs=chapter_refs, section_refs=section_refs)
    return checked_review_prompt(f"""请根据用户反馈更新完整修改思路。

当前修改思路：
{json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, indent=2)}

用户最新意见：
{feedback}

当前成书信息：
{book_information_text(book_information)}

此前用户原话（按时间排列，最新明确更正优先）：
{_user_input_text(user_inputs or [], snapshots)}

当前完整书稿：
{body}

请吸收用户意见后返回一份完整的新思路，不要只返回增量说明。
{_plan_output_instruction()}""", subject="修改思路")


def _plan_draft_prompt(
    *,
    plan: _RevisionPlan,
    snapshots: list[dict[str, object]],
    chapter_refs: ModelReferenceMap,
    section_refs: ModelReferenceMap,
    user_inputs: list[RevisionUserInput] | None = None,
) -> str:
    chapters = _chapter_source_text(
        snapshots=snapshots,
        chapter_refs=chapter_refs,
        section_refs=section_refs,
    )
    return checked_review_prompt(f"""请按照已确认的修改思路生成书稿修改稿。

已确认的完整修改思路：
{json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, indent=2)}

用户原话（按时间排列，只有明确事实可以补入正文）：
{_user_input_text(user_inputs or [], snapshots)}
{ANSWER_CONTEXT_GUIDANCE}

当前书稿：
{chapters}

请输出严格 JSON：
{{
  "section_revisions": [
    {{
      "section_id": "需要修改的小节短编号",
      "content": "修改后的完整小节正文",
      "body_digest": {{"summary":"最终正文概括","focus":"叙事重点","points":[{{"text":"正文要点","material_ids":[]}}]}}
    }}
  ]
}}

要求：
- {BODY_DIGEST_INSTRUCTION} 本次输入没有素材编号，摘要 material_ids 必须为空。
- 只执行已确认修改思路中明确列出的调整。
- section_id 只能使用输入中的短编号。
- 修改小节时返回修改后的完整正文；不需要修改的小节不要返回。
- 不改变章节和小节的标题、数量、收录状态及归属关系。
- 事实只采用当前书稿及用户原话中明确提供的内容。修改思路仅确定编辑范围，不是事实证据；其中没有原文支持的新事实不得写入。
- 保留视角、否定、疑义和转述归属；计划未涉及的段落及文字逐字保留，不为统一文风扩大修改范围。
""", subject="修改稿")


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
    user_inputs = await _proposal_user_inputs(db, proposal)
    plan = _RevisionPlan.model_validate(proposal.revision_plan_json)
    if (
        response.status != MemoirBookRevisionProposalStatus.READY
        or book_revision_proposals.revision_plan_signature(proposal) != expected_plan_signature
    ):
        raise BookRevisionError("修改思路已经变化，请重新查看后再提交反馈")
    baseline = await db.get(
        MemoirBookOutputVersion,
        response.base_output_version_id,
    )
    if baseline is None:
        raise BookRevisionError("修改前的书稿版本已不可用")
    output = await db.get(MemoirBookOutput, baseline.output_id)
    if output is None:
        raise BookRevisionError("当前书稿已经变化，请重新查看")
    snapshots = _snapshots(baseline.chapter_snapshot_json)
    if progress_callback is not None:
        await progress_callback({"stage": "revising"})
    generated = await generate_revision_plan(
        project_id=project_id,
        prompt=_plan_feedback_prompt(
            plan=plan,
            feedback=feedback,
            snapshots=snapshots,
            book_information=_book_information(output, baseline),
            user_inputs=user_inputs,
        ),
        engine=PLAN_FEEDBACK_ENGINE,
        error_message="按反馈更新修改思路时遇到问题，请稍后重试",
        ai_model=ai_model,
        ai_client=ai_client,
        reference_maps=_reference_maps(
            snapshots,
            {
                chapter_id
                for snapshot in snapshots
                if (chapter_id := _text(snapshot.get("chapter_id")))
            },
            stale_message="书稿章节已经变化，请重新生成修改思路",
        ),
    )

    if progress_callback is not None:
        await progress_callback({"stage": "saving"})
    db.expire_all()
    await db.execute(select(MemoirBookRevisionProposal.id).where(
        MemoirBookRevisionProposal.project_id == project_id, MemoirBookRevisionProposal.id == proposal_id,
    ).with_for_update())
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
        or book_revision_proposals.revision_plan_signature(current) != expected_plan_signature
    ):
        raise BookRevisionError("调整期间修改思路已经变化，请重新提交反馈")
    user_inputs = [*user_inputs, RevisionUserInput(text=feedback)]
    current.phase = MemoirBookRevisionProposalPhase.PLAN_READY.value
    current.revision_plan_json = generated.plan.model_dump(mode="json")
    current.candidate_chapter_snapshot_json = []
    current.generation_json = {**generated.generation, "user_inputs": [item.model_dump(mode="json") for item in user_inputs]}
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
    user_inputs = await _proposal_user_inputs(db, proposal)
    plan = _RevisionPlan.model_validate(proposal.revision_plan_json)
    if (
        response.status != MemoirBookRevisionProposalStatus.READY
        or proposal.phase != MemoirBookRevisionProposalPhase.PLAN_READY.value
        or book_revision_proposals.revision_plan_signature(proposal) != expected_plan_signature
    ):
        raise BookRevisionError("修改思路已经变化，请重新查看后再生成修改稿")
    baseline = await db.get(
        MemoirBookOutputVersion,
        response.base_output_version_id,
    )
    if baseline is None:
        raise BookRevisionError("修改前的书稿版本已不可用")
    snapshots = _snapshots(baseline.chapter_snapshot_json)
    raw_action_ids = proposal.selected_actions_json.get("action_ids")
    if isinstance(raw_action_ids, list) and raw_action_ids:
        review = await db.get(MemoirBookReview, proposal.review_id)
        if review is None:
            raise BookRevisionError("全书检查结果已经变化，请重新检查")
        actions = book_editorial_actions.selected_review_actions(
            review.review_json,
            [str(value) for value in raw_action_ids],
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
            user_inputs=user_inputs,
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
    await db.execute(select(MemoirBookRevisionProposal.id).where(
        MemoirBookRevisionProposal.project_id == project_id, MemoirBookRevisionProposal.id == proposal_id,
    ).with_for_update())
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
        or book_revision_proposals.revision_plan_signature(current) != expected_plan_signature
    ):
        raise BookRevisionError("生成期间修改思路已经变化，请重新生成修改稿")
    current.phase = MemoirBookRevisionProposalPhase.DRAFT_READY.value
    current.candidate_chapter_snapshot_json = generated.snapshots
    current.generation_json = {**generated.generation, "user_inputs": [item.model_dump(mode="json") for item in user_inputs]}
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


async def refine_book_revision_proposal(
    db: AsyncSession,
    *,
    project_id: str,
    proposal_id: str,
    feedback: str,
    expected_plan_signature: str,
    ai_model: str | None,
    progress_callback: ProgressCallback | None = None,
    ai_client: AITextClient | None = None,
) -> BookRevisionResult:
    return await _refine_book_revision_plan(
        db,
        project_id=project_id,
        proposal_id=proposal_id,
        feedback=feedback,
        expected_plan_signature=expected_plan_signature,
        ai_model=ai_model,
        progress_callback=progress_callback,
        ai_client=ai_client,
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


def _revision_plan_prompt(
    *,
    snapshots: list[dict[str, object]],
    answer_context: book_review_answer_state.BookReviewAnswerContext,
    include_answers: bool,
    actions: list[dict[str, object]],
    chapter_refs: ModelReferenceMap,
    section_refs: ModelReferenceMap,
    book_information: dict[str, str],
    book_overview: str,
) -> str:
    answers = "\n\n".join(
        f"问题 {index}：{answer.question}\n"
        f"问题原因：{answer.reason}\n"
        f"用户回答：{answer.answer}\n"
        "关联章节：" + "、".join(chapter_refs.encode(value) for value in answer.chapter_ids)
        for index, answer in enumerate(answer_context.answers, start=1)
    ) if include_answers else "未选择问题回答"
    action_text = "\n\n".join(
        f"修改目标：{_text(action.get('title'))}\n"
        f"原因：{_text(action.get('reason'))}\n"
        "涉及范围："
        + (
            "、".join(
                _BOOK_METADATA_LABELS.get(str(field), str(field))
                for field in action.get("metadata_fields", [])
            )
            if action.get("action_type") == "revise_book_profile"
            else "、".join(
                chapter_refs.encode(str(chapter_id))
                for chapter_id in action.get("chapter_ids", [])
            )
        )
        for action in actions
    ) or "未选择编辑建议"
    related_ids = (
        (answer_context.related_chapter_ids if include_answers else set())
        | {
            str(chapter_id)
            for action in actions
            for chapter_id in action.get("chapter_ids", [])
        }
    )
    chapters = _chapter_source_text(
        snapshots=snapshots,
        chapter_refs=chapter_refs,
        section_refs=section_refs,
        allowed_chapter_ids=related_ids,
    )
    required_metadata_fields = book_editorial_actions.metadata_fields(actions)
    metadata_requirement = (
        "本次必须在 book_metadata_patch 中返回以下字段的完整新值："
        + "、".join(
            f"{field}（{_BOOK_METADATA_LABELS[field]}）"
            for field in sorted(required_metadata_fields)
        )
        if required_metadata_fields
        else "本次没有成书信息修改。"
    )
    return checked_review_prompt(f"""请根据用户选中的问题回答和编辑建议，制定一份统一的书稿修改思路。

用户选择采用的问题回答：
{answers}

用户选中的建议：
{action_text}

当前成书信息：
{book_information_text(book_information)}

全书概况：
{book_overview}

相关章节原文：
{chapters}

{metadata_requirement}

{ANSWER_CONTEXT_GUIDANCE}
{_plan_output_instruction()}""", subject="修改思路")


def _validate_metadata_plan(
    *,
    plan: _RevisionPlan,
    actions: list[dict[str, object]],
    book_information: dict[str, str],
) -> None:
    required = book_editorial_actions.metadata_fields(actions)
    missing = required.difference(plan.book_metadata_patch)
    unchanged = {
        field
        for field in required.difference(missing)
        if plan.book_metadata_patch[field] == book_information.get(field, "")
    }
    invalid = missing | unchanged
    if invalid:
        labels = "、".join(
            _BOOK_METADATA_LABELS[field]
            for field in sorted(invalid)
        )
        raise BookRevisionError(f"修改思路没有生成有效的{labels}，请重新生成")


async def generate_book_revision_proposal(
    db: AsyncSession,
    *,
    project_id: str,
    expected_review_input_signature: str,
    expected_answers_signature: str,
    expected_actions_signature: str,
    include_answers: bool,
    action_ids: list[str],
    ai_model: str | None,
    progress_callback: ProgressCallback | None = None,
    ai_client: AITextClient | None = None,
) -> BookRevisionResult:
    review = await book_reviews.get_current_book_review(db, project_id=project_id)
    if review is None or review.status != "ready" or review.input_signature != expected_review_input_signature:
        raise BookRevisionError("全书检查结果已经变化，请重新检查")
    answer_context = await book_review_answer_state.load_answer_context(
        db,
        review=review,
    )
    actions = (
        book_editorial_actions.selected_review_actions(
            review.review_json,
            action_ids,
        )
        if action_ids
        else []
    )
    if include_answers and not answer_context.answers:
        raise BookRevisionError("当前没有可以应用到书稿的回答")
    if include_answers and answer_context.answers_signature != expected_answers_signature:
        raise BookRevisionError("问题回答已经变化，请重新提交")
    if not include_answers and not actions:
        raise BookRevisionError("请至少选择回答或一条编辑建议")
    if any(
        action.get("action_type") == "revise_book_profile"
        and not action.get("metadata_fields")
        for action in actions
    ):
        raise BookRevisionError("成书信息建议已经过期，请重新检查全书")
    if book_editorial_actions.actions_signature(actions) != expected_actions_signature:
        raise BookRevisionError("所选编辑建议已经变化，请重新选择")
    related_ids = (
        (answer_context.related_chapter_ids if include_answers else set())
        | {
            str(chapter_id)
            for action in actions
            for chapter_id in action.get("chapter_ids", [])
        }
    )
    if await selected_revision_has_pending_decision(
        db, project_id=project_id,
        review_input_signature=review.input_signature,
        chapter_ids=set(related_ids),
    ):
        raise BookRevisionError("请先处理所选章节需要确认的问题")

    if progress_callback is not None:
        await progress_callback({"stage": "assembling"})
    output, baseline = await _load_baseline(
        db,
        project_id=project_id,
        missing_message="当前书稿还没有准备好",
    )
    original_snapshots = _snapshots(baseline.chapter_snapshot_json)
    book_information = _book_information(output, baseline)
    chapter_refs, section_refs = _reference_maps(
        original_snapshots,
        related_ids,
        stale_message="编辑建议涉及的章节已经变化，请重新检查全书",
    )
    if progress_callback is not None:
        await progress_callback({"stage": "revising"})
    generated_plan = await generate_revision_plan(
        project_id=project_id,
        prompt=_revision_plan_prompt(
            snapshots=original_snapshots,
            answer_context=answer_context,
            include_answers=include_answers,
            actions=actions,
            chapter_refs=chapter_refs,
            section_refs=section_refs,
            book_information=book_information,
            book_overview=_text(review.review_json.get("summary")),
        ),
        engine=PLAN_ENGINE,
        error_message="生成修改思路时遇到问题，请稍后重试",
        ai_model=ai_model,
        ai_client=ai_client,
        reference_maps=(chapter_refs, section_refs),
    )
    _validate_metadata_plan(
        plan=generated_plan.plan,
        actions=actions,
        book_information=book_information,
    )

    # The plan already contains complete metadata values. This is an actual,
    # bounded patch, so a second LLM pass over the entire body adds no value.
    direct_metadata_preview = bool(
        not include_answers
        and actions
        and all(action.get("action_type") == "revise_book_profile" for action in actions)
        and generated_plan.plan.book_metadata_patch
        and set(generated_plan.plan.book_metadata_patch) <= book_editorial_actions.metadata_fields(actions)
    )

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
    current_actions = (
        book_editorial_actions.selected_review_actions(
            current_review.review_json,
            action_ids,
        )
        if action_ids
        else []
    )
    current_answers = await book_review_answer_state.load_answer_context(
        db,
        review=current_review,
    )
    if (
        book_editorial_actions.actions_signature(current_actions) != expected_actions_signature
        or (
            include_answers
            and current_answers.answers_signature != expected_answers_signature
        )
    ):
        raise BookRevisionError("生成期间编辑建议或问题回答已经变化，请重试")
    if await selected_revision_has_pending_decision(
        db, project_id=project_id,
        review_input_signature=current_review.input_signature,
        chapter_ids=set(related_ids),
    ):
        raise BookRevisionError("所选章节出现需要确认的问题，请处理后重试")
    proposal = await book_revision_proposals.create_ready_proposal(
        db,
        review=current_review,
        baseline=baseline,
        candidate_snapshots=original_snapshots if direct_metadata_preview else [],
        selected_actions={
            "source": book_revision_proposals.BOOK_REVIEW_SELECTION_SOURCE,
            "include_answers": include_answers,
            "answers_signature": (
                current_answers.answers_signature if include_answers else ""
            ),
            "issue_ids": (
                [answer.issue_id for answer in current_answers.answers]
                if include_answers
                else []
            ),
            "action_ids": action_ids,
            "actions_signature": expected_actions_signature,
        },
        generation={**generated_plan.generation, "user_inputs": [RevisionUserInput(text=answer.answer,
            question=answer.question, chapter_ids=list(answer.chapter_ids)).model_dump(mode="json")
            for answer in current_answers.answers] if include_answers else []},
        summary=generated_plan.plan.summary.strip(),
        phase=(MemoirBookRevisionProposalPhase.DRAFT_READY if direct_metadata_preview
               else MemoirBookRevisionProposalPhase.PLAN_READY),
        revision_plan=generated_plan.plan.model_dump(mode="json"),
    )
    summary = generated_plan.plan.summary.strip()
    return BookRevisionResult(
        proposal_id=proposal.id,
        changed_chapter_count=0,
        selected_action_count=len(actions),
        summary=summary,
        requested_ai_model=ai_model,
        resolved_ai_model=generated_plan.resolved_ai_model,
        generation=generated_plan.generation,
        proposal_phase=proposal.phase,
    )
