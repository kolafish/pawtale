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
    detail = {
        "confirm the chapter direction before assembling the chapter": "请先确认本章整理方向",
        "chapter has no structured draft blocks to assemble": "请先整理本章正文段落",
        "confirm every included draft block before assembling the chapter": "请先确认本章所有正文段落",
        "refresh stale draft blocks before assembling the chapter": "本章素材或事实已变化，请重新整理标记过期的段落",
        "chapter draft snapshot no longer matches current structured blocks": "本章正文在组装后发生变化，请重新确认",
        "confirm the book plan before assembling the book output": "请先确认全书章节目录",
        "book plan has no chapters": "请先添加至少一个章节",
    }.get(message, message)
    if message.startswith("ready chapter nodes are missing draft blocks:"):
        detail = "本章还有结构段落未整理，请先完成所有已就绪的段落"
    elif message.startswith(
        "book output requires confirmed structured chapter snapshots:"
    ):
        titles = message.split(":", 1)[1].strip()
        detail = f"请先在章节工作台整理并确认：{titles}"
    return HTTPException(status_code=status_code, detail=detail)
