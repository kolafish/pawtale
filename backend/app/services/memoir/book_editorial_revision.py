from __future__ import annotations

from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
import logging
import time

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memoir import MemoirBookOutput, MemoirBookOutputVersion
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


BOOK_EDITORIAL_REVISION_ENGINE = "llm_book_editorial_revision_v1"
BOOK_EDITORIAL_REVISION_SYSTEM_PROMPT = "你是一位克制的中文回忆录编辑。你只执行用户选中的去重复或补衔接修改，不新增、推测或改变人生事实。"
EditorialRevisionProgressCallback = Callable[
    [dict[str, object]],
    Awaitable[None],
]
logger = logging.getLogger(__name__)


class _SectionRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_id: str
    content: str


class _EditorialRevisionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_revisions: list[_SectionRevision] = Field(default_factory=list)


@dataclass(frozen=True)
class EditorialRevisionResult:
    proposal_id: str
    selected_action_count: int
    requested_ai_model: str | None
    resolved_ai_model: str | None
    generation: dict[str, object]


class EditorialRevisionError(ValueError):
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
    return (
        [deepcopy(item) for item in value if isinstance(item, dict)]
        if isinstance(value, list)
        else []
    )


def _sections(snapshot: dict[str, object]) -> list[dict[str, object]]:
    value = snapshot.get("sections")
    return (
        [item for item in value if isinstance(item, dict)]
        if isinstance(value, list)
        else []
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
        positions = [
            index
            for index, snapshot in enumerate(result)
            if _text(snapshot.get("chapter_id")) in desired
        ]
        if len(positions) != len(desired) or any(
            chapter_id not in by_id for chapter_id in desired
        ):
            raise EditorialRevisionError("调整顺序涉及的章节已经变化，请重新检查全书")
        for position, chapter_id in zip(positions, desired):
            result[position] = by_id[chapter_id]
    for index, snapshot in enumerate(result, start=1):
        snapshot["chapter_index"] = index
    return result


def _content_actions(
    actions: list[dict[str, object]],
) -> list[dict[str, object]]:
    return [
        action
        for action in actions
        if action.get("action_type") in {"remove_repetition", "improve_transition"}
    ]


def _build_prompt(
    *,
    snapshots: list[dict[str, object]],
    actions: list[dict[str, object]],
    chapter_refs: ModelReferenceMap,
    section_refs: ModelReferenceMap,
) -> str:
    action_text = "\n\n".join(
        (
            f"类型：{action.get('action_type')}\n"
            f"修改目标：{_text(action.get('title'))}\n"
            f"原因：{_text(action.get('reason'))}\n"
            "涉及章节："
            + "、".join(
                chapter_refs.encode(str(chapter_id))
                for chapter_id in action.get("chapter_ids", [])
            )
        )
        for action in actions
    )
    allowed_chapter_ids = {
        str(chapter_id)
        for action in actions
        for chapter_id in action.get("chapter_ids", [])
    }
    chapter_text: list[str] = []
    for snapshot in snapshots:
        chapter_id = _text(snapshot.get("chapter_id"))
        if chapter_id not in allowed_chapter_ids:
            continue
        sections = "\n\n".join(
            (
                f"section_id: {section_refs.encode(_text(section.get('section_id')))}\n"
                f"小节名：{_text(section.get('title'))}\n"
                f"正文：\n{_text(section.get('content'))}"
            )
            for section in _sections(snapshot)
            if _text(section.get("section_id"))
        )
        chapter_text.append(
            f"chapter_id: {chapter_refs.encode(chapter_id)}\n章节名：{_text(snapshot.get('chapter_title'))}\n{sections}"
        )
    return f"""请执行用户选中的全书编辑修改。

选中的修改：
{action_text}

允许修改的章节正文：
{chr(10).join(chapter_text)}

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


def _normalize_revisions(
    *,
    snapshots: list[dict[str, object]],
    generated: _EditorialRevisionOutput,
    section_refs: ModelReferenceMap,
    allowed_chapter_ids: set[str],
) -> list[dict[str, object]]:
    section_owner = {
        _text(section.get("section_id")): _text(snapshot.get("chapter_id"))
        for snapshot in snapshots
        for section in _sections(snapshot)
        if _text(section.get("section_id"))
    }
    sections_by_id = {
        _text(section.get("section_id")): section
        for snapshot in snapshots
        for section in _sections(snapshot)
        if _text(section.get("section_id"))
    }
    revised: set[str] = set()
    for revision in generated.section_revisions:
        section_id = section_refs.decode(revision.section_id)
        if section_id in revised:
            raise ValueError("模型重复修改了同一小节")
        revised.add(section_id)
        if (
            section_owner.get(section_id) not in allowed_chapter_ids
            or section_id not in sections_by_id
        ):
            raise ValueError("模型试图修改未选择的章节")
        content = revision.content.strip()
        if not content:
            raise ValueError("模型返回了空的小节正文")
        section = sections_by_id[section_id]
        section["content"] = content
        section["content_char_count"] = len(content)
    for snapshot in snapshots:
        sections = _sections(snapshot)
        snapshot["sections"] = sections
        snapshot["section_count"] = len(sections)
        content = sectioned_chapter_content(sections)
        snapshot["content"] = content
        snapshot["content_char_count"] = len(content)
    return snapshots


def _summary(actions: list[dict[str, object]]) -> str:
    labels = {
        "remove_repetition": "去除重复内容",
        "improve_transition": "改善章节衔接",
        "reorder_chapters": "调整章节顺序",
    }
    selected = list(
        dict.fromkeys(
            labels[str(action.get("action_type"))]
            for action in actions
            if str(action.get("action_type")) in labels
        )
    )
    return f"修改预览已准备，包含{'、'.join(selected)}。"


async def generate_editorial_revision_proposal(
    db: AsyncSession,
    *,
    project_id: str,
    expected_review_input_signature: str,
    expected_actions_signature: str,
    action_ids: list[str],
    ai_model: str | None,
    progress_callback: EditorialRevisionProgressCallback | None = None,
    ai_client: AITextClient | None = None,
) -> EditorialRevisionResult:
    review = await book_reviews.get_current_book_review(
        db,
        project_id=project_id,
    )
    if (
        review is None
        or review.status != "ready"
        or review.input_signature != expected_review_input_signature
    ):
        raise EditorialRevisionError("全书检查结果已经变化，请重新检查")
    actions = book_editorial_actions.selected_review_actions(
        review.review_json,
        action_ids,
    )
    if book_editorial_actions.actions_signature(actions) != expected_actions_signature:
        raise EditorialRevisionError("所选编辑建议已经变化，请重新选择")

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
    baseline = await db.get(MemoirBookOutputVersion, assembled.output_version_id)
    if output is None or baseline is None:
        raise EditorialRevisionError("当前书稿还没有准备好")
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
        raise EditorialRevisionError("请先确认根据问题回答生成的修改预览")

    original_snapshots = _snapshots(baseline.chapter_snapshot_json)
    revised_snapshots = _reorder_chapters(original_snapshots, actions)
    content_actions = _content_actions(actions)
    generation: dict[str, object] = {
        "engine": BOOK_EDITORIAL_REVISION_ENGINE,
    }
    resolved_ai_model: str | None = None
    if content_actions:
        related_ids = {
            str(chapter_id)
            for action in content_actions
            for chapter_id in action.get("chapter_ids", [])
        }
        ordered_related_ids = [
            chapter_id
            for snapshot in revised_snapshots
            if (chapter_id := _text(snapshot.get("chapter_id"))) in related_ids
        ]
        section_ids = [
            _text(section.get("section_id"))
            for snapshot in revised_snapshots
            if _text(snapshot.get("chapter_id")) in related_ids
            for section in _sections(snapshot)
            if _text(section.get("section_id"))
        ]
        chapter_refs = ModelReferenceMap("C", ordered_related_ids)
        section_refs = ModelReferenceMap("S", section_ids)
        prompt = _build_prompt(
            snapshots=revised_snapshots,
            actions=content_actions,
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
                BOOK_EDITORIAL_REVISION_SYSTEM_PROMPT,
                prompt,
                output_model=_EditorialRevisionOutput,
                requested_model=ai_model,
                temperature=0.2,
                max_tokens=policy.max_tokens,
                timeout=timeout_seconds,
                max_attempts=1,
            )
            revised_snapshots = _normalize_revisions(
                snapshots=revised_snapshots,
                generated=generated.value,
                section_refs=section_refs,
                allowed_chapter_ids=related_ids,
            )
            generation = {
                **generated.response.diagnostics(),
                "engine": BOOK_EDITORIAL_REVISION_ENGINE,
                "timeout_seconds": timeout_seconds,
            }
            resolved_ai_model = generated.response.resolved_model
        except Exception as exc:
            logger.exception(
                "memoir editorial revision failed project_id=%s",
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
                "engine": BOOK_EDITORIAL_REVISION_ENGINE,
            }
            raise EditorialRevisionError(
                ai_user_error_message(
                    exc,
                    "生成全书修改预览时遇到问题，请稍后重试",
                ),
                generation=generation,
            ) from exc

    if progress_callback is not None:
        await progress_callback({"stage": "saving"})
    output_id = output.id
    baseline_id = baseline.id
    db.expire_all()
    current_review = await book_reviews.get_current_book_review(
        db,
        project_id=project_id,
    )
    latest = await book_output_versions.load_latest_book_output_version(
        db,
        output_id=output_id,
    )
    if (
        current_review is None
        or current_review.status != "ready"
        or current_review.input_signature != expected_review_input_signature
        or latest is None
        or latest.id != baseline_id
    ):
        raise EditorialRevisionError("生成期间全书内容已经变化，请重试")
    current_actions = book_editorial_actions.selected_review_actions(
        current_review.review_json,
        action_ids,
    )
    current_answers = await book_review_answer_state.load_answer_context(
        db,
        review=current_review,
    )
    if (
        book_editorial_actions.actions_signature(current_actions)
        != expected_actions_signature
        or current_answers.answers_signature != answer_context.answers_signature
    ):
        raise EditorialRevisionError("生成期间编辑建议或问题回答已经变化，请重试")
    baseline = await db.get(MemoirBookOutputVersion, baseline_id)
    if baseline is None:
        raise EditorialRevisionError("保存修改预览时书稿状态已经变化")
    proposal = await book_revision_proposals.create_ready_proposal(
        db,
        review=current_review,
        baseline=baseline,
        candidate_snapshots=revised_snapshots,
        selected_actions={
            "source": book_editorial_actions.BOOK_EDITORIAL_ACTION_SOURCE,
            "action_ids": action_ids,
            "actions_signature": expected_actions_signature,
            "answers_signature": current_answers.answers_signature,
        },
        comparison=book_revision_proposals.build_comparison(
            before=original_snapshots,
            after=revised_snapshots,
        ),
        generation=generation,
        summary=_summary(actions),
    )
    return EditorialRevisionResult(
        proposal_id=proposal.id,
        selected_action_count=len(actions),
        requested_ai_model=ai_model,
        resolved_ai_model=resolved_ai_model,
        generation=generation,
    )
