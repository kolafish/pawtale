from fastapi import WebSocket, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import SESSION_COOKIE_NAME, is_admin_user, session_user_id
from app.models.users import User
from app.services.ai_access import set_request_ai_model_scope
from app.services.ai_usage_records import set_ai_usage_context


async def authenticate_websocket(websocket: WebSocket, db: AsyncSession) -> User | None:
    session_cookie = websocket.cookies.get(SESSION_COOKIE_NAME)
    if not session_cookie:
        return None
    user_id = await session_user_id(db, session_cookie)
    if not user_id:
        return None
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is not None:
        set_request_ai_model_scope(is_admin=is_admin_user(user))
        set_ai_usage_context(
            user_id=user.id,
            operation=websocket.url.path,
            project_id=websocket.path_params.get("project_id"),
        )
    return user


async def reject_websocket(websocket: WebSocket, error: str) -> None:
    await websocket.accept()
    await websocket.send_json(
        {
            "type": "session.error",
            "state": "failed",
            "error": error,
        }
    )
    await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
