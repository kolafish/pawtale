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
    MemoirClaimPublicationStatus,
    MemoirClaimVerificationStatus,
    MemoirNarrativeActorType,
    MemoirStoryCompletionStatus,
    MemoirStoryLifecycleStatus,
    MemoirStoryNarrativeRole,
    MemoirStoryPublicationStatus,
    MemoirStoryVerificationStatus,
)
from app.domain.memoir.types import (
    MemoirBookPlanChapterStatus,
    MemoirChapterSourceLinkStatus,
)
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
    MemoirChapterContractDraftRequest,
    MemoirChapterContractResponse,
    MemoirChapterContractValidationIssue,
    MemoirChapterContractValidationResponse,
    MemoirChapterCoreQuestion,
    MemoirChapterNodeResponse,
    MemoirChapterNodeStoryResponse,
    MemoirChapterStructureResponse,
)
from app.services.memoir.narrative import story_decisions


LEGACY_SUGGESTION_IDEMPOTENCY_KEY = "chapter-structure-backfill-v1"
LEGACY_CORE_STORY_PLACEHOLDER_TITLE = "待补充核心故事"


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
    if chapter.status != MemoirBookPlanChapterStatus.ACTIVE.value:
        raise ValueError("chapter is archived")
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


async def current_contract_for_chapter(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> MemoirChapterContract | None:
    return await db.scalar(
        select(MemoirChapterContract)
        .where(
            MemoirChapterContract.project_id == project_id,
            MemoirChapterContract.chapter_id == chapter_id,
            MemoirChapterContract.status.in_(
                {
                    MemoirChapterContractStatus.DRAFT.value,
                    MemoirChapterContractStatus.CONFIRMED.value,
                }
            ),
        )
        .order_by(
            (MemoirChapterContract.status == MemoirChapterContractStatus.DRAFT.value).desc(),
            MemoirChapterContract.version.desc(),
        )
    )


async def ensure_draft_contract_revision(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    actor_user_id: str,
    idempotency_key: str,
) -> MemoirChapterContract:
    await load_chapter(db, project_id=project_id, chapter_id=chapter_id)
    current = await current_contract_for_chapter(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    if current is None:
        return await create_legacy_contract_suggestion(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
            idempotency_key=f"{idempotency_key}:suggestion",
        )
    if current.status == MemoirChapterContractStatus.DRAFT.value:
        return current
    return await create_contract_revision(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        request=MemoirChapterContractDraftRequest(
            idempotency_key=idempotency_key,
            objective=current.objective,
            reader_takeaway=current.reader_takeaway,
            time_scope_text=current.time_scope_text,
            time_start=current.time_start,
            time_end=current.time_end,
            core_questions=[
                MemoirChapterCoreQuestion.model_validate(item)
                for item in current.core_questions_json
            ],
            constraints=contract_constraints(current),
            copy_from_contract_id=current.id,
            copy_nodes=True,
        ),
        actor_user_id=actor_user_id,
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


async def chapter_structure_response(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
) -> MemoirChapterStructureResponse:
    chapter = await load_chapter(db, project_id=project_id, chapter_id=chapter_id)
    contracts = list(
        (
            await db.scalars(
                select(MemoirChapterContract)
                .where(
                    MemoirChapterContract.project_id == project_id,
                    MemoirChapterContract.chapter_id == chapter_id,
                )
                .order_by(MemoirChapterContract.version.desc())
            )
        ).all()
    )
    return MemoirChapterStructureResponse(
        chapter_id=chapter.id,
        chapter_title=chapter.title,
        contracts=[
            await contract_response(db, contract=contract) for contract in contracts
        ],
    )


async def _validate_constraint_stories(
    db: AsyncSession,
    *,
    project_id: str,
    constraints: MemoirChapterContractConstraints,
) -> None:
    requested_ids = set(constraints.include_story_ids) | set(constraints.exclude_story_ids)
    if not requested_ids:
        return
    stories = list(
        (
            await db.scalars(
                select(MemoirStory).where(
                    MemoirStory.project_id == project_id,
                    MemoirStory.id.in_(requested_ids),
                )
            )
        ).all()
    )
    by_id = {story.id: story for story in stories}
    if set(by_id) != requested_ids:
        raise ValueError("chapter contract stories must belong to the same project")
    for story_id in constraints.include_story_ids:
        story = by_id[story_id]
        if (
            story.lifecycle_status != MemoirStoryLifecycleStatus.ACTIVE.value
            or story.publication_status != MemoirStoryPublicationStatus.ELIGIBLE.value
            or story.verification_status == MemoirStoryVerificationStatus.REJECTED.value
        ):
            raise ValueError("included chapter contract stories must be active and eligible")


def contract_constraints(
    contract: MemoirChapterContract,
) -> MemoirChapterContractConstraints:
    return MemoirChapterContractConstraints.model_validate(contract.constraints_json)


def _contract_id(
    *,
    project_id: str,
    chapter_id: str,
    actor_key: str,
    idempotency_key: str,
) -> str:
    return str(
        uuid5(
            NAMESPACE_URL,
            f"pawtale:chapter-contract:{project_id}:{chapter_id}:{actor_key}:{idempotency_key}",
        )
    )


def legacy_contract_suggestion_id(*, project_id: str, chapter_id: str) -> str:
    return _contract_id(
        project_id=project_id,
        chapter_id=chapter_id,
        actor_key="legacy-suggestion",
        idempotency_key=LEGACY_SUGGESTION_IDEMPOTENCY_KEY,
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


async def _supersede_current_draft(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    except_contract_id: str | None = None,
) -> None:
    current = await db.scalar(
        select(MemoirChapterContract).where(
            MemoirChapterContract.project_id == project_id,
            MemoirChapterContract.chapter_id == chapter_id,
            MemoirChapterContract.status == MemoirChapterContractStatus.DRAFT.value,
        )
    )
    if current is None or current.id == except_contract_id:
        return
    current.status = MemoirChapterContractStatus.SUPERSEDED.value
    current.superseded_at = _utcnow()
    db.add(current)
    await db.flush()


def _contract_payload_matches(
    contract: MemoirChapterContract,
    request: MemoirChapterContractDraftRequest,
) -> bool:
    return all(
        (
            contract.objective == request.objective,
            contract.reader_takeaway == request.reader_takeaway,
            contract.time_scope_text == request.time_scope_text,
            contract.time_start == request.time_start,
            contract.time_end == request.time_end,
            contract.core_questions_json
            == [question.model_dump(mode="json") for question in request.core_questions],
            contract.constraints_json == request.constraints.model_dump(mode="json"),
        )
    )


async def _copy_contract_nodes(
    db: AsyncSession,
    *,
    source: MemoirChapterContract,
    target: MemoirChapterContract,
    actor_user_id: str,
) -> None:
    source_nodes = await _contract_nodes(db, contract=source)
    if not source_nodes:
        return
    links_by_node = await node_story_links(
        db,
        project_id=source.project_id,
        node_ids=[node.id for node in source_nodes],
    )
    id_map = {
        node.id: str(uuid5(NAMESPACE_URL, f"pawtale:chapter-node-copy:{target.id}:{node.id}"))
        for node in source_nodes
    }
    non_transitions = [
        node for node in source_nodes if node.node_type != MemoirChapterNodeType.TRANSITION.value
    ]
    transitions = [
        node for node in source_nodes if node.node_type == MemoirChapterNodeType.TRANSITION.value
    ]
    for node in [*non_transitions, *transitions]:
        cloned = MemoirChapterNode(
            id=id_map[node.id],
            project_id=target.project_id,
            chapter_id=target.chapter_id,
            contract_id=target.id,
            node_type=node.node_type,
            title=node.title,
            purpose=node.purpose,
            sort_order=node.sort_order,
            required=node.required,
            transition_from_node_id=(
                id_map[node.transition_from_node_id]
                if node.transition_from_node_id
                else None
            ),
            transition_to_node_id=(
                id_map[node.transition_to_node_id]
                if node.transition_to_node_id
                else None
            ),
            status=node.status,
            created_by=MemoirNarrativeActorType.USER.value,
            created_by_user_id=actor_user_id,
            updated_by=MemoirNarrativeActorType.USER.value,
            updated_by_user_id=actor_user_id,
        )
        db.add(cloned)
        await db.flush()
        for link in links_by_node.get(node.id, []):
            db.add(
                MemoirChapterNodeStory(
                    project_id=target.project_id,
                    node_id=cloned.id,
                    story_id=link.story_id,
                    story_role=link.story_role,
                    sort_order=link.sort_order,
                    required=link.required,
                    created_by=MemoirNarrativeActorType.USER.value,
                    created_by_user_id=actor_user_id,
                )
            )
    await db.flush()


async def create_contract_revision(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    request: MemoirChapterContractDraftRequest,
    actor_user_id: str,
) -> MemoirChapterContract:
    await load_chapter(db, project_id=project_id, chapter_id=chapter_id)
    await _validate_constraint_stories(
        db,
        project_id=project_id,
        constraints=request.constraints,
    )
    contract_id = _contract_id(
        project_id=project_id,
        chapter_id=chapter_id,
        actor_key=actor_user_id,
        idempotency_key=request.idempotency_key,
    )
    existing = await db.scalar(
        select(MemoirChapterContract).where(MemoirChapterContract.id == contract_id)
    )
    if existing is not None:
        if existing.project_id != project_id or existing.chapter_id != chapter_id:
            raise ValueError("chapter contract idempotency key belongs to another chapter")
        if not _contract_payload_matches(existing, request):
            raise ValueError("chapter contract idempotency key was reused with another payload")
        return existing

    source = None
    if request.copy_from_contract_id:
        source = await load_contract(
            db,
            project_id=project_id,
            contract_id=request.copy_from_contract_id,
        )
        if source.chapter_id != chapter_id:
            raise ValueError("chapter contract copy source belongs to another chapter")

    await _supersede_current_draft(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    contract = MemoirChapterContract(
        id=contract_id,
        project_id=project_id,
        chapter_id=chapter_id,
        version=await _next_contract_version(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
        ),
        objective=request.objective,
        reader_takeaway=request.reader_takeaway,
        time_scope_text=request.time_scope_text,
        time_start=request.time_start,
        time_end=request.time_end,
        core_questions_json=[
            question.model_dump(mode="json") for question in request.core_questions
        ],
        constraints_json=request.constraints.model_dump(mode="json"),
        status=MemoirChapterContractStatus.DRAFT.value,
        created_by=MemoirNarrativeActorType.USER.value,
        created_by_user_id=actor_user_id,
    )
    db.add(contract)
    await db.flush()
    if source is not None and request.copy_nodes:
        await _copy_contract_nodes(
            db,
            source=source,
            target=contract,
            actor_user_id=actor_user_id,
        )
    return contract


def story_is_ready(story: MemoirStory) -> bool:
    return (
        story.lifecycle_status == MemoirStoryLifecycleStatus.ACTIVE.value
        and story.publication_status == MemoirStoryPublicationStatus.ELIGIBLE.value
        and story.verification_status == MemoirStoryVerificationStatus.CONFIRMED.value
        and story.completion_status
        in {
            MemoirStoryCompletionStatus.SUFFICIENT.value,
            MemoirStoryCompletionStatus.ACCEPTED_INCOMPLETE.value,
        }
    )


async def legacy_story_candidates(
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
                .limit(20)
            )
        ).all()
    )


async def _required_legacy_story(
    db: AsyncSession,
    *,
    project_id: str,
    candidates: list[MemoirStory],
) -> MemoirStory | None:
    confirmable_ids = await story_decisions.confirmable_story_ids(
        db,
        project_id=project_id,
        story_ids={story.id for story in candidates},
    )
    return next(
        (story for story in candidates if story.id in confirmable_ids),
        None,
    )


async def _eligible_story_claim_shapes(
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
                MemoirClaim.publication_status
                == MemoirClaimPublicationStatus.ELIGIBLE.value,
                MemoirClaim.verification_status.not_in(
                    {
                        MemoirClaimVerificationStatus.DISPUTED.value,
                        MemoirClaimVerificationStatus.REJECTED.value,
                    }
                ),
                MemoirClaim.superseded_by_claim_id.is_(None),
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


async def _legacy_framing_story_ids(
    db: AsyncSession,
    *,
    project_id: str,
    candidates: list[MemoirStory],
    required_story: MemoirStory | None,
) -> dict[str, str]:
    shapes = await _eligible_story_claim_shapes(
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


def _framing_node_has_supported_claim(
    *,
    node_type: str,
    story_ids: set[str],
    shapes: set[tuple[str, str, bool]],
) -> bool:
    if node_type == MemoirChapterNodeType.OPENING.value:
        return any(
            story_id in story_ids and is_core
            for story_id, _, is_core in shapes
        )
    if node_type == MemoirChapterNodeType.REFLECTION.value:
        roles = {MemoirStoryNarrativeRole.REFLECTION.value}
    elif node_type == MemoirChapterNodeType.CLOSING.value:
        roles = {
            MemoirStoryNarrativeRole.OUTCOME.value,
            MemoirStoryNarrativeRole.REFLECTION.value,
        }
    else:
        return True
    return any(
        story_id in story_ids and narrative_role in roles
        for story_id, narrative_role, _ in shapes
    )


def _legacy_core_questions(
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


async def create_legacy_contract_suggestion(
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
    contract_id = _contract_id(
        project_id=project_id,
        chapter_id=chapter_id,
        actor_key="legacy-suggestion",
        idempotency_key=idempotency_key,
    )
    existing = await db.scalar(
        select(MemoirChapterContract).where(MemoirChapterContract.id == contract_id)
    )
    if existing is not None:
        return existing

    candidates = await legacy_story_candidates(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    required_story = await _required_legacy_story(
        db,
        project_id=project_id,
        candidates=candidates,
    )
    framing_story_ids = await _legacy_framing_story_ids(
        db,
        project_id=project_id,
        candidates=candidates,
        required_story=required_story,
    )
    questions = _legacy_core_questions(chapter=chapter, candidates=candidates)
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
            for story in candidates
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
                LEGACY_CORE_STORY_PLACEHOLDER_TITLE,
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
                if story is not None and story_is_ready(story)
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


async def repair_legacy_contract_suggestion(
    db: AsyncSession,
    *,
    contract: MemoirChapterContract,
) -> str:
    if (
        contract.id
        != legacy_contract_suggestion_id(
            project_id=contract.project_id,
            chapter_id=contract.chapter_id,
        )
        or contract.status != MemoirChapterContractStatus.DRAFT.value
        or contract.created_by != MemoirNarrativeActorType.SYSTEM.value
    ):
        return "not_applicable"

    nodes = await _contract_nodes(db, contract=contract)
    links_by_node = await node_story_links(
        db,
        project_id=contract.project_id,
        node_ids=[node.id for node in nodes],
    )
    links = [link for node_links in links_by_node.values() for link in node_links]
    constraints = contract_constraints(contract)
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
        return "skipped_modified"

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
        if node.title == LEGACY_CORE_STORY_PLACEHOLDER_TITLE
        and not links_by_node.get(node.id)
    ]
    if len(placeholder_nodes) > 1 or any(
        len(links_by_node.get(node.id, [])) != 1
        for node in story_nodes
        if node not in placeholder_nodes
    ):
        return "skipped_modified"
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
        return "skipped_modified"

    candidates = await legacy_story_candidates(
        db,
        project_id=contract.project_id,
        chapter_id=contract.chapter_id,
    )
    required_story = await _required_legacy_story(
        db,
        project_id=contract.project_id,
        candidates=[story for story in candidates if story.id in story_node_story_ids],
    )
    framing_story_ids = await _legacy_framing_story_ids(
        db,
        project_id=contract.project_id,
        candidates=candidates,
        required_story=required_story,
    )
    required_story_id = required_story.id if required_story is not None else None
    desired_include_ids = [required_story_id] if required_story_id else []
    changed = constraints.include_story_ids != desired_include_ids
    if changed:
        contract.constraints_json = constraints.model_copy(
            update={"include_story_ids": desired_include_ids}
        ).model_dump(mode="json")
        db.add(contract)

    placeholder = placeholder_nodes[0] if placeholder_nodes else None
    for node in story_nodes:
        node_links = links_by_node.get(node.id, [])
        node_required = (node is placeholder and required_story_id is None) or any(
            link.story_id == required_story_id for link in node_links
        )
        if node.required != node_required:
            node.required = node_required
            db.add(node)
            changed = True
        for link in node_links:
            link_required = link.story_id == required_story_id
            if link.required != link_required:
                link.required = link_required
                db.add(link)
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
            title=LEGACY_CORE_STORY_PLACEHOLDER_TITLE,
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

    if not changed:
        return "unchanged"

    for index, node in enumerate(nodes):
        if node is placeholder and required_story_id is not None:
            continue
        node.sort_order = 10000 + index
        db.add(node)
    await db.flush()
    for index, node in enumerate(nodes):
        node.sort_order = index
        db.add(node)
    chapter = await load_chapter(
        db,
        project_id=contract.project_id,
        chapter_id=contract.chapter_id,
    )
    contract.core_questions_json = [
        question.model_dump(mode="json")
        for question in _legacy_core_questions(chapter=chapter, candidates=candidates)
    ]
    db.add(contract)
    await db.flush()
    return "repaired"


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


async def validate_contract_for_generation(
    db: AsyncSession,
    *,
    project_id: str,
    contract_id: str,
) -> MemoirChapterContractValidationResponse:
    contract = await load_contract(
        db,
        project_id=project_id,
        contract_id=contract_id,
    )
    nodes = await _contract_nodes(db, contract=contract)
    links_by_node = await node_story_links(
        db,
        project_id=project_id,
        node_ids=[node.id for node in nodes],
    )
    story_ids = {
        link.story_id for links in links_by_node.values() for link in links
    }
    stories = {
        story.id: story
        for story in (
            await db.scalars(
                select(MemoirStory).where(
                    MemoirStory.project_id == project_id,
                    MemoirStory.id.in_(story_ids),
                )
            )
        ).all()
    } if story_ids else {}
    eligible_claim_shapes = await _eligible_story_claim_shapes(
        db,
        project_id=project_id,
        story_ids=set(stories),
    )

    issues: list[MemoirChapterContractValidationIssue] = []
    if contract.status != MemoirChapterContractStatus.CONFIRMED.value:
        issues.append(
            MemoirChapterContractValidationIssue(
                code="contract_not_confirmed",
                message="章节契约尚未确认。",
            )
        )
    if not nodes:
        issues.append(
            MemoirChapterContractValidationIssue(
                code="chapter_nodes_missing",
                message="章节至少需要一个结构节点。",
            )
        )

    required_nodes = [node for node in nodes if node.required]
    ready_required_nodes = [
        node
        for node in required_nodes
        if node.status
        in {
            MemoirChapterNodeStatus.READY.value,
            MemoirChapterNodeStatus.EXCLUDED.value,
        }
    ]
    for node in required_nodes:
        if node.status not in {
            MemoirChapterNodeStatus.READY.value,
            MemoirChapterNodeStatus.EXCLUDED.value,
        }:
            issues.append(
                MemoirChapterContractValidationIssue(
                    code="required_node_not_ready",
                    message=f"必要节点“{node.title}”尚未准备完成。",
                    node_id=node.id,
                )
            )
        if node.node_type != MemoirChapterNodeType.STORY.value:
            continue
        links = links_by_node.get(node.id, [])
        if not links:
            issues.append(
                MemoirChapterContractValidationIssue(
                    code="story_node_has_no_story",
                    message=f"故事节点“{node.title}”尚未关联 Story。",
                    node_id=node.id,
                )
            )
        required_links = [link for link in links if link.required] or links
        for link in required_links:
            story = stories.get(link.story_id)
            if story is None or not story_is_ready(story):
                issues.append(
                    MemoirChapterContractValidationIssue(
                        code="required_story_not_ready",
                        message=f"节点“{node.title}”引用的 Story 尚未满足成书条件。",
                        node_id=node.id,
                        story_id=link.story_id,
                    )
                )

    for node in nodes:
        if node.status == MemoirChapterNodeStatus.EXCLUDED.value:
            continue
        if not node.required and node.status != MemoirChapterNodeStatus.READY.value:
            continue
        node_story_ids = {
            link.story_id for link in links_by_node.get(node.id, [])
        }
        if not _framing_node_has_supported_claim(
            node_type=node.node_type,
            story_ids=node_story_ids,
            shapes=eligible_claim_shapes,
        ):
            issues.append(
                MemoirChapterContractValidationIssue(
                    code="framing_node_has_no_supported_claim",
                    message=(
                        f"结构节点“{node.title}”缺少与其作用匹配且有证据的 Claim。"
                    ),
                    node_id=node.id,
                )
            )

    linked_story_ids = set(stories)
    constraints = contract_constraints(contract)
    excluded_story_ids = set(constraints.exclude_story_ids)
    for node in nodes:
        for link in links_by_node.get(node.id, []):
            story = stories.get(link.story_id)
            if story is None or (
                story.lifecycle_status != MemoirStoryLifecycleStatus.ACTIVE.value
                or story.publication_status
                != MemoirStoryPublicationStatus.ELIGIBLE.value
                or story.verification_status
                in {
                    MemoirStoryVerificationStatus.DISPUTED.value,
                    MemoirStoryVerificationStatus.REJECTED.value,
                }
            ):
                issues.append(
                    MemoirChapterContractValidationIssue(
                        code="linked_story_ineligible",
                        message=f"节点“{node.title}”引用的 Story 已不可用于成书。",
                        node_id=node.id,
                        story_id=link.story_id,
                    )
                )
            if link.story_id in excluded_story_ids:
                issues.append(
                    MemoirChapterContractValidationIssue(
                        code="excluded_story_linked",
                        message=f"节点“{node.title}”引用了契约明确排除的 Story。",
                        node_id=node.id,
                        story_id=link.story_id,
                    )
                )
    for story_id in set(constraints.include_story_ids) - linked_story_ids:
        issues.append(
            MemoirChapterContractValidationIssue(
                code="required_story_not_linked",
                message="契约要求收录的 Story 尚未关联到任何节点。",
                story_id=story_id,
            )
        )
    for story_id in set(constraints.include_story_ids) & linked_story_ids:
        story = stories[story_id]
        if not story_is_ready(story):
            issues.append(
                MemoirChapterContractValidationIssue(
                    code="required_story_not_ready",
                    message="契约要求收录的 Story 尚未满足成书条件。",
                    story_id=story_id,
                )
            )
    return MemoirChapterContractValidationResponse(
        contract_id=contract.id,
        contract_confirmed=(
            contract.status == MemoirChapterContractStatus.CONFIRMED.value
        ),
        required_node_count=len(required_nodes),
        ready_or_excluded_required_node_count=len(ready_required_nodes),
        can_generate=not issues,
        issues=issues,
    )
