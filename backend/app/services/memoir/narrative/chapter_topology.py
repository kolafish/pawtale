from __future__ import annotations

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import MemoirChapterContractStatus
from app.domain.memoir.types import MemoirBookPlanChapterStatus
from app.models.memoir import MemoirBookPlanChapter
from app.models.memoir_narrative import MemoirChapterContract
from app.schemas.memoir_narrative import (
    MemoirChapterContractConstraints,
    MemoirChapterContractDraftRequest,
    MemoirChapterStructureMergeRequest,
    MemoirChapterStructureSplitRequest,
)
from app.services.memoir.narrative import chapter_contracts
from app.services.memoir.narrative.chapter_structure_transfer import (
    merge_contract_requirements,
    move_nodes_between_drafts,
    nodes_for_contract,
    require_draft_contract,
)
from app.services.memoir.narrative.chapter_topology_effects import (
    copy_source_projection,
    invalidate_book_output,
    move_chapter_image_placements,
    resolve_invalidated_chapter_gaps,
    retire_pending_chapter_work,
    story_utterance_ids,
)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _split_payload(request: MemoirChapterStructureSplitRequest) -> dict[str, object]:
    return request.model_dump(mode="json")


async def split_structure(
    db: AsyncSession,
    *,
    project_id: str,
    request: MemoirChapterStructureSplitRequest,
    actor_user_id: str,
) -> tuple[MemoirChapterContract, MemoirChapterContract]:
    source = await chapter_contracts.load_contract(
        db,
        project_id=project_id,
        contract_id=request.source_contract_id,
    )
    target_chapter_id = str(
        uuid5(
            NAMESPACE_URL,
            "pawtale:chapter-structure-split:"
            f"{project_id}:{source.id}:{actor_user_id}:{request.idempotency_key}",
        )
    )
    expected_payload = _split_payload(request)
    existing_target = await db.get(MemoirBookPlanChapter, target_chapter_id)
    if existing_target is not None:
        metadata = existing_target.metadata_json or {}
        if metadata.get("narrative_split") != expected_payload:
            raise ValueError("chapter split idempotency key was reused with another payload")
        if existing_target.status == MemoirBookPlanChapterStatus.ARCHIVED.value:
            if not existing_target.merged_into_chapter_id:
                raise ValueError("archived chapter split target is missing its merge target")
            existing_target = await chapter_contracts.load_chapter(
                db,
                project_id=project_id,
                chapter_id=existing_target.merged_into_chapter_id,
            )
        target = await chapter_contracts.current_contract_for_chapter(
            db,
            project_id=project_id,
            chapter_id=existing_target.id,
        )
        if target is None:
            raise ValueError("chapter split target contract is missing")
        target_node_ids = {
            node.id for node in await nodes_for_contract(db, contract=target)
        }
        if not set(request.node_ids).issubset(target_node_ids):
            raise ValueError("chapter split idempotency state is incomplete")
        return source, target

    require_draft_contract(source)
    source_chapter = await chapter_contracts.load_chapter(
        db,
        project_id=project_id,
        chapter_id=source.chapter_id,
    )
    source_nodes = await nodes_for_contract(db, contract=source)
    moving_ids = set(request.node_ids)
    if moving_ids == {node.id for node in source_nodes}:
        raise ValueError("chapter structure split must leave at least one source node")
    active_chapters = list(
        (
            await db.scalars(
                select(MemoirBookPlanChapter)
                .where(
                    MemoirBookPlanChapter.project_id == project_id,
                    MemoirBookPlanChapter.plan_id == source_chapter.plan_id,
                    MemoirBookPlanChapter.status
                    == MemoirBookPlanChapterStatus.ACTIVE.value,
                )
                .order_by(MemoirBookPlanChapter.sort_order, MemoirBookPlanChapter.id)
            )
        ).all()
    )
    for index, chapter in enumerate(active_chapters):
        chapter.sort_order = 10000 + index
        db.add(chapter)
    await db.flush()
    source_index = next(
        index for index, chapter in enumerate(active_chapters) if chapter.id == source_chapter.id
    )
    target_chapter = MemoirBookPlanChapter(
        id=target_chapter_id,
        plan_id=source_chapter.plan_id,
        project_id=project_id,
        title=request.target_title,
        sort_order=20000,
        status=MemoirBookPlanChapterStatus.ACTIVE.value,
        metadata_json={"narrative_split": expected_payload},
    )
    db.add(target_chapter)
    await db.flush()
    ordered_chapters = [
        *active_chapters[: source_index + 1],
        target_chapter,
        *active_chapters[source_index + 1 :],
    ]
    for index, chapter in enumerate(ordered_chapters):
        chapter.sort_order = index
        db.add(chapter)
    await db.flush()

    target = await chapter_contracts.create_contract_revision(
        db,
        project_id=project_id,
        chapter_id=target_chapter.id,
        request=MemoirChapterContractDraftRequest(
            idempotency_key=f"chapter-split:{request.idempotency_key}",
            objective=request.target_objective,
            reader_takeaway=request.target_reader_takeaway,
            time_scope_text=request.target_time_scope_text,
            core_questions=request.target_core_questions,
            constraints=MemoirChapterContractConstraints(),
            copy_nodes=False,
        ),
        actor_user_id=actor_user_id,
    )
    moving_story_ids = await move_nodes_between_drafts(
        db,
        source=source,
        target=target,
        moving_ids=moving_ids,
        actor_user_id=actor_user_id,
    )
    await copy_source_projection(
        db,
        source_chapter=source_chapter,
        target_chapter=target_chapter,
        reason="narrative_structure_split",
        utterance_ids=await story_utterance_ids(
            db,
            project_id=project_id,
            story_ids=moving_story_ids,
        ),
    )
    await resolve_invalidated_chapter_gaps(
        db,
        project_id=project_id,
        chapter_id=source_chapter.id,
        operation="拆分",
        idempotency_key=request.idempotency_key,
    )
    await invalidate_book_output(db, project_id=project_id)
    return source, target


async def _archive_merged_chapter(
    db: AsyncSession,
    *,
    source: MemoirBookPlanChapter,
    target: MemoirBookPlanChapter,
    actor_user_id: str,
    request: MemoirChapterStructureMergeRequest,
) -> None:
    previously_merged_sources = list(
        (
            await db.scalars(
                select(MemoirBookPlanChapter).where(
                    MemoirBookPlanChapter.project_id == source.project_id,
                    MemoirBookPlanChapter.plan_id == source.plan_id,
                    MemoirBookPlanChapter.status
                    == MemoirBookPlanChapterStatus.ARCHIVED.value,
                    MemoirBookPlanChapter.merged_into_chapter_id == source.id,
                )
            )
        ).all()
    )
    for chapter in previously_merged_sources:
        chapter.merged_into_chapter_id = target.id
        db.add(chapter)
    await db.flush()

    all_chapters = list(
        (
            await db.scalars(
                select(MemoirBookPlanChapter)
                .where(MemoirBookPlanChapter.plan_id == source.plan_id)
                .order_by(MemoirBookPlanChapter.sort_order, MemoirBookPlanChapter.id)
            )
        ).all()
    )
    source.archived_sort_order = source.sort_order
    source.sort_order = min((chapter.sort_order for chapter in all_chapters), default=0) - 1
    source.status = MemoirBookPlanChapterStatus.ARCHIVED.value
    source.archived_at = _utcnow()
    source.archived_by_user_id = actor_user_id
    source.merged_into_chapter_id = target.id
    source.metadata_json = {
        **(source.metadata_json or {}),
        "narrative_merge": request.model_dump(mode="json"),
    }
    db.add(source)
    await db.flush()
    active = [
        chapter
        for chapter in all_chapters
        if chapter.id != source.id
        and chapter.status == MemoirBookPlanChapterStatus.ACTIVE.value
    ]
    for index, chapter in enumerate(active):
        chapter.sort_order = 10000 + index
        db.add(chapter)
    await db.flush()
    for index, chapter in enumerate(active):
        chapter.sort_order = index
        db.add(chapter)
    await db.flush()


async def merge_structures(
    db: AsyncSession,
    *,
    project_id: str,
    request: MemoirChapterStructureMergeRequest,
    actor_user_id: str,
) -> tuple[MemoirChapterContract, MemoirChapterContract]:
    source = await chapter_contracts.load_contract(
        db,
        project_id=project_id,
        contract_id=request.source_contract_id,
    )
    source_chapter = await db.scalar(
        select(MemoirBookPlanChapter).where(
            MemoirBookPlanChapter.project_id == project_id,
            MemoirBookPlanChapter.id == source.chapter_id,
        )
    )
    if source_chapter is None:
        raise ValueError("source chapter does not belong to the project")
    existing_merge = (source_chapter.metadata_json or {}).get("narrative_merge")
    if source_chapter.status == MemoirBookPlanChapterStatus.ARCHIVED.value:
        if existing_merge != request.model_dump(mode="json"):
            raise ValueError("chapter merge idempotency key was reused with another payload")
        target_chapter_id = source_chapter.merged_into_chapter_id
        if not target_chapter_id:
            raise ValueError("archived chapter merge target is missing")
        target = await chapter_contracts.current_contract_for_chapter(
            db,
            project_id=project_id,
            chapter_id=target_chapter_id,
        )
        if target is None:
            raise ValueError("chapter merge target contract is missing")
        return source, target

    require_draft_contract(source)
    target_chapter = await chapter_contracts.load_chapter(
        db,
        project_id=project_id,
        chapter_id=request.target_chapter_id,
    )
    if (
        source_chapter.id == target_chapter.id
        or source_chapter.plan_id != target_chapter.plan_id
    ):
        raise ValueError("chapter structure merge requires two different chapters in one plan")
    target = await chapter_contracts.ensure_draft_contract_revision(
        db,
        project_id=project_id,
        chapter_id=target_chapter.id,
        actor_user_id=actor_user_id,
        idempotency_key=f"chapter-merge:{request.idempotency_key}:target",
    )
    source_nodes = await nodes_for_contract(db, contract=source)
    if not source_nodes:
        raise ValueError("source chapter structure has no nodes to merge")
    await move_nodes_between_drafts(
        db,
        source=source,
        target=target,
        moving_ids={node.id for node in source_nodes},
        actor_user_id=actor_user_id,
    )
    merge_contract_requirements(source=source, target=target)
    db.add(target)
    await db.flush()
    await copy_source_projection(
        db,
        source_chapter=source_chapter,
        target_chapter=target_chapter,
        reason="narrative_structure_merge",
        utterance_ids=None,
    )
    await move_chapter_image_placements(
        db,
        source_chapter_id=source_chapter.id,
        target_chapter_id=target_chapter.id,
    )
    await retire_pending_chapter_work(
        db,
        source=source_chapter,
        target=target_chapter,
    )
    await resolve_invalidated_chapter_gaps(
        db,
        project_id=project_id,
        chapter_id=source_chapter.id,
        operation="合并",
        idempotency_key=request.idempotency_key,
    )
    source.status = MemoirChapterContractStatus.SUPERSEDED.value
    source.superseded_at = _utcnow()
    db.add(source)
    await _archive_merged_chapter(
        db,
        source=source_chapter,
        target=target_chapter,
        actor_user_id=actor_user_id,
        request=request,
    )
    await invalidate_book_output(db, project_id=project_id)
    await db.flush()
    return source, target
