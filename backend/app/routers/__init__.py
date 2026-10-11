from app.routers.audit import router as audit_router
from app.routers.auth import router as auth_router
from app.routers.billing import router as billing_router
from app.routers.github import router as github_router
from app.routers.memoir import router as memoir_router
from app.routers.projects import router as projects_router
from app.routers.wechat_message_push import router as wechat_message_push_router

__all__ = [
    "auth_router",
    "audit_router",
    "billing_router",
    "github_router",
    "memoir_router",
    "projects_router",
    "wechat_message_push_router",
]
