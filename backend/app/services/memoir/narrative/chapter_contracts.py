from __future__ import annotations

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import (
    MemoirChapterContractStatus,
    MemoirChapterNodeStatus,
    MemoirChapterNodeStoryRole,
    MemoirChapterNodeType,
    MemoirClaimEvidenceRole,
    MemoirNarrativeActorType,
    MemoirStoryLifecycleStatus,
    MemoirStoryNarrativeRole,
    MemoirStoryPublicationStatus,
    MemoirStoryType,
    MemoirStoryVerificationStatus,
)
from app.domain.memoir.types import MemoirChapterSourceLinkStatus
from app.models.memoir import MemoirBookPlanChapter, MemoirChapterSourceLink
from app.models.memoir_narrative import (
    MemoirChapterContract,
    MemoirChapterNode,
    MemoirChapterNodeStory,
    MemoirClaim,
    MemoirClaimEvidence,
    MemoirStory,
    MemoirStoryClaim,
    MemoirStoryRevision,
)
from app.schemas.memoir_narrative import (
    MemoirChapterContractConstraints,
    MemoirChapterContractResponse,
    MemoirChapterCoreQuestion,
    MemoirChapterNodeResponse,
    MemoirChapterNodeStoryResponse,
)
from app.services.memoir.narrative import story_decisions
from app.services.memoir.narrative.claim_usage_policy import (
    usable_for_publication_filters,
)
from app.services.memoir.narrative.readiness_policy import story_can_support_draft


CORE_STORY_PLACEHOLDER_TITLE = "待补充核心故事"
ANCHOR_STORY_LIMIT = 3

_STORY_TYPE_PRIORITY = {
    MemoirStoryType.LIFE_STAGE.value: 3,
    MemoirStoryType.RELATIONSHIP.value: 2,
    MemoirStoryType.THEME.value: 1,
    MemoirStoryType.EVENT.value: 0,
}


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def load_chapter(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> MemoirBookPlanChapter:
    chapter = await db.scalar(
        select(MemoirBookPlanChapter).where(
            MemoirBookPlanChapter.project_id == project_id,
            MemoirBookPlanChapter.id == chapter_id,
        )
    )
    if chapter is None:
        raise ValueError("chapter does not belong to the project")
    return chapter


async def load_contract(
    db: AsyncSession,
    *,
    project_id: str,
    contract_id: str,
) -> MemoirChapterContract:
    contract = await db.scalar(
        select(MemoirChapterContract).where(
            MemoirChapterContract.project_id == project_id,
            MemoirChapterContract.id == contract_id,
        )
    )
    if contract is None:
        raise ValueError("chapter contract does not belong to the project")
    return contract


async def _contract_nodes(
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


async def node_story_links(
    db: AsyncSession,
    *,
    project_id: str,
    node_ids: list[str],
) -> dict[str, list[MemoirChapterNodeStory]]:
    if not node_ids:
        return {}
    links = list(
        (
            await db.scalars(
                select(MemoirChapterNodeStory)
                .where(
                    MemoirChapterNodeStory.project_id == project_id,
                    MemoirChapterNodeStory.node_id.in_(node_ids),
                )
                .order_by(
                    MemoirChapterNodeStory.node_id,
                    MemoirChapterNodeStory.sort_order,
                    MemoirChapterNodeStory.id,
                )
            )
        ).all()
    )
    by_node: dict[str, list[MemoirChapterNodeStory]] = {}
    for link in links:
        by_node.setdefault(link.node_id, []).append(link)
    return by_node


async def contract_response(
    db: AsyncSession,
    *,
    contract: MemoirChapterContract,
) -> MemoirChapterContractResponse:
    nodes = await _contract_nodes(db, contract=contract)
    links_by_node = await node_story_links(
        db,
        project_id=contract.project_id,
        node_ids=[node.id for node in nodes],
    )
    return MemoirChapterContractResponse(
        id=contract.id,
        project_id=contract.project_id,
        chapter_id=contract.chapter_id,
        version=contract.version,
        objective=contract.objective,
        reader_takeaway=contract.reader_takeaway,
        time_scope_text=contract.time_scope_text,
        time_start=contract.time_start,
        time_end=contract.time_end,
        core_questions=[
            MemoirChapterCoreQuestion.model_validate(item)
            for item in contract.core_questions_json
        ],
        constraints=MemoirChapterContractConstraints.model_validate(
            contract.constraints_json
        ),
        status=MemoirChapterContractStatus(contract.status),
        created_by=contract.created_by,
        created_by_user_id=contract.created_by_user_id,
        confirmed_by_user_id=contract.confirmed_by_user_id,
        confirmed_at=contract.confirmed_at,
        superseded_at=contract.superseded_at,
        nodes=[
            MemoirChapterNodeResponse(
                id=node.id,
                project_id=node.project_id,
                chapter_id=node.chapter_id,
                contract_id=node.contract_id,
                node_type=node.node_type,
                title=node.title,
                purpose=node.purpose,
                sort_order=node.sort_order,
                required=node.required,
                transition_from_node_id=node.transition_from_node_id,
                transition_to_node_id=node.transition_to_node_id,
                status=node.status,
                created_by=node.created_by,
                created_by_user_id=node.created_by_user_id,
                updated_by=node.updated_by,
                updated_by_user_id=node.updated_by_user_id,
                stories=[
                    MemoirChapterNodeStoryResponse.model_validate(link)
                    for link in links_by_node.get(node.id, [])
                ],
                created_at=node.created_at,
                updated_at=node.updated_at,
            )
            for node in nodes
        ],
        created_at=contract.created_at,
        updated_at=contract.updated_at,
    )


def _contract_constraints(
    contract: MemoirChapterContract,
) -> MemoirChapterContractConstraints:
    return MemoirChapterContractConstraints.model_validate(contract.constraints_json)


def _system_direction_contract_id(
    *,
    project_id: str,
    chapter_id: str,
    idempotency_key: str,
) -> str:
    # Keep the original namespace segment stable because contract IDs are persisted.
    return str(
        uuid5(
            NAMESPACE_URL,
            f"pawtale:chapter-contract:{project_id}:{chapter_id}:"
            f"legacy-suggestion:{idempotency_key}",
        )
    )


async def _next_contract_version(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> int:
    latest = await db.scalar(
        select(func.max(MemoirChapterContract.version)).where(
            MemoirChapterContract.project_id == project_id,
            MemoirChapterContract.chapter_id == chapter_id,
        )
    )
    return int(latest or 0) + 1


async def _chapter_story_candidates(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> list[MemoirStory]:
    return list(
        (
            await db.scalars(
                select(MemoirStory)
                .join(
                    MemoirStoryClaim,
                    (MemoirStoryClaim.project_id == MemoirStory.project_id)
                    & (MemoirStoryClaim.story_id == MemoirStory.id),
                )
                .join(
                    MemoirClaimEvidence,
                    (MemoirClaimEvidence.project_id == MemoirStoryClaim.project_id)
                    & (MemoirClaimEvidence.claim_id == MemoirStoryClaim.claim_id),
                )
                .join(
                    MemoirChapterSourceLink,
                    (MemoirChapterSourceLink.project_id == MemoirClaimEvidence.project_id)
                    & (MemoirChapterSourceLink.utterance_id == MemoirClaimEvidence.utterance_id),
                )
                .where(
                    MemoirStory.project_id == project_id,
                    MemoirChapterSourceLink.chapter_id == chapter_id,
                    MemoirChapterSourceLink.status
                    == MemoirChapterSourceLinkStatus.ACTIVE.value,
                    MemoirStory.lifecycle_status == MemoirStoryLifecycleStatus.ACTIVE.value,
                    MemoirStory.publication_status
                    == MemoirStoryPublicationStatus.ELIGIBLE.value,
                    MemoirStory.verification_status
                    != MemoirStoryVerificationStatus.REJECTED.value,
                )
                .distinct()
                .order_by(MemoirStory.updated_at.desc(), MemoirStory.id)
            )
        ).all()
    )


async def _anchor_stories(
    db: AsyncSession,
    *,
    project_id: str,
    candidates: list[MemoirStory],
    preferred_required_story_id: str | None = None,
) -> tuple[list[MemoirStory], MemoirStory | None]:
    if not candidates:
        return [], None
    confirmable_ids = await story_decisions.confirmable_story_ids(
        db,
        project_id=project_id,
        story_ids={story.id for story in candidates},
    )
    shapes = await eligible_story_claim_shapes(
        db,
        project_id=project_id,
        story_ids={story.id for story in candidates},
    )
    shapes_by_story: dict[str, set[tuple[str, bool]]] = {}
    for story_id, narrative_role, is_core in shapes:
        shapes_by_story.setdefault(story_id, set()).add((narrative_role, is_core))

    supported_candidates = [
        story for story in candidates if story.id in shapes_by_story
    ]
    candidate_order = {story.id: index for index, story in enumerate(candidates)}
    ranked = sorted(
        supported_candidates,
        key=lambda story: (
            story.id in confirmable_ids,
            story.verification_status
            == MemoirStoryVerificationStatus.CONFIRMED.value,
            sum(is_core for _, is_core in shapes_by_story[story.id]),
            len({role for role, _ in shapes_by_story[story.id]}),
            _STORY_TYPE_PRIORITY.get(story.story_type, -1),
            -candidate_order[story.id],
        ),
        reverse=True,
    )
    required_story = next(
        (
            story
            for story in ranked
            if story.id == preferred_required_story_id
            and story.id in confirmable_ids
        ),
        None,
    ) or next(
        (story for story in ranked if story.id in confirmable_ids),
        None,
    )
    if required_story is not None:
        ranked = [required_story, *[story for story in ranked if story != required_story]]
    return ranked[:ANCHOR_STORY_LIMIT], required_story


async def eligible_story_claim_shapes(
    db: AsyncSession,
    *,
    project_id: str,
    story_ids: set[str],
) -> set[tuple[str, str, bool]]:
    if not story_ids:
        return set()
    rows = (
        await db.execute(
            select(
                MemoirStoryClaim.story_id,
                MemoirStoryClaim.narrative_role,
                MemoirStoryClaim.is_core,
            )
            .join(
                MemoirStory,
                (MemoirStory.project_id == MemoirStoryClaim.project_id)
                & (MemoirStory.id == MemoirStoryClaim.story_id),
            )
            .join(
                MemoirStoryRevision,
                (MemoirStoryRevision.project_id == MemoirStory.project_id)
                & (MemoirStoryRevision.story_id == MemoirStory.id)
                & (MemoirStoryRevision.story_version == MemoirStory.version),
            )
            .join(
                MemoirClaim,
                (MemoirClaim.project_id == MemoirStoryClaim.project_id)
                & (MemoirClaim.id == MemoirStoryClaim.claim_id),
            )
            .join(
                MemoirClaimEvidence,
                (MemoirClaimEvidence.project_id == MemoirStoryClaim.project_id)
                & (MemoirClaimEvidence.claim_id == MemoirStoryClaim.claim_id),
            )
            .where(
                MemoirStoryClaim.project_id == project_id,
                MemoirStoryClaim.story_id.in_(story_ids),
                MemoirStory.lifecycle_status
                == MemoirStoryLifecycleStatus.ACTIVE.value,
                MemoirStory.publication_status
                == MemoirStoryPublicationStatus.ELIGIBLE.value,
                MemoirStory.verification_status.not_in(
                    {
                        MemoirStoryVerificationStatus.DISPUTED.value,
                        MemoirStoryVerificationStatus.REJECTED.value,
                    }
                ),
                *usable_for_publication_filters(),
                MemoirClaimEvidence.evidence_role
                == MemoirClaimEvidenceRole.SUPPORTS.value,
                or_(
                    func.trim(func.coalesce(MemoirClaimEvidence.quote, "")) != "",
                    func.trim(func.coalesce(MemoirClaimEvidence.evidence_note, ""))
                    != "",
                ),
            )
            .distinct()
        )
    ).all()
    return {
        (str(story_id), str(narrative_role), bool(is_core))
        for story_id, narrative_role, is_core in rows
    }


async def _framing_story_ids(
    db: AsyncSession,
    *,
    project_id: str,
    candidates: list[MemoirStory],
    required_story: MemoirStory | None,
) -> dict[str, str]:
    shapes = await eligible_story_claim_shapes(
        db,
        project_id=project_id,
        story_ids={story.id for story in candidates},
    )

    def first_story_id(*, roles: set[str]) -> str | None:
        return next(
            (
                story.id
                for story in candidates
                if any(
                    story_id == story.id and narrative_role in roles
                    for story_id, narrative_role, _ in shapes
                )
            ),
            None,
        )

    framing: dict[str, str] = {}
    if required_story is not None and any(
        story_id == required_story.id and is_core
        for story_id, _, is_core in shapes
    ):
        framing[MemoirChapterNodeType.OPENING.value] = required_story.id
    reflection_story_id = first_story_id(
        roles={MemoirStoryNarrativeRole.REFLECTION.value}
    )
    if reflection_story_id is not None:
        framing[MemoirChapterNodeType.REFLECTION.value] = reflection_story_id
    closing_story_id = first_story_id(
        roles={
            MemoirStoryNarrativeRole.OUTCOME.value,
            MemoirStoryNarrativeRole.REFLECTION.value,
        }
    )
    if closing_story_id is not None:
        framing[MemoirChapterNodeType.CLOSING.value] = closing_story_id
    return framing


def _core_questions(
    *,
    chapter: MemoirBookPlanChapter,
    candidates: list[MemoirStory],
) -> list[MemoirChapterCoreQuestion]:
    return [
        MemoirChapterCoreQuestion(
            key="chapter_meaning",
            question=f"“{chapter.title}”这段经历中，最重要的变化和意义是什么？",
        ),
        *[
            MemoirChapterCoreQuestion(
                key=f"story_{story.id.replace('-', '')}",
                question=f"“{story.title}”的背景、过程、结果和感受分别是什么？",
            )
            for story in candidates[:5]
        ],
    ]


async def create_system_contract_suggestion(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    idempotency_key: str,
) -> MemoirChapterContract:
    chapter = await load_chapter(db, project_id=project_id, chapter_id=chapter_id)
    current_draft = await db.scalar(
        select(MemoirChapterContract).where(
            MemoirChapterContract.project_id == project_id,
            MemoirChapterContract.chapter_id == chapter_id,
            MemoirChapterContract.status == MemoirChapterContractStatus.DRAFT.value,
        )
    )
    if current_draft is not None:
        return current_draft
    contract_id = _system_direction_contract_id(
        project_id=project_id,
        chapter_id=chapter_id,
        idempotency_key=idempotency_key,
    )
    existing = await db.scalar(
        select(MemoirChapterContract).where(MemoirChapterContract.id == contract_id)
    )
    if existing is not None:
        return existing

    candidates = await _chapter_story_candidates(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    anchor_stories, required_story = await _anchor_stories(
        db,
        project_id=project_id,
        candidates=candidates,
    )
    framing_story_ids = await _framing_story_ids(
        db,
        project_id=project_id,
        candidates=anchor_stories,
        required_story=required_story,
    )
    questions = _core_questions(chapter=chapter, candidates=anchor_stories)
    supported_story_ids = {
        story_id
        for story_id, _, _ in await eligible_story_claim_shapes(
            db,
            project_id=project_id,
            story_ids={story.id for story in anchor_stories},
        )
    }
    contract = MemoirChapterContract(
        id=contract_id,
        project_id=project_id,
        chapter_id=chapter_id,
        version=await _next_contract_version(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
        ),
        objective=f"梳理“{chapter.title}”所覆盖的经历、变化和个人意义。",
        reader_takeaway=f"读者能够理解“{chapter.title}”中的关键经历及其影响。",
        core_questions_json=[
            question.model_dump(mode="json") for question in questions
        ],
        constraints_json=MemoirChapterContractConstraints(
            include_story_ids=[required_story.id] if required_story is not None else []
        ).model_dump(mode="json"),
        status=MemoirChapterContractStatus.DRAFT.value,
        created_by=MemoirNarrativeActorType.SYSTEM.value,
    )
    db.add(contract)
    await db.flush()

    node_specs: list[
        tuple[MemoirChapterNodeType, str, str, bool, MemoirStory | None]
    ] = [
        (
            MemoirChapterNodeType.OPENING,
            "开场",
            f"用与“{chapter.title}”直接相关的具体场景进入本章。",
            True,
            None,
        ),
        *[
            (
                MemoirChapterNodeType.STORY,
                story.title,
                f"完整讲述“{story.title}”在本章中的叙事作用。",
                story.id == required_story.id if required_story is not None else False,
                story,
            )
            for story in anchor_stories
        ],
        (
            MemoirChapterNodeType.REFLECTION,
            "回望",
            "回应本章经历对讲述者性格、关系或选择的影响。",
            True,
            None,
        ),
        (
            MemoirChapterNodeType.CLOSING,
            "收束",
            "回扣章节目标，并自然连接下一段人生经历。",
            False,
            None,
        ),
    ]
    if required_story is None:
        node_specs.insert(
            1,
            (
                MemoirChapterNodeType.STORY,
                CORE_STORY_PLACEHOLDER_TITLE,
                "选择一个有证据支撑的 Story 作为本章叙事核心。",
                True,
                None,
            ),
        )
    created_nodes: list[MemoirChapterNode] = []
    for index, (node_type, title, purpose, required, story) in enumerate(node_specs):
        node = MemoirChapterNode(
            project_id=project_id,
            chapter_id=chapter_id,
            contract_id=contract.id,
            node_type=node_type.value,
            title=title,
            purpose=purpose,
            sort_order=index,
            required=required,
            status=(
                MemoirChapterNodeStatus.READY.value
                if (
                    story is not None
                    and story_can_support_draft(story)
                    and story.id in supported_story_ids
                )
                else (
                    MemoirChapterNodeStatus.NEEDS_WORK.value
                    if node_type == MemoirChapterNodeType.STORY
                    else MemoirChapterNodeStatus.PLANNED.value
                )
            ),
            created_by=MemoirNarrativeActorType.SYSTEM.value,
            updated_by=MemoirNarrativeActorType.SYSTEM.value,
        )
        db.add(node)
        await db.flush()
        created_nodes.append(node)
        if story is not None:
            db.add(
                MemoirChapterNodeStory(
                    project_id=project_id,
                    node_id=node.id,
                    story_id=story.id,
                    story_role=MemoirChapterNodeStoryRole.PRIMARY.value,
                    sort_order=0,
                    required=required,
                    created_by=MemoirNarrativeActorType.SYSTEM.value,
                )
            )
    for node in created_nodes:
        framing_story_id = framing_story_ids.get(node.node_type)
        if framing_story_id is not None:
            db.add(
                MemoirChapterNodeStory(
                    project_id=project_id,
                    node_id=node.id,
                    story_id=framing_story_id,
                    story_role=MemoirChapterNodeStoryRole.SUPPORTING.value,
                    sort_order=0,
                    required=False,
                    created_by=MemoirNarrativeActorType.SYSTEM.value,
                )
            )
    await db.flush()
    return contract


async def _sync_system_draft_contract(
    db: AsyncSession,
    *,
    contract: MemoirChapterContract,
) -> bool:
    if (
        contract.status != MemoirChapterContractStatus.DRAFT.value
        or contract.created_by != MemoirNarrativeActorType.SYSTEM.value
    ):
        return False

    nodes = await _contract_nodes(db, contract=contract)
    links_by_node = await node_story_links(
        db,
        project_id=contract.project_id,
        node_ids=[node.id for node in nodes],
    )
    links = [link for node_links in links_by_node.values() for link in node_links]
    constraints = _contract_constraints(contract)
    if (
        any(
            node.created_by != MemoirNarrativeActorType.SYSTEM.value
            or node.updated_by != MemoirNarrativeActorType.SYSTEM.value
            for node in nodes
        )
        or any(
            link.created_by != MemoirNarrativeActorType.SYSTEM.value for link in links
        )
        or len(constraints.include_story_ids) > 1
        or constraints.exclude_story_ids
        or constraints.include_topics
        or constraints.exclude_topics
    ):
        return False

    story_nodes = [
        node for node in nodes if node.node_type == MemoirChapterNodeType.STORY.value
    ]
    framing_nodes = [
        node
        for node in nodes
        if node.node_type
        in {
            MemoirChapterNodeType.OPENING.value,
            MemoirChapterNodeType.REFLECTION.value,
            MemoirChapterNodeType.CLOSING.value,
        }
    ]
    placeholder_nodes = [
        node
        for node in story_nodes
        if node.title == CORE_STORY_PLACEHOLDER_TITLE
        and not links_by_node.get(node.id)
    ]
    if len(placeholder_nodes) > 1 or any(
        len(links_by_node.get(node.id, [])) != 1
        or links_by_node[node.id][0].story_role
        != MemoirChapterNodeStoryRole.PRIMARY.value
        for node in story_nodes
        if node not in placeholder_nodes
    ):
        return False
    story_node_story_ids = {
        link.story_id
        for node in story_nodes
        for link in links_by_node.get(node.id, [])
    }
    if any(
        len(links_by_node.get(node.id, [])) > 1
        or any(
            link.story_role != MemoirChapterNodeStoryRole.SUPPORTING.value
            or link.required
            or link.story_id not in story_node_story_ids
            for link in links_by_node.get(node.id, [])
        )
        for node in framing_nodes
    ):
        return False

    candidates = await _chapter_story_candidates(
        db,
        project_id=contract.project_id,
        chapter_id=contract.chapter_id,
    )
    preferred_required_story_id = (
        constraints.include_story_ids[0] if constraints.include_story_ids else None
    )
    anchor_stories, required_story = await _anchor_stories(
        db,
        project_id=contract.project_id,
        candidates=candidates,
        preferred_required_story_id=preferred_required_story_id,
    )
    framing_story_ids = await _framing_story_ids(
        db,
        project_id=contract.project_id,
        candidates=anchor_stories,
        required_story=required_story,
    )
    supported_story_ids = {
        story_id
        for story_id, _, _ in await eligible_story_claim_shapes(
            db,
            project_id=contract.project_id,
            story_ids={story.id for story in anchor_stories},
        )
    }
    required_story_id = required_story.id if required_story is not None else None
    desired_include_ids = [required_story_id] if required_story_id else []
    changed = constraints.include_story_ids != desired_include_ids
    if changed:
        contract.constraints_json = constraints.model_copy(
            update={"include_story_ids": desired_include_ids}
        ).model_dump(mode="json")
        db.add(contract)

    placeholder = placeholder_nodes[0] if placeholder_nodes else None
    existing_story_ids = [
        links_by_node[node.id][0].story_id
        for node in story_nodes
        if node is not placeholder
    ]
    desired_story_ids = [story.id for story in anchor_stories]
    story_shape_changed = existing_story_ids != desired_story_ids
    if story_shape_changed:
        insertion_index = next(
            (
                index
                for index, node in enumerate(nodes)
                if node.node_type == MemoirChapterNodeType.STORY.value
            ),
            min(1, len(nodes)),
        )
        for node in story_nodes:
            nodes.remove(node)
            await db.delete(node)
        await db.flush()

        story_specs: list[tuple[str, str, bool, MemoirStory | None]] = []
        if required_story_id is None:
            story_specs.append(
                (
                    CORE_STORY_PLACEHOLDER_TITLE,
                    "选择一个有证据支撑的 Story 作为本章叙事核心。",
                    True,
                    None,
                )
            )
        story_specs.extend(
            (
                story.title,
                f"完整讲述“{story.title}”在本章中的叙事作用。",
                story.id == required_story_id,
                story,
            )
            for story in anchor_stories
        )
        for offset, (title, purpose, required, story) in enumerate(story_specs):
            node = MemoirChapterNode(
                project_id=contract.project_id,
                chapter_id=contract.chapter_id,
                contract_id=contract.id,
                node_type=MemoirChapterNodeType.STORY.value,
                title=title,
                purpose=purpose,
                sort_order=20000 + offset,
                required=required,
                status=(
                    MemoirChapterNodeStatus.NEEDS_WORK.value
                    if story is None
                    else (
                        MemoirChapterNodeStatus.READY.value
                        if story_can_support_draft(story)
                        and story.id in supported_story_ids
                        else MemoirChapterNodeStatus.NEEDS_WORK.value
                    )
                ),
                created_by=MemoirNarrativeActorType.SYSTEM.value,
                updated_by=MemoirNarrativeActorType.SYSTEM.value,
            )
            db.add(node)
            await db.flush()
            nodes.insert(insertion_index + offset, node)
            if story is not None:
                db.add(
                    MemoirChapterNodeStory(
                        project_id=contract.project_id,
                        node_id=node.id,
                        story_id=story.id,
                        story_role=MemoirChapterNodeStoryRole.PRIMARY.value,
                        sort_order=0,
                        required=required,
                        created_by=MemoirNarrativeActorType.SYSTEM.value,
                    )
                )
        changed = True
    else:
        stories_by_id = {story.id: story for story in anchor_stories}
        for node in story_nodes:
            node_links = links_by_node.get(node.id, [])
            story = (
                stories_by_id.get(node_links[0].story_id) if node_links else None
            )
            node_required = story is None or story.id == required_story_id
            expected_title = (
                CORE_STORY_PLACEHOLDER_TITLE
                if story is None
                else story.title
            )
            expected_purpose = (
                "选择一个有证据支撑的 Story 作为本章叙事核心。"
                if story is None
                else f"完整讲述“{story.title}”在本章中的叙事作用。"
            )
            expected_status = (
                MemoirChapterNodeStatus.NEEDS_WORK.value
                if story is None
                else (
                    MemoirChapterNodeStatus.READY.value
                    if story_can_support_draft(story)
                    and story.id in supported_story_ids
                    else MemoirChapterNodeStatus.NEEDS_WORK.value
                )
            )
            for attribute, expected in (
                ("title", expected_title),
                ("purpose", expected_purpose),
                ("required", node_required),
                ("status", expected_status),
            ):
                if getattr(node, attribute) != expected:
                    setattr(node, attribute, expected)
                    db.add(node)
                    changed = True
            for link in node_links:
                link_required = link.story_id == required_story_id
                if link.required != link_required:
                    link.required = link_required
                    db.add(link)
                    changed = True

        if required_story_id is None and placeholder is None:
            insertion_index = next(
                (
                    index
                    for index, node in enumerate(nodes)
                    if node.node_type == MemoirChapterNodeType.STORY.value
                ),
                min(1, len(nodes)),
            )
            placeholder = MemoirChapterNode(
                project_id=contract.project_id,
                chapter_id=contract.chapter_id,
                contract_id=contract.id,
                node_type=MemoirChapterNodeType.STORY.value,
                title=CORE_STORY_PLACEHOLDER_TITLE,
                purpose="选择一个有证据支撑的 Story 作为本章叙事核心。",
                sort_order=20000,
                required=True,
                status=MemoirChapterNodeStatus.NEEDS_WORK.value,
                created_by=MemoirNarrativeActorType.SYSTEM.value,
                updated_by=MemoirNarrativeActorType.SYSTEM.value,
            )
            nodes.insert(insertion_index, placeholder)
            db.add(placeholder)
            changed = True
        elif required_story_id is not None and placeholder is not None:
            nodes.remove(placeholder)
            await db.delete(placeholder)
            changed = True

    for node in framing_nodes:
        node_links = links_by_node.get(node.id, [])
        expected_story_id = framing_story_ids.get(node.node_type)
        expected_link = next(
            (
                link
                for link in node_links
                if link.story_id == expected_story_id
                and link.story_role == MemoirChapterNodeStoryRole.SUPPORTING.value
                and not link.required
            ),
            None,
        )
        if expected_story_id is not None and expected_link is not None:
            continue
        for link in node_links:
            await db.delete(link)
            changed = True
        if expected_story_id is not None:
            db.add(
                MemoirChapterNodeStory(
                    project_id=contract.project_id,
                    node_id=node.id,
                    story_id=expected_story_id,
                    story_role=MemoirChapterNodeStoryRole.SUPPORTING.value,
                    sort_order=0,
                    required=False,
                    created_by=MemoirNarrativeActorType.SYSTEM.value,
                )
            )
            changed = True

    chapter = await load_chapter(
        db,
        project_id=contract.project_id,
        chapter_id=contract.chapter_id,
    )
    desired_questions = [
        question.model_dump(mode="json")
        for question in _core_questions(
            chapter=chapter,
            candidates=anchor_stories,
        )
    ]
    if contract.core_questions_json != desired_questions:
        contract.core_questions_json = desired_questions
        db.add(contract)
        changed = True

    if not changed:
        return False

    for index, node in enumerate(nodes):
        node.sort_order = 10000 + index
        db.add(node)
    await db.flush()
    for index, node in enumerate(nodes):
        node.sort_order = index
        db.add(node)
    await db.flush()
    return True


async def sync_system_drafts_for_utterance(
    db: AsyncSession,
    *,
    project_id: str,
    utterance_id: str,
) -> tuple[str, ...]:
    contracts = list(
        (
            await db.scalars(
                select(MemoirChapterContract)
                .join(
                    MemoirChapterSourceLink,
                    (MemoirChapterSourceLink.project_id == MemoirChapterContract.project_id)
                    & (
                        MemoirChapterSourceLink.chapter_id
                        == MemoirChapterContract.chapter_id
                    ),
                )
                .where(
                    MemoirChapterContract.project_id == project_id,
                    MemoirChapterContract.status
                    == MemoirChapterContractStatus.DRAFT.value,
                    MemoirChapterContract.created_by
                    == MemoirNarrativeActorType.SYSTEM.value,
                    MemoirChapterSourceLink.utterance_id == utterance_id,
                    MemoirChapterSourceLink.status
                    == MemoirChapterSourceLinkStatus.ACTIVE.value,
                )
                .distinct()
                .order_by(MemoirChapterContract.chapter_id)
            )
        ).all()
    )
    updated_contract_ids: list[str] = []
    for contract in contracts:
        if await _sync_system_draft_contract(db, contract=contract):
            updated_contract_ids.append(contract.id)
    return tuple(updated_contract_ids)


async def confirm_contract(
    db: AsyncSession,
    *,
    project_id: str,
    contract_id: str,
    actor_user_id: str,
) -> MemoirChapterContract:
    contract = await load_contract(
        db,
        project_id=project_id,
        contract_id=contract_id,
    )
    if contract.status == MemoirChapterContractStatus.CONFIRMED.value:
        return contract
    if contract.status != MemoirChapterContractStatus.DRAFT.value:
        raise ValueError("only a draft chapter contract can be confirmed")
    nodes = await _contract_nodes(db, contract=contract)
    if not nodes:
        raise ValueError("chapter contract requires at least one structure node")

    from app.services.memoir.narrative import chapter_nodes

    for node in nodes:
        if node.node_type == MemoirChapterNodeType.STORY.value:
            await chapter_nodes.recompute_story_node_status(
                db,
                node=node,
                actor_user_id=actor_user_id,
            )
            continue
        if node.required and node.status == MemoirChapterNodeStatus.PLANNED.value:
            node.status = MemoirChapterNodeStatus.READY.value
            node.updated_by = MemoirNarrativeActorType.USER.value
            node.updated_by_user_id = actor_user_id
            db.add(node)
    await db.flush()

    previous = await db.scalar(
        select(MemoirChapterContract).where(
            MemoirChapterContract.project_id == project_id,
            MemoirChapterContract.chapter_id == contract.chapter_id,
            MemoirChapterContract.status == MemoirChapterContractStatus.CONFIRMED.value,
            MemoirChapterContract.id != contract.id,
        )
    )
    now = _utcnow()
    if previous is not None:
        previous.status = MemoirChapterContractStatus.SUPERSEDED.value
        previous.superseded_at = now
        db.add(previous)
        await db.flush()
    contract.status = MemoirChapterContractStatus.CONFIRMED.value
    contract.confirmed_by_user_id = actor_user_id
    contract.confirmed_at = now
    db.add(contract)
    await db.flush()
    return contract
