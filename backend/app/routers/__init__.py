from app.routers.audit import router as audit_router
from app.routers.auth import router as auth_router
from app.routers.github import router as github_router
from app.routers.memoir import router as memoir_router
from app.routers.projects import router as projects_router

__all__ = [
    "auth_router",
    "audit_router",
    "github_router",
    "memoir_router",
    "projects_router",
]
