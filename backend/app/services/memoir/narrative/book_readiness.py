from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import (
    MemoirChapterNodeStatus,
    MemoirChapterNodeType,
    MemoirDraftBlockEditorialStatus,
    MemoirDraftBlockFreshnessStatus,
    MemoirDraftBlockRole,
    MemoirCompletionItemStatus,
    MemoirNarrativeGapScopeType,
)
from app.domain.memoir.types import (
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
from app.services.memoir.narrative.claim_usage_policy import (
    claim_usable_for_publication,
)
from app.services.memoir.narrative.readiness_policy import (
    gap_blocks_formalization,
    story_can_support_draft,
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
    blockers: list[MemoirBookReadinessBlocker] = []
    if context.plan is None:
        return [_blocker("book_plan_missing", "还没有章节规划")]
    for chapter in context.chapters:
        chapter_id = chapter.chapter.id
        if chapter.contract is None:
            blockers.append(
                _blocker(
                    f"chapter_contract:{chapter_id}",
                    f"“{chapter.chapter.title}”还没有章节方向",
                    chapter_id=chapter_id,
                )
            )
            continue
        links_by_node: dict[str, list] = {}
        for link in chapter.links:
            links_by_node.setdefault(link.node_id, []).append(link)
        for node in chapter.nodes:
            if not node.required or node.status == MemoirChapterNodeStatus.EXCLUDED.value:
                continue
            if node.node_type != MemoirChapterNodeType.STORY.value:
                continue
            story_links = links_by_node.get(node.id, [])
            required_links = [link for link in story_links if link.required] or story_links
            if not story_links:
                blockers.append(
                    _blocker(
                        f"required_node_story:{node.id}",
                        f"故事节点“{node.title}”还没有必要故事",
                        chapter_id=chapter_id,
                    )
                )
            for link in required_links:
                story = chapter.stories.get(link.story_id)
                if story is None or not story_can_support_draft(story):
                    blockers.append(
                        _blocker(
                            f"required_story:{link.story_id}",
                            "必要故事当前不可用于成书",
                            chapter_id=chapter_id,
                            story_id=link.story_id,
                        )
                    )
                    continue
                has_supported_claim = any(
                    (claim := chapter.claims.get(story_claim.claim_id)) is not None
                    and claim_usable_for_publication(claim)
                    and chapter.evidence_counts.get(claim.id, 0) > 0
                    for story_claim in chapter.story_claims.get(story.id, [])
                )
                if not has_supported_claim:
                    blockers.append(
                        _blocker(
                            f"required_story_source:{story.id}",
                            f"“{story.title}”还没有可追溯的事实来源",
                            chapter_id=chapter_id,
                            story_id=story.id,
                        )
                    )
    return blockers


def _source_blockers(context: BookConsistencyContext) -> list[MemoirBookReadinessBlocker]:
    blockers: list[MemoirBookReadinessBlocker] = []
    active_blocks = [
        item
        for item in context.blocks
        if item.block.editorial_status != MemoirDraftBlockEditorialStatus.EXCLUDED.value
    ]
    if not active_blocks:
        return [_blocker("structured_blocks_missing", "书稿还没有结构化正文块")]
    for item in active_blocks:
        block = item.block
        common = {"chapter_id": block.chapter_id, "draft_block_id": block.id}
        if block.freshness_status != MemoirDraftBlockFreshnessStatus.CURRENT.value:
            blockers.append(
                _blocker(
                    f"stale_block:{block.id}",
                    "正文块引用的事实已经变化",
                    "重新生成或人工核对后才能正式成书。",
                    **common,
                )
            )
        if block.locked and item.locked_version_id and item.locked_version_id != item.version.id:
            blockers.append(
                _blocker(
                    f"locked_block_changed:{block.id}",
                    "锁定正文块在锁定后发生了变化",
                    **common,
                )
            )
        if block.block_role != MemoirDraftBlockRole.FACTUAL.value:
            continue
        if not item.claim_ids:
            blockers.append(
                _blocker(
                    f"factual_block_without_claim:{block.id}",
                    "事实正文块没有 Claim 来源",
                    **common,
                )
            )
            continue
        for claim_id in item.claim_ids:
            claim = context.claims.get(claim_id)
            reason = ""
            if claim is None:
                reason = "Claim 不存在"
            elif claim.superseded_by_claim_id:
                reason = "Claim 已被新版本取代"
            elif not claim_usable_for_publication(claim):
                reason = "Claim 不允许进入正文、尚有争议或已被否定"
            elif context.evidence_counts.get(claim_id, 0) < 1:
                reason = "Claim 没有有效支持证据"
            if reason:
                blockers.append(
                    _blocker(
                        f"invalid_block_claim:{block.id}:{claim_id}",
                        "事实正文块包含不可用于成书的 Claim",
                        reason,
                        claim_id=claim_id,
                        **common,
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
    actual_blocks = {
        (str(block.get("block_id") or ""), str(block.get("block_version_id") or ""))
        for chapter in snapshots
        for block in (
            chapter.get("block_snapshot", [])
            if isinstance(chapter.get("block_snapshot"), list)
            else []
        )
        if isinstance(block, dict)
    }
    expected_blocks = {
        (item.block.id, item.version.id)
        for item in context.blocks
        if item.block.editorial_status != MemoirDraftBlockEditorialStatus.EXCLUDED.value
    }
    if not actual_blocks or actual_blocks != expected_blocks:
        return [
            _blocker(
                "book_output_not_current",
                "全书稿不是当前结构化正文的最新组装结果",
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
