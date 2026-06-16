from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.config import get_settings
from app.database import get_db
from app.models.users import User
from app.services.projects import git_sync


router = APIRouter(prefix="/api/github", tags=["github"])


@router.get("/setup")
async def github_app_setup_callback(
    installation_id: int = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await git_sync.record_installation(db, current_user.id, installation_id)
    redirect_url = get_settings().github_app_setup_redirect_url
    separator = "&" if "?" in redirect_url else "?"
    return RedirectResponse(f"{redirect_url}{separator}github_installation=connected")
