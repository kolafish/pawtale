from __future__ import annotations

from app.services.memoir.project_ai_preferences import project_transcript_polish_level

from dataclasses import dataclass
from typing import Any, Literal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow_naive
from app.domain.memoir.types import (
    MemoirBookRevisionProposalPhase,
    MemoirBookRevisionProposalStatus,
    MemoirCreatedBy,
    MemoirOutputStatus,
    MemoirSemanticCacheStatus,
)
from app.models.memoir import (
    MemoirBookOutput,
    MemoirBookOutputVersion,
    MemoirBookRevisionProposal,
    MemoirBookReview,
)
from app.models.projects import Project
from app.schemas.memoir_book_review import (
    MemoirBookRevisionComparison,
    MemoirBookRevisionPlan,
    MemoirBookRevisionPreviewVersion,
    MemoirBookRevisionProposalResponse,
)
from app.services.memoir import (
    book_editorial_actions,
    book_output_versions,
    book_review_answer_state,
    book_reviews,
)
from app.services.memoir.book_output_manuscript import (
    effective_book_metadata,
    manuscript_from_snapshot,
    manuscript_response,
    stored_cover_style,
    stored_manuscript_design,
    updated_manuscript_payload,
)
from app.services.memoir.book_output_snapshots import (
    book_content_from_chapter_snapshots,
)


BOOK_REVIEW_SELECTION_SOURCE = "book_review_selection"


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _snapshots(value: object) -> list[dict[str, object]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _sections(snapshot: dict[str, object]) -> list[dict[str, object]]:
    value = snapshot.get("sections")
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


_BOOK_METADATA_FIELDS = (
    "title",
    "subtitle",
    "narrator_name",
    "prepared_by",
    "preface",
    "afterword",
    "back_cover_quote",
)


def revision_plan_signature(proposal: MemoirBookRevisionProposal) -> str:
    from app.services.memoir.semantic_state import semantic_json_signature
    inputs = (proposal.generation_json or {}).get("user_inputs")
    return semantic_json_signature({"plan": proposal.revision_plan_json, "user_inputs": inputs}
        if inputs is not None else proposal.revision_plan_json)


def _revision_metadata_patch(proposal: MemoirBookRevisionProposal) -> dict[str, str]:
    plan = proposal.revision_plan_json
    value = plan.get("book_metadata_patch") if isinstance(plan, dict) else None
    return {
        field: text
        for field in _BOOK_METADATA_FIELDS
        if isinstance(value, dict)
        and field in value
        and isinstance((text := value[field]), str)
    }


def _book_information(
    *,
    project: Project,
    baseline: MemoirBookOutputVersion,
) -> dict[str, str]:
    return {
        "title": project.title.strip(),
        **effective_book_metadata(
            project=project,
            stored=baseline.manuscript_json,
            include_empty=True,
        ),
    }


def _metadata_changes(
    before: dict[str, str],
    after: dict[str, str],
) -> list[dict[str, str]]:
    return [
        {
            "field": field,
            "before_value": before.get(field, ""),
            "after_value": after.get(field, ""),
        }
        for field in _BOOK_METADATA_FIELDS
        if before.get(field, "") != after.get(field, "")
    ]


def _patched_book_information(
    *,
    project: Project,
    baseline: MemoirBookOutputVersion,
    before: dict[str, str],
    patch: dict[str, str],
) -> dict[str, str]:
    return {
        "title": patch.get("title", before["title"]),
        **effective_book_metadata(
            project=project,
            stored=baseline.manuscript_json,
            patch={key: value for key, value in patch.items() if key != "title"},
            include_empty=True,
        ),
    }


def build_comparison(
    *,
    before: list[dict[str, object]],
    after: list[dict[str, object]],
) -> dict[str, object]:
    before_order = [
        {
            "chapter_id": _text(chapter.get("chapter_id")),
            "title": _text(chapter.get("chapter_title")),
        }
        for chapter in before
        if _text(chapter.get("chapter_id"))
    ]
    after_order = [
        {
            "chapter_id": _text(chapter.get("chapter_id")),
            "title": _text(chapter.get("chapter_title")),
        }
        for chapter in after
        if _text(chapter.get("chapter_id"))
    ]
    before_by_id = {chapter_id: snapshot for snapshot in before if (chapter_id := _text(snapshot.get("chapter_id")))}
    changed_chapters: list[dict[str, object]] = []
    changed_section_count = 0
    for after_chapter in after:
        chapter_id = _text(after_chapter.get("chapter_id"))
        before_chapter = before_by_id.get(chapter_id)
        if before_chapter is None:
            continue
        before_sections = {
            section_id: section
            for section in _sections(before_chapter)
            if (section_id := _text(section.get("section_id")))
        }
        changed_sections: list[dict[str, object]] = []
        for after_section in _sections(after_chapter):
            section_id = _text(after_section.get("section_id"))
            before_section = before_sections.get(section_id)
            if before_section is None:
                continue
            before_content = _text(before_section.get("content"))
            after_content = _text(after_section.get("content"))
            before_title = _text(before_section.get("title"))
            after_title = _text(after_section.get("title")) or before_title
            if before_content == after_content and before_title == after_title:
                continue
            changed_sections.append(
                {
                    "section_id": section_id,
                    "title": after_title,
                    "before_title": before_title,
                    "after_title": after_title,
                    "before_content": before_content,
                    "after_content": after_content,
                    "before_char_count": len(before_content),
                    "after_char_count": len(after_content),
                }
            )
        if changed_sections:
            changed_section_count += len(changed_sections)
            changed_chapters.append(
                {
                    "chapter_id": chapter_id,
                    "title": (_text(after_chapter.get("chapter_title")) or _text(before_chapter.get("chapter_title"))),
                    "sections": changed_sections,
                }
            )
    before_char_count = sum(len(_text(section.get("content"))) for chapter in before for section in _sections(chapter))
    after_char_count = sum(len(_text(section.get("content"))) for chapter in after for section in _sections(chapter))
    return {
        "changed_chapter_count": len(changed_chapters),
        "changed_section_count": changed_section_count,
        "before_char_count": before_char_count,
        "after_char_count": after_char_count,
        "char_count_delta": after_char_count - before_char_count,
        "chapters": changed_chapters,
        "chapter_order_changed": before_order != after_order,
        "before_chapter_order": before_order,
        "after_chapter_order": after_order,
    }


async def create_ready_proposal(
    db: AsyncSession,
    *,
    review: MemoirBookReview,
    baseline: MemoirBookOutputVersion,
    candidate_snapshots: list[dict[str, object]],
    selected_actions: dict[str, object],
    generation: dict[str, object],
    summary: str,
    phase: MemoirBookRevisionProposalPhase = (MemoirBookRevisionProposalPhase.DRAFT_READY),
    revision_plan: dict[str, object] | None = None,
) -> MemoirBookRevisionProposal:
    previous = list(
        (
            await db.scalars(
                select(MemoirBookRevisionProposal).where(
                    MemoirBookRevisionProposal.project_id == review.project_id,
                    MemoirBookRevisionProposal.status == MemoirBookRevisionProposalStatus.READY.value,
                )
            )
        ).all()
    )
    for item in previous:
        item.status = MemoirBookRevisionProposalStatus.DISMISSED.value
        item.updated_at = utcnow_naive()
        db.add(item)
    proposal = MemoirBookRevisionProposal(
        project_id=review.project_id,
        review_id=review.id,
        review_input_signature=review.input_signature,
        base_output_version_id=baseline.id,
        status=MemoirBookRevisionProposalStatus.READY.value,
        selected_actions_json=selected_actions,
        phase=phase.value,
        revision_plan_json=revision_plan or {},
        candidate_chapter_snapshot_json=candidate_snapshots,
        generation_json=generation,
        summary=summary,
    )
    db.add(proposal)
    await db.flush()
    return proposal


async def load_proposal(
    db: AsyncSession,
    *,
    project_id: str,
    proposal_id: str,
) -> MemoirBookRevisionProposal:
    proposal = await db.scalar(
        select(MemoirBookRevisionProposal).where(
            MemoirBookRevisionProposal.id == proposal_id,
            MemoirBookRevisionProposal.project_id == project_id,
        )
    )
    if proposal is None:
        raise HTTPException(status_code=404, detail="修改预览不存在")
    return proposal


async def _proposal_records(
    db: AsyncSession,
    *,
    proposal: MemoirBookRevisionProposal,
) -> tuple[MemoirBookOutput, MemoirBookOutputVersion, Project]:
    baseline = await db.get(
        MemoirBookOutputVersion,
        proposal.base_output_version_id,
    )
    if baseline is None or baseline.project_id != proposal.project_id:
        raise HTTPException(status_code=409, detail="修改前的书稿版本已不可用")
    output = await db.get(MemoirBookOutput, baseline.output_id)
    project = await db.get(Project, proposal.project_id)
    if output is None or project is None:
        raise HTTPException(status_code=409, detail="书稿状态已变化")
    if proposal.status == MemoirBookRevisionProposalStatus.READY.value:
        baseline = await book_output_versions.revision_compatible_version(db, baseline) or baseline
    return output, baseline, project


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
            illustrations[chapter_id] = [item for item in raw_items if isinstance(item, dict)]
    raw_appendix = manuscript.get("image_appendix")
    appendix = [item for item in raw_appendix if isinstance(item, dict)] if isinstance(raw_appendix, list) else []
    return illustrations, appendix


def _candidate_manuscript(
    *,
    project: Project,
    baseline: MemoirBookOutputVersion,
    snapshots: list[dict[str, object]],
    content: str,
    title: str,
    metadata_patch: dict[str, str],
) -> dict[str, Any]:
    if snapshots == baseline.chapter_snapshot_json:
        # Same body: reuse the normal metadata editor so hand-edited intros,
        # quotations, images and layouts survive both preview and apply.
        return updated_manuscript_payload(
            title=title, stored=baseline.manuscript_json, content=content,
            snapshot=snapshots, cover_style_value=None, metadata_patch=metadata_patch,
        )
    illustrations, image_appendix = _stored_illustrations(baseline.manuscript_json)
    template, custom_layout = stored_manuscript_design(baseline.manuscript_json)
    manuscript = manuscript_from_snapshot(
        polish_level=project_transcript_polish_level(project),
        title=title,
        content=content,
        snapshot=snapshots,
        cover_style_value=stored_cover_style(baseline),
        template=template,
        custom_layout=custom_layout,
        metadata=effective_book_metadata(
            project=project,
            stored=baseline.manuscript_json,
            patch={
                key: value
                for key, value in metadata_patch.items()
                if key != "title"
            },
        ),
        chapter_illustrations_by_chapter=illustrations,
        image_appendix=image_appendix,
    )
    return manuscript.model_dump(mode="json") if manuscript else {}


@dataclass(frozen=True)
class _ProposalSourceState:
    review: MemoirBookReview
    answer_context: book_review_answer_state.BookReviewAnswerContext
    editorial_actions: list[dict[str, Any]]


async def _ready_source_state(
    db: AsyncSession,
    *,
    proposal: MemoirBookRevisionProposal,
    output: MemoirBookOutput,
    baseline: MemoirBookOutputVersion,
) -> tuple[_ProposalSourceState | None, str]:
    latest = await book_output_versions.load_latest_book_output_version(
        db,
        output_id=output.id,
    )
    if latest is None or latest.id != baseline.id:
        return None, "书稿已经变化，请重新生成修改预览"
    review = await book_reviews.get_current_book_review(
        db,
        project_id=proposal.project_id,
    )
    if (
        review is None
        or review.status != MemoirSemanticCacheStatus.READY.value
        or review.input_signature != proposal.review_input_signature
    ):
        return None, "全书检查结果已经变化，请重新生成修改预览"
    context = await book_review_answer_state.load_answer_context(
        db,
        review=review,
    )
    if (
        proposal.selected_actions_json.get("source")
        != BOOK_REVIEW_SELECTION_SOURCE
    ):
        return None, "修改预览来源无效"
    include_answers = proposal.selected_actions_json.get("include_answers") is True
    if include_answers:
        expected = _text(proposal.selected_actions_json.get("answers_signature"))
        if not expected or context.answers_signature != expected:
            return None, "问题回答已经变化，请重新生成修改预览"
    raw_action_ids = proposal.selected_actions_json.get("action_ids")
    try:
        action_ids = (
            [str(value) for value in raw_action_ids]
            if isinstance(raw_action_ids, list)
            else []
        )
        actions = (
            book_editorial_actions.selected_review_actions(
                review.review_json,
                action_ids,
            )
            if action_ids
            else []
        )
    except ValueError:
        return None, "所选编辑建议已经变化，请重新生成修改预览"
    if book_editorial_actions.actions_signature(actions) != proposal.selected_actions_json.get(
        "actions_signature"
    ):
        return None, "所选编辑建议已经变化，请重新生成修改预览"
    return _ProposalSourceState(review, context, actions), ""


async def proposal_response(
    db: AsyncSession,
    *,
    proposal: MemoirBookRevisionProposal,
    preview: Literal["none", "before", "after", "both"] = "both",
) -> MemoirBookRevisionProposalResponse:
    output, baseline, project = await _proposal_records(db, proposal=proposal)
    status = MemoirBookRevisionProposalStatus(proposal.status)
    phase = MemoirBookRevisionProposalPhase(proposal.phase)
    if status == MemoirBookRevisionProposalStatus.READY:
        _, stale_message = await _ready_source_state(
            db,
            proposal=proposal,
            output=output,
            baseline=baseline,
        )
        if stale_message:
            status = MemoirBookRevisionProposalStatus.STALE
    before_snapshots = _snapshots(baseline.chapter_snapshot_json)
    after_snapshots = (
        _snapshots(proposal.candidate_chapter_snapshot_json)
        if phase == MemoirBookRevisionProposalPhase.DRAFT_READY
        else before_snapshots
    )
    before_information = _book_information(
        project=project,
        baseline=baseline,
    )
    metadata_patch = _revision_metadata_patch(proposal)
    after_information = (
        _patched_book_information(
            project=project,
            baseline=baseline,
            before=before_information,
            patch=metadata_patch,
        )
        if phase == MemoirBookRevisionProposalPhase.DRAFT_READY
        else before_information
    )
    metadata_changes = _metadata_changes(
        before_information,
        after_information,
    )
    has_changes = after_snapshots != before_snapshots or bool(metadata_changes)
    before_manuscript = (
        manuscript_response(
            title=before_information["title"],
            content=baseline.content,
            snapshot=before_snapshots,
            stored=baseline.manuscript_json,
        )
        if preview in {"before", "both"}
        else None
    )
    after_manuscript = None
    if phase == MemoirBookRevisionProposalPhase.DRAFT_READY and preview in {"after", "both"}:
        after_content = book_content_from_chapter_snapshots(
            title=after_information["title"],
            snapshots=after_snapshots,
        )
        after_manuscript = manuscript_response(
            title=after_information["title"],
            content=after_content,
            snapshot=after_snapshots,
            stored=_candidate_manuscript(
                project=project,
                baseline=baseline,
                snapshots=after_snapshots,
                content=after_content,
                title=after_information["title"],
                metadata_patch=metadata_patch,
            ),
        )
    comparison_data = build_comparison(
        before=before_snapshots,
        after=after_snapshots,
    )
    comparison_data["metadata_changes"] = metadata_changes
    comparison = MemoirBookRevisionComparison.model_validate(comparison_data)
    return MemoirBookRevisionProposalResponse(
        id=proposal.id,
        project_id=proposal.project_id,
        status=status,
        phase=phase,
        revision_plan=(
            MemoirBookRevisionPlan.model_validate(proposal.revision_plan_json) if proposal.revision_plan_json else None
        ),
        summary=proposal.summary,
        base_output_version_id=baseline.id,
        result_output_version_id=proposal.result_output_version_id,
        comparison=comparison,
        before=MemoirBookRevisionPreviewVersion(
            version=baseline.version,
            manuscript=before_manuscript,
        ),
        after=MemoirBookRevisionPreviewVersion(
            version=baseline.version + int(has_changes),
            manuscript=after_manuscript,
        ),
        can_apply=(
            status == MemoirBookRevisionProposalStatus.READY
            and phase == MemoirBookRevisionProposalPhase.DRAFT_READY
            and has_changes
        ),
        can_dismiss=status
        in {
            MemoirBookRevisionProposalStatus.READY,
            MemoirBookRevisionProposalStatus.STALE,
        },
        created_at=proposal.created_at,
        updated_at=proposal.updated_at,
    )


async def apply_proposal(
    db: AsyncSession,
    *,
    proposal: MemoirBookRevisionProposal,
    preview: Literal["none", "before", "after", "both"] = "both",
) -> MemoirBookRevisionProposalResponse:
    if proposal.status == MemoirBookRevisionProposalStatus.APPLIED.value:
        return await proposal_response(db, proposal=proposal, preview=preview)
    if proposal.status != MemoirBookRevisionProposalStatus.READY.value:
        raise ValueError("这个修改预览已经不能确认")
    if proposal.phase != MemoirBookRevisionProposalPhase.DRAFT_READY.value:
        raise ValueError("请先按修改思路生成修改稿")
    await db.execute(select(MemoirBookOutput.id).where(
        MemoirBookOutput.project_id == proposal.project_id,
    ).with_for_update())
    output, baseline, project = await _proposal_records(db, proposal=proposal)
    state, stale_message = await _ready_source_state(
        db,
        proposal=proposal,
        output=output,
        baseline=baseline,
    )
    if state is None:
        raise ValueError(stale_message)
    review = state.review
    context = state.answer_context
    selected_editorial_actions = state.editorial_actions
    include_answers = proposal.selected_actions_json.get("include_answers") is True

    snapshots = _snapshots(proposal.candidate_chapter_snapshot_json)
    before_snapshots = _snapshots(baseline.chapter_snapshot_json)
    before_information = _book_information(
        project=project,
        baseline=baseline,
    )
    metadata_patch = _revision_metadata_patch(proposal)
    after_information = _patched_book_information(
        project=project,
        baseline=baseline,
        before=before_information,
        patch=metadata_patch,
    )
    content = book_content_from_chapter_snapshots(
        title=after_information["title"],
        snapshots=snapshots,
    )
    manuscript_json = _candidate_manuscript(
        project=project,
        baseline=baseline,
        snapshots=snapshots,
        content=content,
        title=after_information["title"],
        metadata_patch=metadata_patch,
    )
    if (
        snapshots == before_snapshots
        and not _metadata_changes(before_information, after_information)
    ):
        raise ValueError("这版没有产生实际修改，不能确认使用")
    await db.refresh(output, attribute_names=["metadata_json", "versions"])
    old_chapters = book_output_versions.manuscript_chapters(baseline)
    book_output_versions.claim_editorial_copy_fields(output,
        targets=[key for key in ("subtitle", "preface", "afterword", "back_cover_quote")
            if before_information.get(key) != after_information.get(key)],
        chapter_fields={chapter["chapter_id"]: [field for field in ("intro", "pull_quotes")
            if old_chapters.get(chapter["chapter_id"], {}).get(field) != chapter.get(field)]
            for chapter in manuscript_json.get("chapters", [])})
    applied_version = await book_output_versions.append_book_output_version(
        db,
        output=output,
        content=content,
        chapter_snapshot_json=snapshots,
        manuscript_json=manuscript_json,
        created_by=MemoirCreatedBy.AI,
    )

    changed_chapter_count = int(build_comparison(before=before_snapshots, after=snapshots)["changed_chapter_count"])
    metadata = {
        **(output.metadata_json if isinstance(output.metadata_json, dict) else {}),
        "source": BOOK_REVIEW_SELECTION_SOURCE,
    }
    if include_answers:
        metadata[book_review_answer_state.BOOK_REVIEW_ANSWER_APPLICATION_KEY] = {
            "review_input_signature": review.input_signature,
            "answers_signature": context.answers_signature,
            "output_version_id": applied_version.id,
            "applied_issue_ids": [answer.issue_id for answer in context.answers],
            "applied_answer_count": len(context.answers),
            "changed_chapter_count": changed_chapter_count,
            "summary": proposal.summary,
            "resolved_ai_model": _text(proposal.generation_json.get("model")),
            "engine": _text(proposal.generation_json.get("engine")),
            "proposal_id": proposal.id,
        }
    if selected_editorial_actions:
        metadata[book_editorial_actions.BOOK_EDITORIAL_REVISION_APPLICATION_KEY] = {
            "review_input_signature": review.input_signature,
            "action_ids": [str(action.get("id") or "") for action in selected_editorial_actions],
            "actions_signature": book_editorial_actions.actions_signature(selected_editorial_actions),
            "answers_signature": context.answers_signature,
            "output_version_id": applied_version.id,
            "proposal_id": proposal.id,
        }
    output.status = MemoirOutputStatus.DRAFT.value
    output.title = after_information["title"]
    project.title = after_information["title"]
    project.updated_at = utcnow_naive()
    output.metadata_json = metadata
    output.updated_at = utcnow_naive()
    proposal.status = MemoirBookRevisionProposalStatus.APPLIED.value
    proposal.result_output_version_id = applied_version.id
    proposal.updated_at = utcnow_naive()
    db.add(output)
    db.add(project)
    db.add(proposal)
    await db.flush()
    return await proposal_response(db, proposal=proposal, preview=preview)


async def dismiss_proposal(
    db: AsyncSession,
    *,
    proposal: MemoirBookRevisionProposal,
    preview: Literal["none", "before", "after", "both"] = "both",
) -> MemoirBookRevisionProposalResponse:
    if proposal.status == MemoirBookRevisionProposalStatus.APPLIED.value:
        raise ValueError("已经使用的修改版本不能放弃")
    if proposal.status != MemoirBookRevisionProposalStatus.DISMISSED.value:
        proposal.status = MemoirBookRevisionProposalStatus.DISMISSED.value
        proposal.updated_at = utcnow_naive()
        db.add(proposal)
        await db.flush()
    return await proposal_response(db, proposal=proposal, preview=preview)
