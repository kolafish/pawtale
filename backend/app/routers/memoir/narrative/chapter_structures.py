from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.users import User
from app.schemas.memoir_narrative import (
    MemoirChapterContractResponse,
    MemoirChapterContractSuggestionRequest,
)
from app.services.memoir.narrative import chapter_contracts

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
        detail="chapter direction changed concurrently; reload and retry",
    ) from exc


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
