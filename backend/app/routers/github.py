from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.users import User
from app.schemas.projects import GitHubSetupUrlResponse
from app.services.projects import git_sync


router = APIRouter(prefix="/api/github", tags=["github"])


@router.get("/setup")
async def github_app_setup_callback(
    installation_id: int | None = Query(None, gt=0),
    code: str | None = Query(None, min_length=1),
    state: str = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    url = await git_sync.handle_setup_callback(
        db, current_user.id, state=state, installation_id=installation_id, code=code,
    )
    return RedirectResponse(url)


@router.post("/setup-url", response_model=GitHubSetupUrlResponse)
async def create_github_app_setup_url(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await git_sync.create_setup_url(db, current_user.id)
