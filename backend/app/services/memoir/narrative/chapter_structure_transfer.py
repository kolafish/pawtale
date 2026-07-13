from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import (
    MemoirChapterContractStatus,
    MemoirChapterNodeStoryRole,
    MemoirChapterNodeType,
    MemoirNarrativeActorType,
)
from app.models.memoir_narrative import MemoirChapterContract, MemoirChapterNode
from app.schemas.memoir_narrative import (
    MemoirChapterContractConstraints,
    MemoirChapterCoreQuestion,
)
from app.services.memoir.narrative import chapter_contracts


def require_draft_contract(contract: MemoirChapterContract) -> None:
    if contract.status != MemoirChapterContractStatus.DRAFT.value:
        raise ValueError("chapter structure changes require a draft contract revision")


async def nodes_for_contract(
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


async def move_nodes_between_drafts(
    db: AsyncSession,
    *,
    source: MemoirChapterContract,
    target: MemoirChapterContract,
    moving_ids: set[str],
    actor_user_id: str,
) -> set[str]:
    require_draft_contract(source)
    require_draft_contract(target)
    source_nodes = await nodes_for_contract(db, contract=source)
    target_nodes = await nodes_for_contract(db, contract=target)
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
    if moving_story_ids & set(target_constraints.exclude_story_ids):
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

    for index, node in enumerate([*target_nodes, *moving]):
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
    return moving_story_ids


def _merged_question_key(key: str, used_keys: set[str]) -> str:
    if key not in used_keys:
        return key
    base = f"merged_{key}"[:80]
    candidate = base
    suffix = 2
    while candidate in used_keys:
        tail = f"_{suffix}"
        candidate = f"{base[: 80 - len(tail)]}{tail}"
        suffix += 1
    return candidate


def merge_contract_requirements(
    *,
    source: MemoirChapterContract,
    target: MemoirChapterContract,
) -> None:
    source_questions = [
        MemoirChapterCoreQuestion.model_validate(item)
        for item in source.core_questions_json
    ]
    target_questions = [
        MemoirChapterCoreQuestion.model_validate(item)
        for item in target.core_questions_json
    ]
    merged_questions = list(target_questions)
    used_keys = {question.key for question in merged_questions}
    used_text = {question.question for question in merged_questions}
    for question in source_questions:
        if question.question in used_text:
            continue
        merged = question.model_copy(
            update={"key": _merged_question_key(question.key, used_keys)}
        )
        merged_questions.append(merged)
        used_keys.add(merged.key)
        used_text.add(merged.question)
    if len(merged_questions) > 20:
        raise ValueError("merged chapter contract has more than 20 core questions")

    source_constraints = chapter_contracts.contract_constraints(source)
    target_constraints = chapter_contracts.contract_constraints(target)
    target.constraints_json = MemoirChapterContractConstraints(
        include_story_ids=list(
            dict.fromkeys(
                [
                    *target_constraints.include_story_ids,
                    *source_constraints.include_story_ids,
                ]
            )
        ),
        exclude_story_ids=list(
            dict.fromkeys(
                [
                    *target_constraints.exclude_story_ids,
                    *source_constraints.exclude_story_ids,
                ]
            )
        ),
        include_topics=list(
            dict.fromkeys(
                [
                    *target_constraints.include_topics,
                    *source_constraints.include_topics,
                ]
            )
        ),
        exclude_topics=list(
            dict.fromkeys(
                [
                    *target_constraints.exclude_topics,
                    *source_constraints.exclude_topics,
                ]
            )
        ),
    ).model_dump(mode="json")
    target.core_questions_json = [
        question.model_dump(mode="json") for question in merged_questions
    ]

    scopes = list(
        dict.fromkeys(
            value.strip()
            for value in (target.time_scope_text, source.time_scope_text)
            if value.strip()
        )
    )
    merged_scope = "；".join(scopes)
    if len(merged_scope) > 1000:
        raise ValueError("merged chapter contract time scope is too long")
    target.time_scope_text = merged_scope
    starts = [value for value in (target.time_start, source.time_start) if value is not None]
    ends = [value for value in (target.time_end, source.time_end) if value is not None]
    target.time_start = min(starts) if starts else None
    target.time_end = max(ends) if ends else None
