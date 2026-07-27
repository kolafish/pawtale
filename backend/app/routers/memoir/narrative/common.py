from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import is_admin_user
from app.models.projects import Project
from app.models.users import User
from app.services.memoir.access import load_memoir_project


async def authorize_project(
    db: AsyncSession,
    *,
    project_id: str,
    current_user: User,
) -> Project:
    return await load_memoir_project(
        db,
        project_id,
        current_user.id,
        allow_admin=is_admin_user(current_user),
    )


def decision_error(exc: ValueError) -> HTTPException:
    message = str(exc)
    status_code = 404 if "does not belong" in message else 409
    detail = {
        "chapter does not belong to the project": "章节不存在",
        "confirm the book plan before assembling the book output": "请先确认全书章节目录",
        "book plan has no chapters": "请先添加至少一个章节",
        "book output requires a current book review": "请先完成当前版本的全书检查",
        "book output requires resolving book review decisions": "请先处理全书检查中需要确认的问题",
    }.get(message, message)
    if message.startswith("book output requires confirmed chapter drafts:"):
        titles = message.split(":", 1)[1].strip()
        detail = f"请先在章节工作台整理并确认：{titles}"
    return HTTPException(status_code=status_code, detail=detail)
