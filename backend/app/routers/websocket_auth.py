from fastapi import WebSocket, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import SESSION_COOKIE_NAME, decode_session_token
from app.models.users import User


async def authenticate_websocket(websocket: WebSocket, db: AsyncSession) -> User | None:
    session_cookie = websocket.cookies.get(SESSION_COOKIE_NAME)
    if not session_cookie:
        return None
    user_id = decode_session_token(session_cookie)
    if not user_id:
        return None
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


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
