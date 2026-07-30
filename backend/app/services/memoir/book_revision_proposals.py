from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow_naive
from app.domain.memoir.types import (
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
)
from app.services.memoir.book_output_snapshots import (
    book_content_from_chapter_snapshots,
)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _snapshots(value: object) -> list[dict[str, object]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _sections(snapshot: dict[str, object]) -> list[dict[str, object]]:
    value = snapshot.get("sections")
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


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
            if before_content == after_content:
                continue
            changed_sections.append(
                {
                    "section_id": section_id,
                    "title": (_text(after_section.get("title")) or _text(before_section.get("title"))),
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
    comparison: dict[str, object],
    generation: dict[str, object],
    summary: str,
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
        candidate_chapter_snapshot_json=candidate_snapshots,
        comparison_json=comparison,
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
    output: MemoirBookOutput,
    baseline: MemoirBookOutputVersion,
    snapshots: list[dict[str, object]],
    content: str,
) -> dict[str, Any]:
    illustrations, image_appendix = _stored_illustrations(baseline.manuscript_json)
    manuscript = manuscript_from_snapshot(
        title=output.title,
        content=content,
        snapshot=snapshots,
        cover_style_value=stored_cover_style(baseline),
        metadata=effective_book_metadata(
            project=project,
            stored=baseline.manuscript_json,
        ),
        chapter_illustrations_by_chapter=illustrations,
        image_appendix=image_appendix,
    )
    return manuscript.model_dump(mode="json") if manuscript else {}


async def proposal_response(
    db: AsyncSession,
    *,
    proposal: MemoirBookRevisionProposal,
) -> MemoirBookRevisionProposalResponse:
    output, baseline, project = await _proposal_records(db, proposal=proposal)
    latest = await book_output_versions.load_latest_book_output_version(
        db,
        output_id=output.id,
    )
    status = MemoirBookRevisionProposalStatus(proposal.status)
    if status == MemoirBookRevisionProposalStatus.READY:
        review = await book_reviews.get_current_book_review(
            db,
            project_id=proposal.project_id,
        )
        expected_answers_signature = _text(
            proposal.selected_actions_json.get("answers_signature")
        )
        if (
            latest is None
            or latest.id != baseline.id
            or review is None
            or review.status != MemoirSemanticCacheStatus.READY.value
            or review.input_signature != proposal.review_input_signature
        ):
            status = MemoirBookRevisionProposalStatus.STALE
        elif proposal.selected_actions_json.get("source") == "book_review_answers":
            context = await book_review_answer_state.load_answer_context(
                db,
                review=review,
            )
            if context.answers_signature != expected_answers_signature:
                status = MemoirBookRevisionProposalStatus.STALE
        elif (
            proposal.selected_actions_json.get("source")
            == book_editorial_actions.BOOK_EDITORIAL_ACTION_SOURCE
        ):
            answer_context = await book_review_answer_state.load_answer_context(
                db,
                review=review,
            )
            raw_action_ids = proposal.selected_actions_json.get("action_ids")
            try:
                selected = book_editorial_actions.selected_review_actions(
                    review.review_json,
                    (
                        [str(value) for value in raw_action_ids]
                        if isinstance(raw_action_ids, list)
                        else []
                    ),
                )
            except ValueError:
                status = MemoirBookRevisionProposalStatus.STALE
            else:
                if (
                    book_editorial_actions.actions_signature(selected)
                    != proposal.selected_actions_json.get("actions_signature")
                    or answer_context.answers_signature
                    != proposal.selected_actions_json.get(
                        "answers_signature",
                        "",
                    )
                ):
                    status = MemoirBookRevisionProposalStatus.STALE
    before_snapshots = _snapshots(baseline.chapter_snapshot_json)
    after_snapshots = _snapshots(proposal.candidate_chapter_snapshot_json)
    before_manuscript = manuscript_response(
        title=output.title,
        content=baseline.content,
        snapshot=before_snapshots,
        stored=baseline.manuscript_json,
    )
    after_content = book_content_from_chapter_snapshots(
        title=output.title,
        snapshots=after_snapshots,
    )
    after_manuscript_json = _candidate_manuscript(
        project=project,
        output=output,
        baseline=baseline,
        snapshots=after_snapshots,
        content=after_content,
    )
    after_manuscript = manuscript_response(
        title=output.title,
        content=after_content,
        snapshot=after_snapshots,
        stored=after_manuscript_json,
    )
    comparison = MemoirBookRevisionComparison.model_validate(proposal.comparison_json)
    return MemoirBookRevisionProposalResponse(
        id=proposal.id,
        project_id=proposal.project_id,
        status=status,
        summary=proposal.summary,
        base_output_version_id=baseline.id,
        result_output_version_id=proposal.result_output_version_id,
        comparison=comparison,
        before=MemoirBookRevisionPreviewVersion(
            version=baseline.version,
            manuscript=before_manuscript,
        ),
        after=MemoirBookRevisionPreviewVersion(
            version=baseline.version + int(after_snapshots != before_snapshots),
            manuscript=after_manuscript,
        ),
        can_apply=status == MemoirBookRevisionProposalStatus.READY,
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
) -> MemoirBookRevisionProposalResponse:
    if proposal.status == MemoirBookRevisionProposalStatus.APPLIED.value:
        return await proposal_response(db, proposal=proposal)
    if proposal.status != MemoirBookRevisionProposalStatus.READY.value:
        raise ValueError("这个修改预览已经不能确认")
    output, baseline, project = await _proposal_records(db, proposal=proposal)
    latest = await book_output_versions.load_latest_book_output_version(
        db,
        output_id=output.id,
    )
    if latest is None or latest.id != baseline.id:
        raise ValueError("书稿已经变化，请重新生成修改预览")
    review = await book_reviews.get_current_book_review(
        db,
        project_id=proposal.project_id,
    )
    if (
        review is None
        or review.status != MemoirSemanticCacheStatus.READY.value
        or review.input_signature != proposal.review_input_signature
    ):
        raise ValueError("全书检查结果已经变化，请重新生成修改预览")
    source = proposal.selected_actions_json.get("source")
    context = None
    selected_editorial_actions: list[dict[str, Any]] = []
    if source == "book_review_answers":
        context = await book_review_answer_state.load_answer_context(
            db,
            review=review,
        )
        expected_answers_signature = _text(
            proposal.selected_actions_json.get("answers_signature")
        )
        if (
            not expected_answers_signature
            or context.answers_signature != expected_answers_signature
        ):
            raise ValueError("问题回答已经变化，请重新生成修改预览")
    elif source == book_editorial_actions.BOOK_EDITORIAL_ACTION_SOURCE:
        context = await book_review_answer_state.load_answer_context(
            db,
            review=review,
        )
        raw_action_ids = proposal.selected_actions_json.get("action_ids")
        selected_editorial_actions = (
            book_editorial_actions.selected_review_actions(
                review.review_json,
                (
                    [str(value) for value in raw_action_ids]
                    if isinstance(raw_action_ids, list)
                    else []
                ),
            )
        )
        if (
            book_editorial_actions.actions_signature(
                selected_editorial_actions
            )
            != proposal.selected_actions_json.get("actions_signature")
            or context.answers_signature
            != proposal.selected_actions_json.get("answers_signature", "")
        ):
            raise ValueError("所选编辑建议已经变化，请重新生成修改预览")
    else:
        raise ValueError("修改预览来源无效")

    snapshots = _snapshots(proposal.candidate_chapter_snapshot_json)
    content = book_content_from_chapter_snapshots(
        title=output.title,
        snapshots=snapshots,
    )
    applied_version = baseline
    if content != baseline.content or snapshots != _snapshots(baseline.chapter_snapshot_json):
        manuscript_json = _candidate_manuscript(
            project=project,
            output=output,
            baseline=baseline,
            snapshots=snapshots,
            content=content,
        )
        applied_version = MemoirBookOutputVersion(
            output_id=output.id,
            project_id=proposal.project_id,
            plan_id=output.plan_id,
            version=baseline.version + 1,
            content=content,
            chapter_snapshot_json=snapshots,
            manuscript_json=manuscript_json,
            created_by=MemoirCreatedBy.AI.value,
        )
        db.add(applied_version)
        await db.flush()

    changed_chapter_count = int(proposal.comparison_json.get("changed_chapter_count") or 0)
    metadata = {
        **(output.metadata_json if isinstance(output.metadata_json, dict) else {}),
        "source": str(source),
        "book_tts": None,
    }
    if source == "book_review_answers" and context is not None:
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
    else:
        metadata[
            book_editorial_actions.BOOK_EDITORIAL_REVISION_APPLICATION_KEY
        ] = {
            "review_input_signature": review.input_signature,
            "action_ids": [
                str(action.get("id") or "")
                for action in selected_editorial_actions
            ],
            "actions_signature": book_editorial_actions.actions_signature(
                selected_editorial_actions
            ),
            "answers_signature": context.answers_signature if context else "",
            "output_version_id": applied_version.id,
            "proposal_id": proposal.id,
        }
    output.status = MemoirOutputStatus.DRAFT.value
    output.metadata_json = metadata
    output.updated_at = utcnow_naive()
    proposal.status = MemoirBookRevisionProposalStatus.APPLIED.value
    proposal.result_output_version_id = applied_version.id
    proposal.updated_at = utcnow_naive()
    db.add(output)
    db.add(proposal)
    await db.flush()
    return await proposal_response(db, proposal=proposal)


async def dismiss_proposal(
    db: AsyncSession,
    *,
    proposal: MemoirBookRevisionProposal,
) -> MemoirBookRevisionProposalResponse:
    if proposal.status == MemoirBookRevisionProposalStatus.APPLIED.value:
        raise ValueError("已经使用的修改版本不能放弃")
    if proposal.status != MemoirBookRevisionProposalStatus.DISMISSED.value:
        proposal.status = MemoirBookRevisionProposalStatus.DISMISSED.value
        proposal.updated_at = utcnow_naive()
        db.add(proposal)
        await db.flush()
    return await proposal_response(db, proposal=proposal)
