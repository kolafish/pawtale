from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import (
    MemoirCompletionItemStatus,
    MemoirNarrativeGapScopeType,
)
from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirEditingJobStatus,
    MemoirEditingJobType,
    MemoirOutputStatus,
)
from app.models.memoir import MemoirBookOutput, MemoirEditingJob
from app.models.memoir_narrative import MemoirCompletionItem
from app.schemas.memoir_narrative import (
    MemoirBookConsistencyAuditJobResponse,
    MemoirBookFormalizeResponse,
    MemoirBookReadinessBlocker,
    MemoirBookReadinessGroups,
    MemoirBookReadinessResponse,
)
from app.services.memoir.narrative.book_consistency_context import (
    BookConsistencyContext,
    book_consistency_signature,
    load_book_consistency_context,
)
from app.services.memoir.narrative.readiness_policy import (
    gap_blocks_formalization,
)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def invalidate_formal_book_output(output: MemoirBookOutput) -> None:
    output.status = MemoirOutputStatus.DRAFT.value
    output.formal_version_id = None
    output.formal_audit_job_id = None
    output.formalized_by_user_id = None
    output.formalized_at = None


def _blocker(code: str, title: str, detail: str = "", **ids) -> MemoirBookReadinessBlocker:
    return MemoirBookReadinessBlocker(code=code, title=title, detail=detail, **ids)


async def _latest_audit_job(
    db: AsyncSession,
    *,
    project_id: str,
) -> MemoirEditingJob | None:
    return await db.scalar(
        select(MemoirEditingJob)
        .where(
            MemoirEditingJob.project_id == project_id,
            MemoirEditingJob.job_type == MemoirEditingJobType.AUDIT_BOOK_CONSISTENCY.value,
        )
        .order_by(MemoirEditingJob.updated_at.desc(), MemoirEditingJob.created_at.desc())
    )


def _structure_blockers(context: BookConsistencyContext) -> list[MemoirBookReadinessBlocker]:
    if context.plan is None:
        return [_blocker("book_plan_missing", "还没有章节规划")]
    return []


def _source_blockers(context: BookConsistencyContext) -> list[MemoirBookReadinessBlocker]:
    blockers: list[MemoirBookReadinessBlocker] = []
    for chapter in context.chapters:
        draft = chapter.draft
        chapter_id = chapter.chapter.id
        if draft is None or not draft.content.strip():
            blockers.append(
                _blocker(
                    f"chapter_draft_missing:{chapter_id}",
                    f"“{chapter.chapter.title}”还没有正文",
                    chapter_id=chapter_id,
                )
            )
            continue
        if draft.source_changes_pending:
            blockers.append(
                _blocker(
                    f"chapter_draft_stale:{chapter_id}",
                    f"“{chapter.chapter.title}”的素材已变化",
                    "请重新整理并确认本章正文。",
                    chapter_id=chapter_id,
                )
            )
        if draft.status != MemoirBookChapterDraftStatus.CONFIRMED.value:
            blockers.append(
                _blocker(
                    f"chapter_draft_unconfirmed:{chapter_id}",
                    f"“{chapter.chapter.title}”正文还没有确认",
                    chapter_id=chapter_id,
                )
            )
    return blockers


def _output_blockers(context: BookConsistencyContext) -> list[MemoirBookReadinessBlocker]:
    if context.output is None or context.output_version is None:
        return [_blocker("book_output_missing", "还没有可正式化的全书稿")]
    snapshots = [
        item
        for item in (context.output_version.chapter_snapshot_json or [])
        if isinstance(item, dict)
    ]
    actual_versions = {
        (
            str(chapter.get("chapter_id") or ""),
            str(chapter.get("draft_version_id") or ""),
        )
        for chapter in snapshots
    }
    expected_versions = {
        (chapter.chapter.id, chapter.draft.version_id)
        for chapter in context.chapters
        if chapter.draft is not None
    }
    if not actual_versions or actual_versions != expected_versions:
        return [
            _blocker(
                "book_output_not_current",
                "全书稿不是当前章节正文的最新组装结果",
                "重新组装全书后再运行一致性检查。",
            )
        ]
    return []


async def _gap_groups(
    db: AsyncSession,
    *,
    project_id: str,
    context: BookConsistencyContext,
) -> tuple[list[MemoirBookReadinessBlocker], list[MemoirBookReadinessBlocker]]:
    completion_items = list(
        (
            await db.scalars(
                select(MemoirCompletionItem)
                .where(
                    MemoirCompletionItem.project_id == project_id,
                    MemoirCompletionItem.status.in_(
                        [
                            MemoirCompletionItemStatus.OPEN.value,
                            MemoirCompletionItemStatus.IN_PROGRESS.value,
                            MemoirCompletionItemStatus.DEFERRED.value,
                        ]
                    ),
                )
                .order_by(MemoirCompletionItem.last_detected_at.desc())
            )
        ).all()
    )
    must: list[MemoirBookReadinessBlocker] = []
    suggested: list[MemoirBookReadinessBlocker] = []
    current_chapter_ids = {chapter.chapter.id for chapter in context.chapters}
    current_story_ids = {
        story.id
        for chapter in context.chapters
        for story in chapter.stories.values()
    }
    current_claim_ids = set(context.claims)
    for item in completion_items:
        if item.scope_type == MemoirNarrativeGapScopeType.CHAPTER.value:
            relevant = item.chapter_id in current_chapter_ids
        elif item.scope_type == MemoirNarrativeGapScopeType.STORY.value:
            relevant = item.story_id in current_story_ids
        else:
            relevant = not any(
                (item.chapter_id, item.story_id, item.claim_id)
            ) or bool(
                item.chapter_id in current_chapter_ids
                or item.story_id in current_story_ids
                or item.claim_id in current_claim_ids
            )
        if not relevant:
            continue
        blocker = _blocker(
            f"completion_item:{item.id}",
            item.title,
            item.description,
            chapter_id=item.chapter_id,
            story_id=item.story_id,
            claim_id=item.claim_id,
            completion_item_id=item.id,
        )
        if gap_blocks_formalization(
            gap_type=item.item_type,
            severity=item.severity,
        ):
            must.append(blocker)
        else:
            suggested.append(blocker)
    return must, suggested


def _deduplicate(items: list[MemoirBookReadinessBlocker]) -> list[MemoirBookReadinessBlocker]:
    return list({item.code: item for item in items}.values())


async def get_book_readiness(
    db: AsyncSession,
    *,
    project_id: str,
) -> MemoirBookReadinessResponse:
    context = await load_book_consistency_context(db, project_id=project_id)
    signature = book_consistency_signature(context)
    latest_job = await _latest_audit_job(db, project_id=project_id)
    job_result = latest_job.result_json if latest_job and isinstance(latest_job.result_json, dict) else {}
    audit_is_current = bool(
        latest_job
        and latest_job.status == MemoirEditingJobStatus.SUCCEEDED.value
        and job_result.get("input_signature") == signature
        and job_result.get("semantic_audit_completed") is True
        and job_result.get("partial_result") is False
    )
    must = _structure_blockers(context) + _source_blockers(context) + _output_blockers(context)
    if not audit_is_current:
        must.append(
            _blocker(
                "book_consistency_audit_required",
                "需要对当前书稿运行一次完整的全书一致性检查",
            )
        )
    gap_must, suggested = await _gap_groups(
        db,
        project_id=project_id,
        context=context,
    )
    must.extend(gap_must)
    return MemoirBookReadinessResponse(
        project_id=project_id,
        ready_for_formal=not must,
        current_signature=signature,
        output_status=(
            MemoirOutputStatus(context.output.status) if context.output is not None else None
        ),
        output_version_id=context.output_version.id if context.output_version else None,
        latest_audit_job=(
            MemoirBookConsistencyAuditJobResponse.model_validate(latest_job)
            if latest_job
            else None
        ),
        audit_is_current=audit_is_current,
        groups=MemoirBookReadinessGroups(
            must_handle=_deduplicate(must),
            suggested=_deduplicate(suggested),
        ),
    )


async def formalize_book_output(
    db: AsyncSession,
    *,
    project_id: str,
    actor_user_id: str,
    audit_job_id: str,
    output_version_id: str,
) -> MemoirBookFormalizeResponse:
    readiness = await get_book_readiness(db, project_id=project_id)
    if not readiness.ready_for_formal:
        raise ValueError("book output does not meet the formal readiness gate")
    if (
        readiness.latest_audit_job is None
        or readiness.latest_audit_job.id != audit_job_id
        or readiness.output_version_id != output_version_id
    ):
        raise ValueError("book readiness changed; reload and run the audit again")
    output = await db.scalar(
        select(MemoirBookOutput).where(MemoirBookOutput.project_id == project_id)
    )
    if output is None:
        raise ValueError("book output does not belong to the project")
    if (
        output.status == MemoirOutputStatus.ACCEPTED.value
        and output.formal_version_id == output_version_id
        and output.formal_audit_job_id == audit_job_id
        and output.formalized_at is not None
    ):
        return MemoirBookFormalizeResponse(
            project_id=project_id,
            output_id=output.id,
            output_version_id=output_version_id,
            audit_job_id=audit_job_id,
            status=MemoirOutputStatus.ACCEPTED,
            formalized_at=output.formalized_at,
        )
    formalized_at = _utcnow()
    output.status = MemoirOutputStatus.ACCEPTED.value
    output.formal_version_id = output_version_id
    output.formal_audit_job_id = audit_job_id
    output.formalized_by_user_id = actor_user_id
    output.formalized_at = formalized_at
    output.updated_at = formalized_at
    db.add(output)
    await db.flush()
    return MemoirBookFormalizeResponse(
        project_id=project_id,
        output_id=output.id,
        output_version_id=output_version_id,
        audit_job_id=audit_job_id,
        status=MemoirOutputStatus.ACCEPTED,
        formalized_at=formalized_at,
    )
