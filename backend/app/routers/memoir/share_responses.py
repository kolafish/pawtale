from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.users import User
from app.schemas.memoir_share_responses import ShareResponseCreate, ShareResponseItem, ShareResponseList
from app.services.memoir import share_responses as service


def no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(dependencies=[Depends(no_store)])


@router.post("/share-responses", response_model=ShareResponseItem)
async def create_response(req: ShareResponseCreate, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return await service.create_response(db, user, req)


@router.get("/share-responses/{response_id}", response_model=ShareResponseItem)
async def read_response(response_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return await service.private_response(db, response_id, user.id)


@router.post("/share-responses/{response_id}/read", response_model=ShareResponseItem)
async def mark_read(response_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return await service.mark_read(db, response_id, user.id)


@router.post("/share-responses/{response_id}/adopt", response_model=ShareResponseItem)
async def adopt_response(response_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return await service.adopt_response(db, response_id, user.id)


@router.get("/projects/{project_id}/share-responses", response_model=ShareResponseList)
async def list_responses(project_id: str, before_id: str | None = None, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return await service.list_responses(db, project_id, user.id, before_id)
