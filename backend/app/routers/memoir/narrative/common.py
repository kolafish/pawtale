from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import is_admin_user
from app.models.users import User
from app.services.memoir.access import load_memoir_project


async def authorize_project(
    db: AsyncSession,
    *,
    project_id: str,
    current_user: User,
) -> None:
    await load_memoir_project(
        db,
        project_id,
        current_user.id,
        allow_admin=is_admin_user(current_user),
    )


def decision_error(exc: ValueError) -> HTTPException:
    message = str(exc)
    status_code = 404 if "does not belong" in message else 409
    return HTTPException(status_code=status_code, detail=message)
