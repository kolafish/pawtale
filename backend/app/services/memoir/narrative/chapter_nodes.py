from __future__ import annotations

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import (
    MemoirChapterContractStatus,
    MemoirChapterNodeStatus,
    MemoirChapterNodeStoryRole,
    MemoirChapterNodeType,
    MemoirNarrativeActorType,
    MemoirNarrativeDecisionAction,
    MemoirNarrativeDecisionTargetType,
    MemoirStoryLifecycleStatus,
    MemoirStoryPublicationStatus,
    MemoirStoryVerificationStatus,
)
from app.models.memoir_narrative import (
    MemoirChapterContract,
    MemoirChapterNode,
    MemoirChapterNodeStory,
    MemoirStory,
)
from app.schemas.memoir_narrative import (
    MemoirChapterNodeCreateRequest,
    MemoirChapterNodeReorderRequest,
    MemoirChapterNodeStoryAttachRequest,
    MemoirChapterNodeStoryMoveRequest,
    MemoirChapterNodeUpdateRequest,
    MemoirChapterStructureMergeRequest,
    MemoirChapterStructureSplitRequest,
    MemoirNarrativeDecisionRequest,
)
from app.services.memoir.narrative import chapter_contracts, decision_log


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _require_draft(contract: MemoirChapterContract) -> None:
    if contract.status != MemoirChapterContractStatus.DRAFT.value:
        raise ValueError("chapter structure changes require a draft contract revision")


async def load_node(
    db: AsyncSession,
    *,
    project_id: str,
    contract_id: str,
    node_id: str,
) -> MemoirChapterNode:
    node = await db.scalar(
        select(MemoirChapterNode).where(
            MemoirChapterNode.project_id == project_id,
            MemoirChapterNode.contract_id == contract_id,
            MemoirChapterNode.id == node_id,
        )
    )
    if node is None:
        raise ValueError("chapter node does not belong to the contract")
    return node


async def _nodes_for_contract(
    db: AsyncSession,
    *,
    contract: MemoirChapterContract,
) -> list[MemoirChapterNode]:
    return list(
        (
            await db.scalars(
                select(MemoirChapterNode)
                .where(
                    MemoirChapterNode.project_id == contract.project_id,
                    MemoirChapterNode.contract_id == contract.id,
                )
                .order_by(MemoirChapterNode.sort_order, MemoirChapterNode.id)
            )
        ).all()
    )


async def _validate_transition_refs(
    db: AsyncSession,
    *,
    contract: MemoirChapterContract,
    from_node_id: str | None,
    to_node_id: str | None,
) -> None:
    if not from_node_id or not to_node_id or from_node_id == to_node_id:
        raise ValueError("transition node requires two distinct neighboring nodes")
    nodes = list(
        (
            await db.scalars(
                select(MemoirChapterNode).where(
                    MemoirChapterNode.project_id == contract.project_id,
                    MemoirChapterNode.contract_id == contract.id,
                    MemoirChapterNode.id.in_([from_node_id, to_node_id]),
                )
            )
        ).all()
    )
    if {node.id for node in nodes} != {from_node_id, to_node_id}:
        raise ValueError("transition nodes must reference nodes in the same contract")
    if any(node.node_type == MemoirChapterNodeType.TRANSITION.value for node in nodes):
        raise ValueError("transition nodes cannot use another transition as an endpoint")


async def create_node(
    db: AsyncSession,
    *,
    project_id: str,
    contract_id: str,
    request: MemoirChapterNodeCreateRequest,
    actor_user_id: str,
) -> MemoirChapterNode:
    contract = await chapter_contracts.load_contract(
        db,
        project_id=project_id,
        contract_id=contract_id,
    )
    _require_draft(contract)
    node_id = str(
        uuid5(
            NAMESPACE_URL,
            f"pawtale:chapter-node:{project_id}:{contract_id}:{actor_user_id}:{request.idempotency_key}",
        )
    )
    existing = await db.scalar(
        select(MemoirChapterNode).where(MemoirChapterNode.id == node_id)
    )
    if existing is not None:
        if (
            existing.project_id != project_id
            or existing.contract_id != contract_id
            or existing.node_type != request.node_type.value
            or existing.title != request.title
            or existing.purpose != request.purpose
            or existing.required != request.required
            or existing.transition_from_node_id != request.transition_from_node_id
            or existing.transition_to_node_id != request.transition_to_node_id
            or existing.status != request.status.value
        ):
            raise ValueError("chapter node idempotency key was reused with another payload")
        return existing
    if request.node_type == MemoirChapterNodeType.TRANSITION:
        await _validate_transition_refs(
            db,
            contract=contract,
            from_node_id=request.transition_from_node_id,
            to_node_id=request.transition_to_node_id,
        )
    if (
        request.node_type == MemoirChapterNodeType.STORY
        and request.status == MemoirChapterNodeStatus.READY
    ):
        raise ValueError("story node cannot be ready before an eligible Story is linked")
    max_sort_order = await db.scalar(
        select(func.max(MemoirChapterNode.sort_order)).where(
            MemoirChapterNode.project_id == project_id,
            MemoirChapterNode.contract_id == contract_id,
        )
    )
    node = MemoirChapterNode(
        id=node_id,
        project_id=project_id,
        chapter_id=contract.chapter_id,
        contract_id=contract.id,
        node_type=request.node_type.value,
        title=request.title,
        purpose=request.purpose,
        sort_order=(
            int(max_sort_order) + 1 if max_sort_order is not None else 0
        ),
        required=request.required,
        transition_from_node_id=request.transition_from_node_id,
        transition_to_node_id=request.transition_to_node_id,
        status=request.status.value,
        created_by=MemoirNarrativeActorType.USER.value,
        created_by_user_id=actor_user_id,
        updated_by=MemoirNarrativeActorType.USER.value,
        updated_by_user_id=actor_user_id,
    )
    db.add(node)
    await db.flush()
    return node


async def _story_links_for_node(
    db: AsyncSession,
    *,
    node: MemoirChapterNode,
) -> list[MemoirChapterNodeStory]:
    return list(
        (
            await db.scalars(
                select(MemoirChapterNodeStory)
                .where(
                    MemoirChapterNodeStory.project_id == node.project_id,
                    MemoirChapterNodeStory.node_id == node.id,
                )
                .order_by(
                    MemoirChapterNodeStory.sort_order,
                    MemoirChapterNodeStory.id,
                )
            )
        ).all()
    )


async def _load_linkable_story(
    db: AsyncSession,
    *,
    project_id: str,
    story_id: str,
) -> MemoirStory:
    story = await db.scalar(
        select(MemoirStory).where(
            MemoirStory.project_id == project_id,
            MemoirStory.id == story_id,
        )
    )
    if story is None:
        raise ValueError("chapter node Story does not belong to the project")
    if (
        story.lifecycle_status != MemoirStoryLifecycleStatus.ACTIVE.value
        or story.publication_status != MemoirStoryPublicationStatus.ELIGIBLE.value
        or story.verification_status == MemoirStoryVerificationStatus.REJECTED.value
    ):
        raise ValueError("chapter node Story must be active and eligible")
    return story


async def recompute_story_node_status(
    db: AsyncSession,
    *,
    node: MemoirChapterNode,
    actor_user_id: str | None,
) -> None:
    if (
        node.node_type != MemoirChapterNodeType.STORY.value
        or node.status == MemoirChapterNodeStatus.EXCLUDED.value
    ):
        return
    links = await _story_links_for_node(db, node=node)
    required_links = [link for link in links if link.required] or links
    story_ids = {link.story_id for link in required_links}
    stories = {
        story.id: story
        for story in (
            await db.scalars(
                select(MemoirStory).where(
                    MemoirStory.project_id == node.project_id,
                    MemoirStory.id.in_(story_ids),
                )
            )
        ).all()
    } if story_ids else {}
    ready = bool(required_links) and all(
        story_id in stories and chapter_contracts.story_is_ready(stories[story_id])
        for story_id in story_ids
    )
    status = (
        MemoirChapterNodeStatus.READY.value
        if ready
        else MemoirChapterNodeStatus.NEEDS_WORK.value
    )
    if node.status != status:
        node.status = status
        node.updated_by = (
            MemoirNarrativeActorType.USER.value
            if actor_user_id
            else MemoirNarrativeActorType.SYSTEM.value
        )
        node.updated_by_user_id = actor_user_id
        db.add(node)
        await db.flush()


async def update_node(
    db: AsyncSession,
    *,
    project_id: str,
    contract_id: str,
    node_id: str,
    request: MemoirChapterNodeUpdateRequest,
    actor_user_id: str,
) -> MemoirChapterNode:
    contract = await chapter_contracts.load_contract(
        db,
        project_id=project_id,
        contract_id=contract_id,
    )
    _require_draft(contract)
    node = await load_node(
        db,
        project_id=project_id,
        contract_id=contract_id,
        node_id=node_id,
    )
    fields = request.model_fields_set
    if "title" in fields:
        node.title = request.title or ""
    if "purpose" in fields:
        node.purpose = request.purpose or ""
    if "required" in fields:
        node.required = bool(request.required)
    if "transition_from_node_id" in fields:
        node.transition_from_node_id = request.transition_from_node_id or None
    if "transition_to_node_id" in fields:
        node.transition_to_node_id = request.transition_to_node_id or None
    if node.node_type == MemoirChapterNodeType.TRANSITION.value:
        await _validate_transition_refs(
            db,
            contract=contract,
            from_node_id=node.transition_from_node_id,
            to_node_id=node.transition_to_node_id,
        )
    elif node.transition_from_node_id or node.transition_to_node_id:
        raise ValueError("only transition nodes can reference neighboring nodes")
    if "status" in fields and request.status is not None:
        node.status = request.status.value
    node.updated_by = MemoirNarrativeActorType.USER.value
    node.updated_by_user_id = actor_user_id
    db.add(node)
    await db.flush()
    if (
        node.node_type == MemoirChapterNodeType.STORY.value
        and node.status == MemoirChapterNodeStatus.READY.value
    ):
        await recompute_story_node_status(
            db,
            node=node,
            actor_user_id=actor_user_id,
        )
        if node.status != MemoirChapterNodeStatus.READY.value:
            raise ValueError("story node cannot be ready before linked Stories are ready")
    return node


async def reorder_nodes(
    db: AsyncSession,
    *,
    project_id: str,
    contract_id: str,
    request: MemoirChapterNodeReorderRequest,
    actor_user_id: str,
) -> list[MemoirChapterNode]:
    contract = await chapter_contracts.load_contract(
        db,
        project_id=project_id,
        contract_id=contract_id,
    )
    _require_draft(contract)
    nodes = await _nodes_for_contract(db, contract=contract)
    by_id = {node.id: node for node in nodes}
    if set(request.node_ids) != set(by_id):
        raise ValueError("chapter node order must contain every node exactly once")
    for index, node in enumerate(nodes):
        node.sort_order = 10000 + index
        db.add(node)
    await db.flush()
    ordered = [by_id[node_id] for node_id in request.node_ids]
    for index, node in enumerate(ordered):
        node.sort_order = index
        node.updated_by = MemoirNarrativeActorType.USER.value
        node.updated_by_user_id = actor_user_id
        db.add(node)
    await db.flush()
    return ordered


async def attach_story(
    db: AsyncSession,
    *,
    project_id: str,
    contract_id: str,
    node_id: str,
    request: MemoirChapterNodeStoryAttachRequest,
    actor_user_id: str,
) -> MemoirChapterNodeStory:
    contract = await chapter_contracts.load_contract(
        db,
        project_id=project_id,
        contract_id=contract_id,
    )
    _require_draft(contract)
    node = await load_node(
        db,
        project_id=project_id,
        contract_id=contract_id,
        node_id=node_id,
    )
    story = await _load_linkable_story(
        db,
        project_id=project_id,
        story_id=request.story_id,
    )
    constraints = chapter_contracts.contract_constraints(contract)
    if story.id in constraints.exclude_story_ids:
        raise ValueError("chapter contract explicitly excludes this Story")
    existing = await db.scalar(
        select(MemoirChapterNodeStory).where(
            MemoirChapterNodeStory.project_id == project_id,
            MemoirChapterNodeStory.node_id == node.id,
            MemoirChapterNodeStory.story_id == story.id,
        )
    )
    if existing is not None:
        if (
            existing.story_role != request.story_role.value
            or existing.sort_order != request.sort_order
            or existing.required != request.required
        ):
            raise ValueError("Story is already linked to the node with another role")
        return existing
    if request.story_role == MemoirChapterNodeStoryRole.PRIMARY:
        other_primary = await db.scalar(
            select(MemoirChapterNodeStory.id)
            .join(
                MemoirChapterNode,
                (MemoirChapterNode.project_id == MemoirChapterNodeStory.project_id)
                & (MemoirChapterNode.id == MemoirChapterNodeStory.node_id),
            )
            .where(
                MemoirChapterNodeStory.project_id == project_id,
                MemoirChapterNodeStory.story_id == story.id,
                MemoirChapterNodeStory.story_role
                == MemoirChapterNodeStoryRole.PRIMARY.value,
                MemoirChapterNode.contract_id == contract.id,
            )
        )
        if other_primary is not None:
            raise ValueError("a Story can only be primary once in one chapter contract")
    link = MemoirChapterNodeStory(
        project_id=project_id,
        node_id=node.id,
        story_id=story.id,
        story_role=request.story_role.value,
        sort_order=request.sort_order,
        required=request.required,
        created_by=MemoirNarrativeActorType.USER.value,
        created_by_user_id=actor_user_id,
    )
    db.add(link)
    await db.flush()
    await recompute_story_node_status(
        db,
        node=node,
        actor_user_id=actor_user_id,
    )
    return link


async def detach_story(
    db: AsyncSession,
    *,
    project_id: str,
    contract_id: str,
    node_id: str,
    story_id: str,
    actor_user_id: str,
) -> MemoirChapterNode:
    contract = await chapter_contracts.load_contract(
        db,
        project_id=project_id,
        contract_id=contract_id,
    )
    _require_draft(contract)
    node = await load_node(
        db,
        project_id=project_id,
        contract_id=contract_id,
        node_id=node_id,
    )
    link = await db.scalar(
        select(MemoirChapterNodeStory).where(
            MemoirChapterNodeStory.project_id == project_id,
            MemoirChapterNodeStory.node_id == node.id,
            MemoirChapterNodeStory.story_id == story_id,
        )
    )
    if link is None:
        raise ValueError("Story is not linked to the chapter node")
    await db.delete(link)
    await db.flush()
    await recompute_story_node_status(
        db,
        node=node,
        actor_user_id=actor_user_id,
    )
    return node


async def move_story(
    db: AsyncSession,
    *,
    project_id: str,
    contract_id: str,
    story_id: str,
    request: MemoirChapterNodeStoryMoveRequest,
    actor_user_id: str,
) -> MemoirChapterNodeStory:
    contract = await chapter_contracts.load_contract(
        db,
        project_id=project_id,
        contract_id=contract_id,
    )
    _require_draft(contract)
    source = await load_node(
        db,
        project_id=project_id,
        contract_id=contract_id,
        node_id=request.source_node_id,
    )
    target = await load_node(
        db,
        project_id=project_id,
        contract_id=contract_id,
        node_id=request.target_node_id,
    )
    if (
        source.node_type != MemoirChapterNodeType.STORY.value
        or target.node_type != MemoirChapterNodeType.STORY.value
    ):
        raise ValueError("Story links can only move between Story nodes")
    await _load_linkable_story(
        db,
        project_id=project_id,
        story_id=story_id,
    )
    link = await db.scalar(
        select(MemoirChapterNodeStory).where(
            MemoirChapterNodeStory.project_id == project_id,
            MemoirChapterNodeStory.node_id == source.id,
            MemoirChapterNodeStory.story_id == story_id,
        )
    )
    target_link = await db.scalar(
        select(MemoirChapterNodeStory).where(
            MemoirChapterNodeStory.project_id == project_id,
            MemoirChapterNodeStory.node_id == target.id,
            MemoirChapterNodeStory.story_id == story_id,
        )
    )
    if link is None:
        if target_link is not None:
            return target_link
        raise ValueError("Story is not linked to the source chapter node")
    if target_link is not None:
        raise ValueError("Story is already linked to the target chapter node")
    if link.story_role == MemoirChapterNodeStoryRole.PRIMARY.value:
        other_primary = await db.scalar(
            select(MemoirChapterNodeStory.id)
            .join(
                MemoirChapterNode,
                (MemoirChapterNode.project_id == MemoirChapterNodeStory.project_id)
                & (MemoirChapterNode.id == MemoirChapterNodeStory.node_id),
            )
            .where(
                MemoirChapterNodeStory.project_id == project_id,
                MemoirChapterNodeStory.story_id == story_id,
                MemoirChapterNodeStory.story_role
                == MemoirChapterNodeStoryRole.PRIMARY.value,
                MemoirChapterNode.contract_id == contract.id,
                MemoirChapterNodeStory.id != link.id,
            )
        )
        if other_primary is not None:
            raise ValueError("a Story can only be primary once in one chapter contract")

    link.node_id = target.id
    db.add(link)
    await db.flush()
    if await _story_links_for_node(db, node=source):
        await recompute_story_node_status(
            db,
            node=source,
            actor_user_id=actor_user_id,
        )
    else:
        source.required = False
        source.status = MemoirChapterNodeStatus.EXCLUDED.value
        source.updated_by = MemoirNarrativeActorType.USER.value
        source.updated_by_user_id = actor_user_id
        db.add(source)
    if target.status == MemoirChapterNodeStatus.EXCLUDED.value:
        target.status = MemoirChapterNodeStatus.PLANNED.value
    await recompute_story_node_status(
        db,
        node=target,
        actor_user_id=actor_user_id,
    )
    await db.flush()
    return link


def _included_story_node_purpose(story: MemoirStory) -> str:
    purposes = {
        "event": f"呈现“{story.title}”的起因、经过、结果和影响。",
        "relationship": f"呈现“{story.title}”中的相识、共同经历、关系变化和影响。",
        "life_stage": f"呈现“{story.title}”这一人生阶段的背景、经历、变化和影响。",
        "theme": f"围绕“{story.title}”组织有事实依据的经历、对照和回望。",
    }
    return purposes.get(story.story_type, f"完整呈现“{story.title}”及其影响。")


async def _create_included_story_node(
    db: AsyncSession,
    *,
    contract: MemoirChapterContract,
    story: MemoirStory,
    actor_user_id: str,
) -> MemoirChapterNode:
    node_id = str(
        uuid5(
            NAMESPACE_URL,
            f"pawtale:chapter-included-story:{contract.project_id}:{contract.id}:{story.id}",
        )
    )
    existing = await db.scalar(
        select(MemoirChapterNode).where(MemoirChapterNode.id == node_id)
    )
    if existing is not None:
        if (
            existing.project_id != contract.project_id
            or existing.contract_id != contract.id
            or existing.node_type != MemoirChapterNodeType.STORY.value
        ):
            raise ValueError("included Story node identity conflicts with another chapter node")
        existing.required = True
        if existing.status == MemoirChapterNodeStatus.EXCLUDED.value:
            existing.status = MemoirChapterNodeStatus.PLANNED.value
        existing.updated_by = MemoirNarrativeActorType.USER.value
        existing.updated_by_user_id = actor_user_id
        db.add(existing)
        await db.flush()
        return existing

    nodes = await _nodes_for_contract(db, contract=contract)
    insertion_index = next(
        (
            index
            for index, node in enumerate(nodes)
            if node.node_type
            in {
                MemoirChapterNodeType.REFLECTION.value,
                MemoirChapterNodeType.CLOSING.value,
            }
        ),
        len(nodes),
    )
    for index, node in enumerate(nodes):
        node.sort_order = 10000 + index
        db.add(node)
    await db.flush()

    created = MemoirChapterNode(
        id=node_id,
        project_id=contract.project_id,
        chapter_id=contract.chapter_id,
        contract_id=contract.id,
        node_type=MemoirChapterNodeType.STORY.value,
        title=story.title,
        purpose=_included_story_node_purpose(story),
        sort_order=insertion_index,
        required=True,
        status=MemoirChapterNodeStatus.PLANNED.value,
        created_by=MemoirNarrativeActorType.USER.value,
        created_by_user_id=actor_user_id,
        updated_by=MemoirNarrativeActorType.USER.value,
        updated_by_user_id=actor_user_id,
    )
    db.add(created)
    await db.flush()
    ordered_nodes = [*nodes[:insertion_index], created, *nodes[insertion_index:]]
    for index, node in enumerate(ordered_nodes):
        if node.sort_order == index:
            continue
        node.sort_order = index
        node.updated_by = MemoirNarrativeActorType.USER.value
        node.updated_by_user_id = actor_user_id
        db.add(node)
    await db.flush()
    return created


async def include_story_in_contract(
    db: AsyncSession,
    *,
    project_id: str,
    contract_id: str,
    story_id: str,
    request: MemoirNarrativeDecisionRequest,
    actor_user_id: str,
) -> MemoirChapterContract:
    contract = await chapter_contracts.load_contract(
        db,
        project_id=project_id,
        contract_id=contract_id,
    )
    existing_decision = await decision_log.existing_decision(
        db,
        project_id=project_id,
        idempotency_key=request.idempotency_key,
        target_type=MemoirNarrativeDecisionTargetType.CHAPTER_CONTRACT,
        target_id=contract.id,
        action=MemoirNarrativeDecisionAction.INCLUDE,
        actor_type=MemoirNarrativeActorType.USER,
        actor_user_id=actor_user_id,
    )
    if existing_decision is not None:
        if existing_decision.after_state_json.get("included_story_id") != story_id:
            raise ValueError("idempotency key was reused for another chapter Story")
        return contract

    _require_draft(contract)
    story = await _load_linkable_story(
        db,
        project_id=project_id,
        story_id=story_id,
    )
    nodes = await _nodes_for_contract(db, contract=contract)
    primary_links = list(
        (
            await db.scalars(
                select(MemoirChapterNodeStory)
                .join(
                    MemoirChapterNode,
                    (MemoirChapterNode.project_id == MemoirChapterNodeStory.project_id)
                    & (MemoirChapterNode.id == MemoirChapterNodeStory.node_id),
                )
                .where(
                    MemoirChapterNodeStory.project_id == project_id,
                    MemoirChapterNodeStory.story_id == story.id,
                    MemoirChapterNodeStory.story_role
                    == MemoirChapterNodeStoryRole.PRIMARY.value,
                    MemoirChapterNode.contract_id == contract.id,
                )
            )
        ).all()
    )
    if len(primary_links) > 1:
        raise ValueError("a Story can only be primary once in one chapter contract")

    constraints = chapter_contracts.contract_constraints(contract)
    before_state = {
        "story_id": story.id,
        "include_story_ids": constraints.include_story_ids,
        "exclude_story_ids": constraints.exclude_story_ids,
        "primary_node_ids": [link.node_id for link in primary_links],
    }
    if primary_links:
        link = primary_links[0]
        node = next(node for node in nodes if node.id == link.node_id)
        link.required = True
        node.required = True
        if node.status == MemoirChapterNodeStatus.EXCLUDED.value:
            node.status = MemoirChapterNodeStatus.PLANNED.value
        node.updated_by = MemoirNarrativeActorType.USER.value
        node.updated_by_user_id = actor_user_id
        db.add_all([link, node])
        await db.flush()
    else:
        node = await _create_included_story_node(
            db,
            contract=contract,
            story=story,
            actor_user_id=actor_user_id,
        )
        link = MemoirChapterNodeStory(
            project_id=project_id,
            node_id=node.id,
            story_id=story.id,
            story_role=MemoirChapterNodeStoryRole.PRIMARY.value,
            sort_order=0,
            required=True,
            created_by=MemoirNarrativeActorType.USER.value,
            created_by_user_id=actor_user_id,
        )
        db.add(link)
        await db.flush()

    await recompute_story_node_status(
        db,
        node=node,
        actor_user_id=actor_user_id,
    )
    updated_constraints = type(constraints)(
        **constraints.model_dump(exclude={"include_story_ids", "exclude_story_ids"}),
        include_story_ids=list(dict.fromkeys([*constraints.include_story_ids, story.id])),
        exclude_story_ids=[
            candidate_id
            for candidate_id in constraints.exclude_story_ids
            if candidate_id != story.id
        ],
    )
    contract.constraints_json = updated_constraints.model_dump(mode="json")
    db.add(contract)
    await db.flush()

    await decision_log.record_decision(
        db,
        project_id=project_id,
        target_type=MemoirNarrativeDecisionTargetType.CHAPTER_CONTRACT,
        target_id=contract.id,
        action=MemoirNarrativeDecisionAction.INCLUDE,
        actor_type=MemoirNarrativeActorType.USER,
        actor_user_id=actor_user_id,
        before_state=before_state,
        after_state={
            "included_story_id": story.id,
            "include_story_ids": updated_constraints.include_story_ids,
            "exclude_story_ids": updated_constraints.exclude_story_ids,
            "primary_node_id": node.id,
            "primary_link_id": link.id,
        },
        reason=request.reason,
        idempotency_key=request.idempotency_key,
    )
    return contract


async def exclude_story_from_contract(
    db: AsyncSession,
    *,
    project_id: str,
    contract_id: str,
    story_id: str,
    request: MemoirNarrativeDecisionRequest,
    actor_user_id: str,
) -> MemoirChapterContract:
    contract = await chapter_contracts.load_contract(
        db,
        project_id=project_id,
        contract_id=contract_id,
    )
    existing = await decision_log.existing_decision(
        db,
        project_id=project_id,
        idempotency_key=request.idempotency_key,
        target_type=MemoirNarrativeDecisionTargetType.CHAPTER_CONTRACT,
        target_id=contract.id,
        action=MemoirNarrativeDecisionAction.EXCLUDE,
        actor_type=MemoirNarrativeActorType.USER,
        actor_user_id=actor_user_id,
    )
    if existing is not None:
        if existing.after_state_json.get("excluded_story_id") != story_id:
            raise ValueError("idempotency key was reused for another chapter Story")
        return contract

    _require_draft(contract)
    nodes = await _nodes_for_contract(db, contract=contract)
    nodes_by_id = {node.id: node for node in nodes}
    links = (
        list(
            (
                await db.scalars(
                    select(MemoirChapterNodeStory).where(
                        MemoirChapterNodeStory.project_id == project_id,
                        MemoirChapterNodeStory.node_id.in_(nodes_by_id),
                        MemoirChapterNodeStory.story_id == story_id,
                    )
                )
            ).all()
        )
        if nodes_by_id
        else []
    )
    if not links:
        raise ValueError("Story is not linked to the chapter contract")

    constraints = chapter_contracts.contract_constraints(contract)
    affected_node_ids = sorted({link.node_id for link in links})
    before_state = {
        "story_id": story_id,
        "include_story_ids": constraints.include_story_ids,
        "exclude_story_ids": constraints.exclude_story_ids,
        "node_ids": affected_node_ids,
    }
    for link in links:
        await db.delete(link)
    await db.flush()

    for node_id in affected_node_ids:
        node = nodes_by_id[node_id]
        if await _story_links_for_node(db, node=node):
            await recompute_story_node_status(
                db,
                node=node,
                actor_user_id=actor_user_id,
            )
            continue
        node.required = False
        node.status = MemoirChapterNodeStatus.EXCLUDED.value
        node.updated_by = MemoirNarrativeActorType.USER.value
        node.updated_by_user_id = actor_user_id
        db.add(node)

    updated_constraints = type(constraints)(
        **constraints.model_dump(exclude={"include_story_ids", "exclude_story_ids"}),
        include_story_ids=[
            candidate_id
            for candidate_id in constraints.include_story_ids
            if candidate_id != story_id
        ],
        exclude_story_ids=list(
            dict.fromkeys([*constraints.exclude_story_ids, story_id])
        ),
    )
    contract.constraints_json = updated_constraints.model_dump(mode="json")
    db.add(contract)
    await db.flush()

    await decision_log.record_decision(
        db,
        project_id=project_id,
        target_type=MemoirNarrativeDecisionTargetType.CHAPTER_CONTRACT,
        target_id=contract.id,
        action=MemoirNarrativeDecisionAction.EXCLUDE,
        actor_type=MemoirNarrativeActorType.USER,
        actor_user_id=actor_user_id,
        before_state=before_state,
        after_state={
            "excluded_story_id": story_id,
            "include_story_ids": updated_constraints.include_story_ids,
            "exclude_story_ids": updated_constraints.exclude_story_ids,
            "excluded_node_ids": [
                node_id
                for node_id in affected_node_ids
                if nodes_by_id[node_id].status
                == MemoirChapterNodeStatus.EXCLUDED.value
            ],
        },
        reason=request.reason,
        idempotency_key=request.idempotency_key,
    )
    return contract


def _validate_transition_partition(
    nodes: list[MemoirChapterNode],
    moving_ids: set[str],
) -> None:
    for node in nodes:
        if node.node_type != MemoirChapterNodeType.TRANSITION.value:
            continue
        endpoint_ids = {
            node.transition_from_node_id,
            node.transition_to_node_id,
        } - {None}
        if node.id in moving_ids and not endpoint_ids.issubset(moving_ids):
            raise ValueError("moving a transition requires moving both endpoint nodes")
        if node.id not in moving_ids and endpoint_ids & moving_ids:
            raise ValueError("a remaining transition cannot reference moved nodes")


async def _move_nodes_between_drafts(
    db: AsyncSession,
    *,
    source: MemoirChapterContract,
    target: MemoirChapterContract,
    moving_ids: set[str],
    actor_user_id: str,
) -> tuple[list[MemoirChapterNode], list[MemoirChapterNode]]:
    source_nodes = await _nodes_for_contract(db, contract=source)
    target_nodes = await _nodes_for_contract(db, contract=target)
    if not moving_ids or not moving_ids.issubset({node.id for node in source_nodes}):
        raise ValueError("chapter structure move contains unknown source nodes")
    _validate_transition_partition(source_nodes, moving_ids)
    moving = [node for node in source_nodes if node.id in moving_ids]
    remaining = [node for node in source_nodes if node.id not in moving_ids]
    links_by_node = await chapter_contracts.node_story_links(
        db,
        project_id=source.project_id,
        node_ids=[node.id for node in source_nodes],
    )
    moving_story_ids = {
        link.story_id
        for node in moving
        for link in links_by_node.get(node.id, [])
    }
    remaining_story_ids = {
        link.story_id
        for node in remaining
        for link in links_by_node.get(node.id, [])
    }
    source_constraints = chapter_contracts.contract_constraints(source)
    target_constraints = chapter_contracts.contract_constraints(target)
    conflicting_story_ids = moving_story_ids & set(target_constraints.exclude_story_ids)
    if conflicting_story_ids:
        raise ValueError("target chapter contract explicitly excludes a moved Story")
    moving_primary_story_ids = {
        link.story_id
        for node in moving
        for link in links_by_node.get(node.id, [])
        if link.story_role == MemoirChapterNodeStoryRole.PRIMARY.value
    }
    target_links = await chapter_contracts.node_story_links(
        db,
        project_id=target.project_id,
        node_ids=[node.id for node in target_nodes],
    )
    target_primary_story_ids = {
        link.story_id
        for node in target_nodes
        for link in target_links.get(node.id, [])
        if link.story_role == MemoirChapterNodeStoryRole.PRIMARY.value
    }
    if moving_primary_story_ids & target_primary_story_ids:
        raise ValueError("a Story can only be primary once in one chapter contract")

    for index, node in enumerate(target_nodes):
        node.sort_order = 10000 + index
        db.add(node)
    for index, node in enumerate(source_nodes):
        node.sort_order = 20000 + index
        db.add(node)
    await db.flush()
    for index, node in enumerate(moving):
        node.contract_id = target.id
        node.chapter_id = target.chapter_id
        node.sort_order = 30000 + index
        node.updated_by = MemoirNarrativeActorType.USER.value
        node.updated_by_user_id = actor_user_id
        db.add(node)
    await db.flush()

    target_result = [*target_nodes, *moving]
    for index, node in enumerate(target_result):
        node.sort_order = index
        db.add(node)
    for index, node in enumerate(remaining):
        node.sort_order = index
        db.add(node)
    transferred_required_story_ids = (
        set(source_constraints.include_story_ids)
        & moving_story_ids
        - remaining_story_ids
    )
    if transferred_required_story_ids:
        source.constraints_json = source_constraints.model_copy(
            update={
                "include_story_ids": [
                    story_id
                    for story_id in source_constraints.include_story_ids
                    if story_id not in transferred_required_story_ids
                ]
            }
        ).model_dump(mode="json")
        target.constraints_json = target_constraints.model_copy(
            update={
                "include_story_ids": list(
                    dict.fromkeys(
                        [
                            *target_constraints.include_story_ids,
                            *sorted(transferred_required_story_ids),
                        ]
                    )
                )
            }
        ).model_dump(mode="json")
        db.add(source)
        db.add(target)
    await db.flush()
    return remaining, target_result


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
    target = await chapter_contracts.load_contract(
        db,
        project_id=project_id,
        contract_id=request.target_contract_id,
    )
    if source.id == target.id or source.chapter_id == target.chapter_id:
        raise ValueError("chapter structure split requires two different chapters")
    _require_draft(source)
    _require_draft(target)
    source_nodes = await _nodes_for_contract(db, contract=source)
    moving_ids = set(request.node_ids)
    if moving_ids == {node.id for node in source_nodes}:
        raise ValueError("chapter structure split must leave at least one source node")
    await _move_nodes_between_drafts(
        db,
        source=source,
        target=target,
        moving_ids=moving_ids,
        actor_user_id=actor_user_id,
    )
    return source, target


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
    target = await chapter_contracts.load_contract(
        db,
        project_id=project_id,
        contract_id=request.target_contract_id,
    )
    if source.id == target.id or source.chapter_id == target.chapter_id:
        raise ValueError("chapter structure merge requires two different chapters")
    _require_draft(source)
    _require_draft(target)
    source_nodes = await _nodes_for_contract(db, contract=source)
    if not source_nodes:
        raise ValueError("source chapter structure has no nodes to merge")
    await _move_nodes_between_drafts(
        db,
        source=source,
        target=target,
        moving_ids={node.id for node in source_nodes},
        actor_user_id=actor_user_id,
    )
    source.status = MemoirChapterContractStatus.SUPERSEDED.value
    source.superseded_at = _utcnow()
    db.add(source)
    await db.flush()
    return source, target
