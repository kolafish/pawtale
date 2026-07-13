from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.users import User
from app.schemas.memoir_narrative import (
    MemoirChapterContractDraftRequest,
    MemoirChapterContractResponse,
    MemoirChapterContractSuggestionRequest,
    MemoirChapterContractValidationResponse,
    MemoirChapterNodeCreateRequest,
    MemoirChapterNodeReorderRequest,
    MemoirChapterNodeStoryAttachRequest,
    MemoirChapterNodeStoryMoveRequest,
    MemoirChapterNodeUpdateRequest,
    MemoirChapterStructureMergeRequest,
    MemoirChapterStructureMutationResponse,
    MemoirChapterStructureResponse,
    MemoirChapterStructureSplitRequest,
    MemoirNarrativeDecisionRequest,
)
from app.services.memoir.narrative import (
    chapter_contracts,
    chapter_nodes,
    chapter_topology,
)

from .common import authorize_project, decision_error


router = APIRouter()


async def _commit_contract_response(
    db: AsyncSession,
    *,
    contract,
) -> MemoirChapterContractResponse:
    await db.commit()
    return await chapter_contracts.contract_response(db, contract=contract)


async def _raise_mutation_error(
    db: AsyncSession,
    exc: ValueError | IntegrityError,
) -> NoReturn:
    await db.rollback()
    if isinstance(exc, ValueError):
        raise decision_error(exc) from exc
    raise HTTPException(
        status_code=409,
        detail="chapter structure changed concurrently; reload and retry",
    ) from exc


@router.get(
    "/projects/{project_id}/chapters/{chapter_id}/narrative-structure",
    response_model=MemoirChapterStructureResponse,
)
async def get_memoir_chapter_structure(
    project_id: str,
    chapter_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        return await chapter_contracts.chapter_structure_response(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
        )
    except ValueError as exc:
        raise decision_error(exc) from exc


@router.post(
    "/projects/{project_id}/chapters/{chapter_id}/contract/suggest",
    response_model=MemoirChapterContractResponse,
)
async def suggest_memoir_chapter_contract(
    project_id: str,
    chapter_id: str,
    req: MemoirChapterContractSuggestionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        contract = await chapter_contracts.create_legacy_contract_suggestion(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
            idempotency_key=req.idempotency_key,
        )
        return await _commit_contract_response(db, contract=contract)
    except (ValueError, IntegrityError) as exc:
        await _raise_mutation_error(db, exc)


@router.post(
    "/projects/{project_id}/chapters/{chapter_id}/contract/revisions",
    response_model=MemoirChapterContractResponse,
)
async def create_memoir_chapter_contract_revision(
    project_id: str,
    chapter_id: str,
    req: MemoirChapterContractDraftRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        contract = await chapter_contracts.create_contract_revision(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
            request=req,
            actor_user_id=current_user.id,
        )
        return await _commit_contract_response(db, contract=contract)
    except (ValueError, IntegrityError) as exc:
        await _raise_mutation_error(db, exc)


@router.get(
    "/projects/{project_id}/chapter-contracts/{contract_id}",
    response_model=MemoirChapterContractResponse,
)
async def get_memoir_chapter_contract(
    project_id: str,
    contract_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        contract = await chapter_contracts.load_contract(
            db,
            project_id=project_id,
            contract_id=contract_id,
        )
        return await chapter_contracts.contract_response(db, contract=contract)
    except ValueError as exc:
        raise decision_error(exc) from exc


@router.post(
    "/projects/{project_id}/chapter-contracts/{contract_id}/confirm",
    response_model=MemoirChapterContractResponse,
)
async def confirm_memoir_chapter_contract(
    project_id: str,
    contract_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        contract = await chapter_contracts.confirm_contract(
            db,
            project_id=project_id,
            contract_id=contract_id,
            actor_user_id=current_user.id,
        )
        return await _commit_contract_response(db, contract=contract)
    except (ValueError, IntegrityError) as exc:
        await _raise_mutation_error(db, exc)


@router.get(
    "/projects/{project_id}/chapter-contracts/{contract_id}/generation-validation",
    response_model=MemoirChapterContractValidationResponse,
)
async def validate_memoir_chapter_contract_for_generation(
    project_id: str,
    contract_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        return await chapter_contracts.validate_contract_for_generation(
            db,
            project_id=project_id,
            contract_id=contract_id,
        )
    except ValueError as exc:
        raise decision_error(exc) from exc


@router.post(
    "/projects/{project_id}/chapter-contracts/{contract_id}/nodes",
    response_model=MemoirChapterContractResponse,
)
async def create_memoir_chapter_node(
    project_id: str,
    contract_id: str,
    req: MemoirChapterNodeCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        await chapter_nodes.create_node(
            db,
            project_id=project_id,
            contract_id=contract_id,
            request=req,
            actor_user_id=current_user.id,
        )
        contract = await chapter_contracts.load_contract(
            db,
            project_id=project_id,
            contract_id=contract_id,
        )
        return await _commit_contract_response(db, contract=contract)
    except (ValueError, IntegrityError) as exc:
        await _raise_mutation_error(db, exc)


@router.patch(
    "/projects/{project_id}/chapter-contracts/{contract_id}/nodes/{node_id}",
    response_model=MemoirChapterContractResponse,
)
async def update_memoir_chapter_node(
    project_id: str,
    contract_id: str,
    node_id: str,
    req: MemoirChapterNodeUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        await chapter_nodes.update_node(
            db,
            project_id=project_id,
            contract_id=contract_id,
            node_id=node_id,
            request=req,
            actor_user_id=current_user.id,
        )
        contract = await chapter_contracts.load_contract(
            db,
            project_id=project_id,
            contract_id=contract_id,
        )
        return await _commit_contract_response(db, contract=contract)
    except (ValueError, IntegrityError) as exc:
        await _raise_mutation_error(db, exc)


@router.post(
    "/projects/{project_id}/chapter-contracts/{contract_id}/nodes/reorder",
    response_model=MemoirChapterContractResponse,
)
async def reorder_memoir_chapter_nodes(
    project_id: str,
    contract_id: str,
    req: MemoirChapterNodeReorderRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        await chapter_nodes.reorder_nodes(
            db,
            project_id=project_id,
            contract_id=contract_id,
            request=req,
            actor_user_id=current_user.id,
        )
        contract = await chapter_contracts.load_contract(
            db,
            project_id=project_id,
            contract_id=contract_id,
        )
        return await _commit_contract_response(db, contract=contract)
    except (ValueError, IntegrityError) as exc:
        await _raise_mutation_error(db, exc)


@router.post(
    "/projects/{project_id}/chapter-contracts/{contract_id}/nodes/{node_id}/stories",
    response_model=MemoirChapterContractResponse,
)
async def attach_story_to_memoir_chapter_node(
    project_id: str,
    contract_id: str,
    node_id: str,
    req: MemoirChapterNodeStoryAttachRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        await chapter_nodes.attach_story(
            db,
            project_id=project_id,
            contract_id=contract_id,
            node_id=node_id,
            request=req,
            actor_user_id=current_user.id,
        )
        contract = await chapter_contracts.load_contract(
            db,
            project_id=project_id,
            contract_id=contract_id,
        )
        return await _commit_contract_response(db, contract=contract)
    except (ValueError, IntegrityError) as exc:
        await _raise_mutation_error(db, exc)


@router.delete(
    "/projects/{project_id}/chapter-contracts/{contract_id}/nodes/{node_id}/stories/{story_id}",
    response_model=MemoirChapterContractResponse,
)
async def detach_story_from_memoir_chapter_node(
    project_id: str,
    contract_id: str,
    node_id: str,
    story_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        await chapter_nodes.detach_story(
            db,
            project_id=project_id,
            contract_id=contract_id,
            node_id=node_id,
            story_id=story_id,
            actor_user_id=current_user.id,
        )
        contract = await chapter_contracts.load_contract(
            db,
            project_id=project_id,
            contract_id=contract_id,
        )
        return await _commit_contract_response(db, contract=contract)
    except (ValueError, IntegrityError) as exc:
        await _raise_mutation_error(db, exc)


@router.post(
    "/projects/{project_id}/chapter-contracts/{contract_id}/stories/{story_id}/move",
    response_model=MemoirChapterContractResponse,
)
async def move_story_between_memoir_chapter_nodes(
    project_id: str,
    contract_id: str,
    story_id: str,
    req: MemoirChapterNodeStoryMoveRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        await chapter_nodes.move_story(
            db,
            project_id=project_id,
            contract_id=contract_id,
            story_id=story_id,
            request=req,
            actor_user_id=current_user.id,
        )
        contract = await chapter_contracts.load_contract(
            db,
            project_id=project_id,
            contract_id=contract_id,
        )
        return await _commit_contract_response(db, contract=contract)
    except (ValueError, IntegrityError) as exc:
        await _raise_mutation_error(db, exc)


@router.post(
    "/projects/{project_id}/chapter-contracts/{contract_id}/stories/{story_id}/exclude",
    response_model=MemoirChapterContractResponse,
)
async def exclude_story_from_memoir_chapter_contract(
    project_id: str,
    contract_id: str,
    story_id: str,
    req: MemoirNarrativeDecisionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        contract = await chapter_nodes.exclude_story_from_contract(
            db,
            project_id=project_id,
            contract_id=contract_id,
            story_id=story_id,
            request=req,
            actor_user_id=current_user.id,
        )
        return await _commit_contract_response(db, contract=contract)
    except (ValueError, IntegrityError) as exc:
        await _raise_mutation_error(db, exc)


async def _structure_mutation_response(
    db: AsyncSession,
    *,
    source,
    target,
) -> MemoirChapterStructureMutationResponse:
    await db.commit()
    return MemoirChapterStructureMutationResponse(
        source=await chapter_contracts.contract_response(db, contract=source),
        target=await chapter_contracts.contract_response(db, contract=target),
    )


@router.post(
    "/projects/{project_id}/chapter-structures/split",
    response_model=MemoirChapterStructureMutationResponse,
)
async def split_memoir_chapter_structure(
    project_id: str,
    req: MemoirChapterStructureSplitRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        source, target = await chapter_topology.split_structure(
            db,
            project_id=project_id,
            request=req,
            actor_user_id=current_user.id,
        )
        return await _structure_mutation_response(
            db,
            source=source,
            target=target,
        )
    except (ValueError, IntegrityError) as exc:
        await _raise_mutation_error(db, exc)


@router.post(
    "/projects/{project_id}/chapter-structures/merge",
    response_model=MemoirChapterStructureMutationResponse,
)
async def merge_memoir_chapter_structures(
    project_id: str,
    req: MemoirChapterStructureMergeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await authorize_project(db, project_id=project_id, current_user=current_user)
    try:
        source, target = await chapter_topology.merge_structures(
            db,
            project_id=project_id,
            request=req,
            actor_user_id=current_user.id,
        )
        return await _structure_mutation_response(
            db,
            source=source,
            target=target,
        )
    except (ValueError, IntegrityError) as exc:
        await _raise_mutation_error(db, exc)
